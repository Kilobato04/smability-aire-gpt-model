import json
import os
import time
import requests
import boto3
from datetime import datetime, timedelta
import lambda_api_light
import cards

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
DYNAMODB_TABLE = 'SmabilityUsers'
MASTER_API_URL = "https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference"
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

def get_cdmx_time(): return datetime.utcnow() - timedelta(hours=6)
def clean_key(text): return "".join(e for e in text if e.isalnum()).lower()
def get_maps_url(lat, lon): return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
def send_telegram_push(chat_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        time.sleep(0.05)
    except: pass

def interpret_timeline_short(current_ias, timeline):
    """Resumen corto para Alertas de Emergencia"""
    if not timeline or not isinstance(timeline, list): return "Estable"
    try:
        max_point = max(timeline, key=lambda x: x.get('ias', 0))
        diff = max_point['ias'] - current_ias
        if diff > 10: return f"Sigue alta hasta las {max_point['hora']}"
        elif diff < -10: return "Mejora pronto"
        return "Estable"
    except: return "Estable"

def format_forecast_block(timeline):
    """Bloque visual para Reportes Diarios"""
    if not timeline or not isinstance(timeline, list): return "➡️ Estable"
    block = ""
    cat_map = {"Bajo": "Buena", "Moderado": "Regular", "Alto": "Mala", "Muy Alto": "Muy Mala", "Extremadamente Alto": "Extrema"}
    emoji_map = {"Bajo": "🟢", "Moderado": "🟡", "Alto": "🟠", "Muy Alto": "🔴", "Extremadamente Alto": "🟣"}
    count = 0
    for t in timeline:
        if count >= 4: break
        riesgo_bot = cat_map.get(t.get('riesgo'), "Regular")
        emoji = emoji_map.get(t.get('riesgo'), "🟢")
        block += f"`{t.get('hora')}` | {emoji} {t.get('ias')} {riesgo_bot}\n"
        count += 1
    return block.strip()

def check_master_api_contingency():
    try:
        r = requests.get(MASTER_API_URL, timeout=5)
        if r.status_code == 200:
            d = r.json().get('contingency')
            if d and isinstance(d, dict):
                p = "Ozono" if 'ozone' in str(d.get('alert_type')).lower() else "Partículas"
                try: p += f" ({int(float(d['value']['value']))} {d['value']['unit']})"
                except: pass
                return True, d.get('phase','Fase I'), p
    except: pass
    return False, "", ""

def get_location_air_data(lat, lon):
    try:
        mock = {'queryStringParameters': {'lat': str(lat), 'lon': str(lon), 'mode': 'live'}}
        res = lambda_api_light.lambda_handler(mock, None)
        if res['statusCode'] == 200: return json.loads(res['body'])
    except: pass
    return None

def process_user(user, current_hour_str, contingency_data):
    user_id = user['user_id']
    first_name = user.get('first_name', 'Usuario')
    alerts = user.get('alerts', {})
    locations = user.get('locations', {})
    health = user.get('health_profile', {})
    h_str = ", ".join([v.get('condition','') for v in health.values()]) if health else None

    # 1. CONTINGENCIA
    is_c, ph, pol = contingency_data
    if is_c:
        last = user.get('last_contingency_date', '')
        today = get_cdmx_time().strftime("%Y-%m-%d")
        if last != today:
            card = cards.CARD_CONTINGENCY.format(user_name=first_name, report_time=f"{current_hour_str.split(':')[0]}:20", phase=ph, pollutant=pol, forecast_msg="Oficial", footer=cards.BOT_FOOTER)
            send_telegram_push(user_id, card)
            table.update_item(Key={'user_id': user_id}, UpdateExpression="SET last_contingency_date = :d", ExpressionAttributeValues={':d': today})
            return

    # 2. RECORDATORIOS (Reporte Diario = Bloque Visual Completo)
    for loc_name, config in alerts.get('schedule', {}).items():
        if config.get('time', '').split(':')[0] == current_hour_str.split(':')[0]:
            loc_data = locations.get(loc_name)
            if loc_data:
                data = get_location_air_data(loc_data['lat'], loc_data['lon'])
                if data:
                    qa = data.get('aire', {})
                    f_block = format_forecast_block(data.get('pronostico_timeline', [])) # <--- BLOQUE VISUAL
                    
                    cat_map = {"Bajo": "Buena", "Moderado": "Regular", "Alto": "Mala", "Muy Alto": "Muy Mala", "Extremadamente Alto": "Extremadamente Mala"}
                    cat = cat_map.get(qa.get('riesgo'), "Regular")
                    info = cards.IAS_INFO.get(cat, cards.IAS_INFO['Regular'])
                    card = cards.CARD_REMINDER.format(
                        user_name=first_name, location_name=loc_data.get('display_name', loc_name),
                        maps_url=get_maps_url(loc_data['lat'], loc_data['lon']),
                        report_time=f"{current_hour_str.split(':')[0]}:20", region="ZMVM",
                        ias_value=qa.get('ias', 0), risk_category=cat, risk_circle=info['emoji'],
                        natural_message=info['msg'], forecast_block=f_block, # CAMBIO A BLOCK
                        health_recommendation=cards.get_health_advice(cat, h_str),
                        footer=cards.BOT_FOOTER
                    )
                    send_telegram_push(user_id, card)

    # 3. ALERTAS UMBRAL (Emergencia = Resumen Corto)
    for loc_name, config in alerts.get('threshold', {}).items():
        if not config.get('active', False): continue
        umbral = max(int(config.get('umbral', 100)), 100)
        loc_data = locations.get(loc_name)
        if loc_data:
            data = get_location_air_data(loc_data['lat'], loc_data['lon'])
            if data:
                qa = data.get('aire', {})
                cur_ias = qa.get('ias', 0)
                if cur_ias > umbral:
                    count = int(config.get('consecutive_sent', 0))
                    if count < 3:
                        f_short = interpret_timeline_short(cur_ias, data.get('pronostico_timeline', [])) # <--- RESUMEN CORTO
                        cat_map = {"Bajo": "Buena", "Moderado": "Regular", "Alto": "Mala", "Muy Alto": "Muy Mala", "Extremadamente Alto": "Extremadamente Mala"}
                        cat = cat_map.get(qa.get('riesgo'), "Regular")
                        info = cards.IAS_INFO.get(cat, cards.IAS_INFO['Mala'])
                        card = cards.CARD_ALERT_IAS.format(
                            user_name=first_name, location_name=loc_data.get('display_name', loc_name),
                            maps_url=get_maps_url(loc_data['lat'], loc_data['lon']),
                            report_time=f"{current_hour_str.split(':')[0]}:20", region="ZMVM",
                            risk_category=cat, risk_circle=info['emoji'], ias_value=cur_ias,
                            forecast_msg=f_short, natural_message=info['msg'],
                            threshold=umbral, pollutant="N/A", health_recommendation=cards.get_health_advice(cat, h_str),
                            footer=cards.BOT_FOOTER
                        )
                        send_telegram_push(user_id, card)
                        table.update_item(Key={'user_id': user_id}, UpdateExpression=f"SET alerts.threshold.#{loc_name}.consecutive_sent = :inc", ExpressionAttributeNames={f"#{loc_name}": loc_name}, ExpressionAttributeValues={':inc': count + 1})
                elif config.get('consecutive_sent', 0) > 0:
                    table.update_item(Key={'user_id': user_id}, UpdateExpression=f"SET alerts.threshold.#{loc_name}.consecutive_sent = :zero", ExpressionAttributeNames={f"#{loc_name}": loc_name}, ExpressionAttributeValues={':zero': 0})

def lambda_handler(event, context):
    now = get_cdmx_time()
    if now.hour < 6 or now.hour > 23: return {'statusCode': 200, 'body': 'Sleep'}
    cont_data = check_master_api_contingency()
    try:
        paginator = dynamodb.meta.client.get_paginator('scan')
        for page in paginator.paginate(TableName=DYNAMODB_TABLE):
            for item in page['Items']: process_user(item, now.strftime("%H:%M"), cont_data)
    except Exception as e: print(f"Error: {e}")
    return {'statusCode': 200, 'body': 'OK'}
