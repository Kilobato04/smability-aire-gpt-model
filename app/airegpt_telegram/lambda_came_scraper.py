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

HOME_URL = "https://www.gob.mx/comisionambiental"
PRENSA_URL = "https://www.gob.mx/comisionambiental/es/archivo/prensa"
BASE_URL = "https://www.gob.mx"

def obtener_contexto_completo():
    print("🌍 1. Consultando fuentes oficiales de la CAMe...")
    try:
        r_home = requests.get(HOME_URL, timeout=10)
        soup_home = BeautifulSoup(r_home.text, 'html.parser')
        home_text_clean = " ".join(soup_home.text.split())[:3000] 
        
        r_prensa = requests.get(PRENSA_URL, timeout=10)
        soup_prensa = BeautifulSoup(r_prensa.text, 'html.parser')
        
        articulos = soup_prensa.find_all('article', limit=10)
        max_id = -1
        best_link, best_title = None, None
        
        for art in articulos:
            a_tag = art.find('a')
            if a_tag and 'href' in a_tag.attrs:
                match = re.search(r'-(\d+)(?:\?|$)', a_tag['href'])
                if match:
                    post_id = int(match.group(1))
                    if post_id > max_id:
                        max_id, best_link, best_title = post_id, BASE_URL + a_tag['href'], a_tag.text.strip()
        
        if not best_link: return None, "No hay enlaces."
        
        print(f"   ✅ ID más reciente: {max_id}")
        r_art = requests.get(best_link, timeout=10)
        soup_art = BeautifulSoup(r_art.text, 'html.parser')
        art_text_clean = " ".join(soup_art.text.split())[:6000]
        
        texto_final = f"=== PORTADA PRINCIPAL ===\n{home_text_clean}\n\n=== COMUNICADO DETALLE ===\n{art_text_clean}"
        return best_title, texto_final
    except Exception as e:
        return None, f"Error web: {e}"

def analizar_contingencia_ia(titulo, texto_combinado):
    print("🤖 2. Procesando cruce de datos con IA...")
    prompt_sistema = """Eres un analista legal de la CAMe. Lee el texto y devuelve un JSON.
    REGLAS:
    1. "estatus": "ACTIVA", "MANTIENE", "SUSPENDE", o "SIN_CONTINGENCIA".
    2. "fase": "Fase I", "Fase II", o "None".
    3. "resumen_hnc": Resume EXACTAMENTE quién NO circula (Ej: "No circulan hologramas 2, 1 impar y 0/00 rojo"). Si se SUSPENDE, pon "Circulación normal".
    4. "fecha_hora": Extrae la FECHA Y HORA REAL (Ej: "17 de febrero, 15:00 horas") basándote en la PORTADA PRINCIPAL y el título. Ignora fechas pasadas del texto."""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={ "type": "json_object" },
            messages=[{"role": "system", "content": prompt_sistema}, {"role": "user", "content": f"TÍTULO: {titulo}\n\nTEXTO: {texto_combinado}"}],
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
    
    if fecha_nueva != fecha_vieja and fecha_nueva != "":
        print(f"🚨 ¡NUEVO BOLETÍN DETECTADO! Actualizando BD... ({fecha_vieja} -> {fecha_nueva})")
        
        fase_detectada = resultado_ia.get('fase', 'None')
        estatus = resultado_ia.get('estatus', 'MANTIENE')
        fase_broadcast = "SUSPENDIDA" if estatus == "SUSPENDE" else fase_detectada
            
        # 1. Guardar la verdad oficial en la BD
        table.update_item(
            Key={'user_id': 'SYSTEM_STATE'},
            UpdateExpression="SET came_oficial = :c, last_contingency_phase = :p, updated_at = :t",
            ExpressionAttributeValues={':c': resultado_ia, ':p': fase_broadcast, ':t': datetime.now().isoformat()}
        )
            
        # 2. Despertar al Chatbot para el Broadcast
        payload = {
            "action": "BROADCAST_CONTINGENCY",
            "data": {
                "phase": fase_broadcast,
                "alert_type": "Comunicado CAMe",
                "trigger_station_name": "Portal Oficial (CAMe)",
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
