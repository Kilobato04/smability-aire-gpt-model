import json
import os
import time
import requests
import boto3
from datetime import datetime, timedelta
from decimal import Decimal
import lambda_api_light
import cards

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
DYNAMODB_TABLE = 'SmabilityUsers'
# URL MAESTRA PROPORCIONADA
MASTER_API_URL = "https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference"

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

def get_cdmx_time():
    return datetime.utcnow() - timedelta(hours=6)

def send_telegram_push(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        time.sleep(0.05)
    except Exception as e: print(f"Error push: {e}")

def get_location_air_data(lat, lon):
    try:
        mock = {'queryStringParameters': {'lat': str(lat), 'lon': str(lon)}}
        res = lambda_api_light.lambda_handler(mock, None)
        if res['statusCode'] == 200: return json.loads(res['body'])
    except: pass
    return None

def get_maps_url(lat, lon):
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

def clean_key(text):
    return "".join(e for e in text if e.isalnum()).lower()

# --- LÓGICA DE CONTINGENCIA V52 (PARSER JSON) ---
def check_master_api_contingency():
    print(f"🔍 [SCAN] Consultando API Maestra: {MASTER_API_URL}")
    try:
        response = requests.get(MASTER_API_URL, timeout=5)
        if response.status_code == 200:
            data = response.json()
            
            # Buscamos el objeto 'contingency'
            # Estructura esperada: "contingency": { "alert_type": "ozone", "phase": "Fase I", ... }
            cont_obj = data.get('contingency')
            
            # Validación: Si existe y es un diccionario (no null, no false)
            if cont_obj and isinstance(cont_obj, dict):
                
                # 1. Extraer Fase
                phase = cont_obj.get('phase', 'Fase I')
                
                # 2. Extraer y Traducir Contaminante
                raw_type = cont_obj.get('alert_type', 'ozone').lower()
                pollutant = "Ozono" if "ozone" in raw_type else "Partículas"
                
                # 3. Extraer Valor (Opcional, para enriquecer)
                try:
                    val_data = cont_obj.get('value', {})
                    val_num = val_data.get('value')
                    val_unit = val_data.get('unit', '')
                    if val_num:
                        pollutant += f" ({val_num} {val_unit})"
                except: pass

                print(f"🚨 [CONTINGENCIA ACTIVA] {phase} por {pollutant}")
                return True, phase, pollutant
            
            else:
                print("✅ [NORMAL] No hay objeto de contingencia activo.")
                
    except Exception as e: 
        print(f"⚠️ Error API Maestra: {e}")
        
    return False, "", ""

def process_user(user, current_hour_str, contingency_data):
    user_id = user['user_id']
    first_name = user.get('first_name', 'Usuario')
    alerts = user.get('alerts', {})
    locations = user.get('locations', {})
    health_map = user.get('health_profile', {})
    
    # EXTRAER SALUD
    health_str = None
    if isinstance(health_map, dict) and health_map:
        conditions = [v.get('condition', 'Padecimiento') for v in health_map.values() if isinstance(v, dict)]
        if conditions: health_str = ", ".join(conditions)

    # 1. CONTINGENCIA (Prioridad)
    is_cont, phase, pol = contingency_data
    if is_cont:
        last_sent = user.get('last_contingency_date', '')
        today = get_cdmx_time().strftime("%Y-%m-%d")
        
        # Solo enviar una vez al día por usuario
        if last_sent != today:
            print(f"🚨 [PUSH CONTINGENCIA] Enviando a {user_id}")
            card = cards.CARD_CONTINGENCY.format(
                user_name=first_name,
                report_time=f"{current_hour_str.split(':')[0]}:20",
                phase=phase,
                pollutant=pol,
                forecast_msg="Revisa recomendaciones oficiales.", # Mensaje fijo para contingencia
                footer=cards.BOT_FOOTER
            )
            send_telegram_push(user_id, card)
            
            # Actualizar flag para no repetir hoy
            table.update_item(
                Key={'user_id': user_id}, 
                UpdateExpression="SET last_contingency_date = :d", 
                ExpressionAttributeValues={':d': today}
            )
            return # Si enviamos contingencia, quizás saltar otras alertas para no saturar

    # 2. RECORDATORIOS
    schedule = alerts.get('schedule', {})
    for loc_name, config in schedule.items():
        user_time = config.get('time', '').split(':')[0]
        curr_time = current_hour_str.split(':')[0]
        
        if user_time == curr_time:
            loc_data = locations.get(loc_name)
            if loc_data:
                data = get_location_air_data(loc_data['lat'], loc_data['lon'])
                if data:
                    qa = data.get('calidad_aire', {})
                    cat = qa.get('category', 'Regular').title()
                    custom_rec = cards.get_health_advice(cat, health_str)
                    info = cards.IAS_INFO.get(cat, cards.IAS_INFO['Regular'])
                    
                    card = cards.CARD_REMINDER.format(
                        user_name=first_name,
                        location_name=loc_data.get('display_name', loc_name),
                        maps_url=get_maps_url(loc_data['lat'], loc_data['lon']),
                        report_time=f"{curr_time}:20",
                        region="ZMVM",
                        ias_value=qa.get('ias', 0),
                        risk_category=cat,
                        risk_circle=info['emoji'],
                        natural_message=info['msg'],
                        forecast_msg=qa.get('forecast_24h', 'Estable'),
                        pollutant=qa.get('dominant_pollutant', 'N/A'),
                        health_recommendation=custom_rec,
                        footer=cards.BOT_FOOTER
                    )
                    send_telegram_push(user_id, card)

    # 3. ALERTAS UMBRAL
    thresholds = alerts.get('threshold', {})
    for loc_name, config in thresholds.items():
        if not config.get('active', False): continue
        umbral = int(config.get('umbral', 100))
        if umbral < 100: umbral = 100
        
        loc_data = locations.get(loc_name)
        if loc_data:
            data = get_location_air_data(loc_data['lat'], loc_data['lon'])
            if data:
                cur_ias = data.get('calidad_aire', {}).get('ias', 0)
                
                if cur_ias > umbral:
                    count = int(config.get('consecutive_sent', 0))
                    if count < 3:
                        qa = data['calidad_aire']
                        cat = qa.get('category', 'Mala').title()
                        custom_rec = cards.get_health_advice(cat, health_str)
                        info = cards.IAS_INFO.get(cat, cards.IAS_INFO['Mala'])
                        
                        card = cards.CARD_ALERT_IAS.format(
                            user_name=first_name,
                            location_name=loc_data.get('display_name', loc_name),
                            maps_url=get_maps_url(loc_data['lat'], loc_data['lon']),
                            report_time=f"{current_hour_str.split(':')[0]}:20",
                            region="ZMVM",
                            risk_category=cat,
                            risk_circle=info['emoji'],
                            ias_value=cur_ias,
                            forecast_msg=qa.get('forecast_24h', 'Al alza'),
                            natural_message=info['msg'],
                            threshold=umbral,
                            pollutant=qa.get('dominant_pollutant', 'N/A'),
                            health_recommendation=custom_rec,
                            footer=cards.BOT_FOOTER
                        )
                        send_telegram_push(user_id, card)
                        table.update_item(Key={'user_id': user_id}, UpdateExpression=f"SET alerts.threshold.#{loc_name}.consecutive_sent = :inc", ExpressionAttributeNames={f"#{loc_name}": loc_name}, ExpressionAttributeValues={':inc': count + 1})
                else:
                    if config.get('consecutive_sent', 0) > 0:
                        table.update_item(Key={'user_id': user_id}, UpdateExpression=f"SET alerts.threshold.#{loc_name}.consecutive_sent = :zero", ExpressionAttributeNames={f"#{loc_name}": loc_name}, ExpressionAttributeValues={':zero': 0})

def lambda_handler(event, context):
    print("⏰ [V52] Scheduler - Iniciando ciclo...")
    now = get_cdmx_time()
    if now.hour < 6 or now.hour > 23: return {'statusCode': 200, 'body': 'Sleep'}

    # Check Maestro de Contingencia
    contingency_data = check_master_api_contingency() # (bool, phase, pollutant)
    
    try:
        paginator = dynamodb.meta.client.get_paginator('scan')
        count = 0
        for page in paginator.paginate(TableName=DYNAMODB_TABLE):
            for item in page['Items']:
                process_user(item, now.strftime("%H:%M"), contingency_data)
                count += 1
        print(f"✅ Ciclo finalizado. Usuarios: {count}")
    except Exception as e: print(f"Error critico: {e}")

    return {'statusCode': 200, 'body': 'OK'}
