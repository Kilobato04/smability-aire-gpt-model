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

def check_master_api_contingency():
    try:
        response = requests.get(MASTER_API_URL, timeout=5)
        if response.status_code == 200:
            data = response.json()
            is_active = False
            phase = "Fase 1"
            pollutant = "Ozono"
            
            if isinstance(data, dict):
                if data.get('contingency') is True: is_active = True
            
            if is_active: return True, phase, pollutant
    except: pass
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

    # 1. CONTINGENCIA
    is_cont, phase, pol = contingency_data
    if is_cont:
        last_sent = user.get('last_contingency_date', '')
        today = get_cdmx_time().strftime("%Y-%m-%d")
        
        if last_sent != today:
            card = cards.CARD_CONTINGENCY.format(
                user_name=first_name,
                report_time=f"{current_hour_str.split(':')[0]}:20",
                phase=phase,
                pollutant=pol,
                forecast_msg="Mantener precaución",
                footer=cards.BOT_FOOTER
            )
            send_telegram_push(user_id, card)
            table.update_item(Key={'user_id': user_id}, UpdateExpression="SET last_contingency_date = :d", ExpressionAttributeValues={':d': today})

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
                    
                    # PERSONALIZACIÓN
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
                        health_recommendation=custom_rec, # AQUI
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
                        
                        # PERSONALIZACIÓN
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
                            health_recommendation=custom_rec, # AQUI
                            footer=cards.BOT_FOOTER
                        )
                        send_telegram_push(user_id, card)
                        table.update_item(Key={'user_id': user_id}, UpdateExpression=f"SET alerts.threshold.#{loc_name}.consecutive_sent = :inc", ExpressionAttributeNames={f"#{loc_name}": loc_name}, ExpressionAttributeValues={':inc': count + 1})
                else:
                    if config.get('consecutive_sent', 0) > 0:
                        table.update_item(Key={'user_id': user_id}, UpdateExpression=f"SET alerts.threshold.#{loc_name}.consecutive_sent = :zero", ExpressionAttributeNames={f"#{loc_name}": loc_name}, ExpressionAttributeValues={':zero': 0})

def lambda_handler(event, context):
    print("⏰ [V51] Scheduler Iniciado")
    now = get_cdmx_time()
    if now.hour < 6 or now.hour > 23: return {'statusCode': 200, 'body': 'Sleep'}

    cont_data = check_master_api_contingency()
    
    try:
        paginator = dynamodb.meta.client.get_paginator('scan')
        count = 0
        for page in paginator.paginate(TableName=DYNAMODB_TABLE):
            for item in page['Items']:
                process_user(item, now.strftime("%H:%M"), cont_data)
                count += 1
        print(f"✅ Procesados {count} usuarios.")
    except Exception as e: print(f"Error: {e}")

    return {'statusCode': 200, 'body': 'OK'}
