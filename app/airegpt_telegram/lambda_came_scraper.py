import json
import os
import requests
import re
import boto3
from bs4 import BeautifulSoup
from datetime import datetime
from openai import OpenAI

# --- CONFIGURACIÓN ---
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'SmabilityUsers')
BOT_LAMBDA_NAME = os.environ.get('BOT_LAMBDA_NAME', 'Smability-Chatbot')

client = OpenAI(api_key=OPENAI_API_KEY, timeout=20.0)
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)
lambda_client = boto3.client('lambda')

PRENSA_URL = "https://www.gob.mx/comisionambiental/es/archivo/prensa"
BASE_URL = "https://www.gob.mx"

def obtener_contexto_completo():
    print("🌍 1. Consultando archivo de prensa de la CAMe...")
    try:
        r_prensa = requests.get(PRENSA_URL, timeout=10)
        soup_prensa = BeautifulSoup(r_prensa.text, 'html.parser')
        
        articulos = soup_prensa.find_all('article', limit=10)
        max_id = -1
        best_link = None
        
        # Buscamos en todos los enlaces del artículo para no fallar
        for art in articulos:
            enlaces = art.find_all('a')
            for a_tag in enlaces:
                if 'href' in a_tag.attrs:
                    match = re.search(r'-(\d+)(?:\?|$)', a_tag['href'])
                    if match:
                        post_id = int(match.group(1))
                        if post_id > max_id:
                            max_id = post_id
                            best_link = BASE_URL + a_tag['href']
        
        if not best_link: return None, "No hay enlaces."
        
        print(f"   ✅ ID más reciente: {max_id}")
        r_art = requests.get(best_link, timeout=10)
        soup_art = BeautifulSoup(r_art.text, 'html.parser')
        
        # --- FIX: EXTRAER SOLO EL CONTENIDO REAL (Bypass de Banners) ---
        # 1. Buscamos el título principal real de la noticia
        h1_tag = soup_art.find('h1')
        titulo_real = h1_tag.text.strip() if h1_tag else "Comunicado Oficial"
        
        # 2. Extraemos ÚNICAMENTE los párrafos de texto, ignorando menús y alertas globales
        parrafos = soup_art.find_all('p')
        texto_limpio = " ".join([p.text.strip() for p in parrafos if len(p.text.strip()) > 15])[:6000]
        # -------------------------------------------------------------
        
        return titulo_real, texto_limpio
    except Exception as e:
        return None, f"Error web: {e}"

def analizar_contingencia_ia(titulo, texto_articulo):
    print("🤖 2. Procesando cruce de datos con IA...")
    
    prompt_sistema = """Eres el Analista Legal en Jefe de la CAMe. 
    Tu única tarea es leer el comunicado oficial y extraer la verdad legal en formato JSON.
    
    REGLAS INFALIBLES PARA EL JSON (DEBES SEGUIR ESTE ORDEN):
    1. "razonamiento": Extrae la frase exacta del TÍTULO o PRIMER PÁRRAFO que dicta la orden principal. (Ej: "El título dice explícitamente SE SUSPENDE").
    2. "estatus": Basado en tu razonamiento, si la orden incluye "SUSPENDE" o "LEVANTA", el valor AQUÍ DEBE SER "SUSPENDE". Si dice "MANTIENE" o "CONTINÚA", pon "MANTIENE".
    3. "fase": Si el estatus es "SUSPENDE", pon obligatoriamente "None". Si es "MANTIENE", pon "Fase I" o la fase correspondiente.
    4. "resumen_hnc": Si el estatus es "SUSPENDE", pon estrictamente "Circulación normal". Si es "MANTIENE", resume qué autos no circulan.
    5. "fecha_hora": Extrae la fecha y hora de emisión (Ej: "17 de febrero, 18:00 horas").
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={ "type": "json_object" },
            messages=[
                {"role": "system", "content": prompt_sistema}, 
                {"role": "user", "content": f"Devuelve el JSON estructurado basado en esta información:\n\nTÍTULO: {titulo}\n\nTEXTO:\n{texto_articulo}"}
            ],
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e: return {"error": str(e)}

def lambda_handler(event, context):
    print("🚀 Iniciando CAMe Scraper...")
    titulo, texto = obtener_contexto_completo()
    if not titulo: return {"statusCode": 500, "body": "Fallo extracción"}
        
    resultado_ia = analizar_contingencia_ia(titulo, texto)
    if "error" in resultado_ia: return {"statusCode": 500, "body": "Fallo IA"}
        
    print(f"✅ JSON Extraído: {json.dumps(resultado_ia, ensure_ascii=False)}")
    
    # --- COMPARAR CONTRA LA BASE DE DATOS ---
    db_item = table.get_item(Key={'user_id': 'SYSTEM_STATE'}).get('Item', {})
    estado_anterior = db_item.get('came_oficial', {})
    
    fecha_nueva = resultado_ia.get('fecha_hora', '')
    fecha_vieja = estado_anterior.get('fecha_hora', '')
    
    # Disparamos si la fecha cambió
    if fecha_nueva != fecha_vieja and fecha_nueva != "":
        print(f"🚨 ¡NUEVO BOLETÍN DETECTADO! Actualizando BD... ({fecha_vieja} -> {fecha_nueva})")
        
        fase_detectada = resultado_ia.get('fase', 'None')
        estatus = resultado_ia.get('estatus', 'MANTIENE')
        
        # Lógica de Suspensión Segura
        if estatus in ["SUSPENDE", "SIN_CONTINGENCIA"]:
            fase_broadcast = "SUSPENDIDA"
            fase_db = "None" 
        else:
            fase_broadcast = fase_detectada
            fase_db = fase_detectada
            
        # 1. Guardar la verdad oficial en la BD
        table.update_item(
            Key={'user_id': 'SYSTEM_STATE'},
            UpdateExpression="SET came_oficial = :c, last_contingency_phase = :p, updated_at = :t",
            ExpressionAttributeValues={':c': resultado_ia, ':p': fase_db, ':t': datetime.now().isoformat()}
        )
            
        # 2. Despertar al Chatbot (Con el formato que espera)
        if fase_broadcast == "SUSPENDIDA":
            payload = {
                "action": "BROADCAST_CONTINGENCY",
                "data": {"phase": "SUSPENDIDA"}
            }
        else:
            payload = {
                "action": "BROADCAST_CONTINGENCY",
                "data": {
                    "phase": fase_broadcast,
                    "alert_type": "Decreto Legal (CAMe)",
                    "trigger_station_name": "Portal CAMe",
                    "recommendations": {
                        "categories": [{"name": "RESTRICCIONES VEHICULARES", "items": [resultado_ia.get('resumen_hnc', 'Verificar oficial')]}]
                    }
                }
            }
        
        lambda_client.invoke(FunctionName=BOT_LAMBDA_NAME, InvocationType='Event', Payload=json.dumps(payload))
        print("📢 Señal de Broadcast enviada al Chatbot.")
            
    else:
        print("💤 Sin boletines nuevos. A dormir.")
        
    return {"statusCode": 200, "body": "OK"}
