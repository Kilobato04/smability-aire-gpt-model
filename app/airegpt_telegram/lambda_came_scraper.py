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
        
        # Buscamos el ID dinámico más reciente
        for art in articulos:
            enlaces = art.find_all('a')
            for a_tag in enlaces:
                if 'href' in a_tag.attrs:
                    # --- FIX: LIMPIEZA DE URL BASURA DEL GOBIERNO ---
                    href_limpio = a_tag['href'].replace('\\"', '').replace('"', '').replace('\\', '').strip()
                    # ------------------------------------------------
                    
                    match = re.search(r'-(\d+)(?:\?|$)', href_limpio)
                    if match:
                        post_id = int(match.group(1))
                        if post_id > max_id:
                            max_id = post_id
                            # Aseguramos que se una bien con la base
                            if href_limpio.startswith('/'):
                                best_link = BASE_URL + href_limpio
                            else:
                                best_link = BASE_URL + '/' + href_limpio
        
        if not best_link: return None, "No hay enlaces."
        
        print(f"   ✅ ID más reciente: {max_id} | Link: {best_link}")
        
        # Le subimos a 15s el timeout por si la página del gobierno está lenta
        r_art = requests.get(best_link, timeout=15) 
        soup_art = BeautifulSoup(r_art.text, 'html.parser')
        
        # --- EXTRACCIÓN SÚPER SEGURA (A prueba de caídas) ---
        # 1. Título
        meta_title = soup_art.find('meta', property='og:title')
        if meta_title and meta_title.get('content'):
            titulo_real = meta_title['content']
        else:
            h1_tag = soup_art.find('h1')
            titulo_real = h1_tag.text.strip() if h1_tag else "Comunicado Oficial CAMe"
        
        # 2. Texto
        articulo_html = soup_art.find('article')
        if articulo_html:
            parrafos = articulo_html.find_all('p')
        else:
            parrafos = soup_art.find_all('p')
            
        texto_limpio = " ".join([p.text.strip() for p in parrafos if len(p.text.strip()) > 15])[:6000]
        
        return titulo_real, texto_limpio, best_link
    
    except Exception as e:
        print(f"❌ [CRITICAL ERROR] Falló la extracción en la web: {e}")
        return None, f"Error web: {e}", None

def analizar_contingencia_ia(titulo, texto_articulo):
    print("🤖 2. Procesando cruce de datos con IA...")
    
    prompt_sistema = """Eres el Analista Legal en Jefe de la CAMe. 
    Lee el TÍTULO y el TEXTO del comunicado oficial y extrae la verdad legal en formato JSON.
    
    REGLAS INFALIBLES PARA EL JSON:
    1. "razonamiento": Escribe paso a paso tu lógica. ¿El título dice SE SUSPENDE o MANTIENE?
    2. "estatus": Si el título o el primer párrafo dice "SUSPENDE" o "LEVANTA", pon "SUSPENDE" (ignora la palabra "mantiene" si hablan del clima). Si dice "MANTIENE", pon "MANTIENE".
    3. "fase": Si el estatus es "SUSPENDE", pon "None". Si es "MANTIENE", pon "Fase I" o "Fase II".
    4. "resumen_hnc": Si el estatus es "SUSPENDE", pon "Circulación normal". Si es "MANTIENE", resume qué autos no circulan.
    5. "fecha_hora": Extrae la fecha y hora de emisión (Ej: "17 de febrero, 18:00 horas")."""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={ "type": "json_object" },
            messages=[
                {"role": "system", "content": prompt_sistema}, 
                {"role": "user", "content": f"TÍTULO: {titulo}\n\nTEXTO:\n{texto_articulo}"}
            ],
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e: 
        print(f"❌ [IA ERROR] Falló OpenAI: {e}")
        return {"error": str(e)}

def lambda_handler(event, context):
    print("🚀 Iniciando CAMe Scraper...")
    titulo, texto, link_oficial = obtener_contexto_completo()
    
    if not titulo: 
        print(f"🚨 Abortando Lambda por error: {texto}")
        return {"statusCode": 500, "body": "Fallo extracción"}
        
    resultado_ia = analizar_contingencia_ia(titulo, texto)
    if "error" in resultado_ia: 
        print("🚨 Abortando Lambda por error de IA.")
        return {"statusCode": 500, "body": "Fallo IA"}
        
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
            
        # 2. Despertar al Chatbot
        if fase_broadcast == "SUSPENDIDA":
            payload = {
                "action": "BROADCAST_CONTINGENCY",
                "data": {"phase": "SUSPENDIDA"},
                "oficial_link":
            }
        else:
            payload = {
                "action": "BROADCAST_CONTINGENCY",
                "data": {
                    "phase": fase_broadcast,
                    "oficial_link": link_oficial,
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
