import json
import os
import requests
import boto3
import unicodedata
from datetime import datetime, timedelta
from openai import OpenAI
import lambda_api_light 
import bot_content
import cards
import prompts

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
DYNAMODB_TABLE = 'SmabilityUsers'
client = OpenAI(api_key=OPENAI_API_KEY, timeout=20.0)
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

def clean_key(text):
    try:
        if not text: return "unknown"
        text = "".join([c for c in unicodedata.normalize('NFD', str(text)) if not unicodedata.combining(c)])
        return "".join(e for e in text if e.isalnum() or e.isspace()).strip().lower().replace(" ", "_")
    except: return "unknown"

def get_official_report_time():
    now = datetime.utcnow() - timedelta(hours=6)
    return (now.replace(minute=20) if now.minute >= 20 else now.replace(minute=20) - timedelta(hours=1)).strftime("%H:%M")

def get_time_greeting():
    h = (datetime.utcnow() - timedelta(hours=6)).hour
    return "Buenos días" if 5<=h<12 else "Buenas tardes" if 12<=h<20 else "Buenas noches"

def get_maps_url(lat, lon): return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

# --- FORMATEO VISUAL DEL PRONÓSTICO (V58) ---
def format_forecast_block(timeline):
    """Genera el bloque visual: 14:00 | 🟡 78 Regular"""
    if not timeline or not isinstance(timeline, list): return "➡️ Estable (Sin datos)"
    
    block = ""
    # Mapeo API -> Bot
    cat_map = {"Bajo": "Buena", "Moderado": "Regular", "Alto": "Mala", "Muy Alto": "Muy Mala", "Extremadamente Alto": "Extrema"}
    emoji_map = {"Bajo": "🟢", "Moderado": "🟡", "Alto": "🟠", "Muy Alto": "🔴", "Extremadamente Alto": "🟣"}
    
    count = 0
    for t in timeline:
        if count >= 4: break # Max 4 líneas
        hora = t.get('hora', '00:00')
        ias = t.get('ias', 0)
        riesgo_api = t.get('riesgo', 'Bajo')
        
        riesgo_bot = cat_map.get(riesgo_api, "Regular")
        emoji = emoji_map.get(riesgo_api, "🟢")
        
        block += f"`{hora}` | {emoji} {ias} {riesgo_bot}\n"
        count += 1
        
    return block.strip()

# --- FUNCIONES DE DB ---
def get_user_profile(user_id):
    try: return table.get_item(Key={'user_id': str(user_id)}, ConsistentRead=True).get('Item', {})
    except: return {}
def save_user_interaction(user_id, first_name):
    try: table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET first_name=:n, last_interaction=:t, locations=if_not_exists(locations,:e), alerts=if_not_exists(alerts,:al), health_profile=if_not_exists(health_profile,:e)", ExpressionAttributeValues={':n': first_name, ':t': datetime.now().isoformat(), ':e': {}, ':al': {'threshold': {}, 'schedule': {}}})
    except: pass
def save_location(user_id, raw_name, lat, lon):
    try:
        user = get_user_profile(user_id)
        # Permitir guardar Casa/Trabajo aunque exceda limite para el onboarding
        key = clean_key(raw_name)
        if len(user.get('locations', {})) >= 3 and key not in user.get('locations', {}) and key not in ['casa','trabajo']: return "⚠️ Límite 3 ubicaciones."
        
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set locations.#loc = :val", ExpressionAttributeNames={'#loc': key}, ExpressionAttributeValues={':val': {'lat': str(lat), 'lon': str(lon), 'display_name': raw_name, 'active': True}})
        
        # Auto-configurar alerta por defecto si es Casa o Trabajo
        if key in ['casa', 'trabajo']:
             table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set alerts.threshold.#l=:v", ExpressionAttributeNames={'#l': key}, ExpressionAttributeValues={':v': {'umbral': 100, 'active': True}})
        
        if key == "casa": return cards.CARD_ONBOARDING_WORK.format(footer=cards.BOT_FOOTER)
        return f"✅ Ubicación **{raw_name}** guardada."
    except: return "Error."
def delete_location(user_id, raw_name):
    try:
        key = clean_key(raw_name)
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE locations.#loc, alerts.threshold.#loc, alerts.schedule.#loc", ExpressionAttributeNames={'#loc': key})
        return f"🗑️ Ubicación **{raw_name}** eliminada."
    except: return "Error."
def rename_location(user_id, old, new):
    try:
        user = get_user_profile(user_id)
        old_k = clean_key(old)
        if old_k not in user.get('locations', {}): return "No existe."
        dat = user['locations'][old_k]
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set locations.#n=:v", ExpressionAttributeNames={'#n': clean_key(new)}, ExpressionAttributeValues={':v': {'lat': dat['lat'], 'lon': dat['lon'], 'display_name': new, 'active': True}})
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE locations.#o", ExpressionAttributeNames={'#o': old_k})
        return f"✅ Renombrado a **{new}**."
    except: return "Error."
def delete_full_profile(user_id, conf="SI"):
    if conf.upper()!="SI": return "Confirma con 'SI'."
    table.delete_item(Key={'user_id': str(user_id)})
    return "👋 Perfil borrado."
def save_health_profile(user_id, tipo, vul):
    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set health_profile.#h=:v", ExpressionAttributeNames={'#h': clean_key(tipo)}, ExpressionAttributeValues={':v': {'condition': tipo, 'is_vulnerable': vul}})
    return f"🩺 Salud guardada: {tipo}."
def delete_health_profile(user_id, tipo):
    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE health_profile.#h", ExpressionAttributeNames={'#h': clean_key(tipo)})
    return "🗑️ Salud borrada."
def set_alert_ias(user_id, loc, umbral):
    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set alerts.threshold.#l=:v", ExpressionAttributeNames={'#l': clean_key(loc)}, ExpressionAttributeValues={':v': {'umbral': int(umbral), 'active': True}})
    return f"🔔 Alerta IAS > {umbral} configurada."
def delete_alert_ias(user_id, loc):
    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE alerts.threshold.#l", ExpressionAttributeNames={'#l': clean_key(loc)})
    return "🔕 Alerta borrada."
def set_alert_time(user_id, loc, hora):
    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set alerts.schedule.#l=:v", ExpressionAttributeNames={'#l': clean_key(loc)}, ExpressionAttributeValues={':v': {'time': hora, 'active': True}})
    return f"⏰ Recordatorio {hora} configurado."
def delete_alert_time(user_id, loc):
    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE alerts.schedule.#l", ExpressionAttributeNames={'#l': clean_key(loc)})
    return "🔕 Recordatorio borrado."
def get_my_config_str(user_id):
    u = get_user_profile(user_id)
    msg = "📂 **DATOS**\n"
    for k,v in u.get('locations',{}).items(): msg+=f"📍 {v.get('display_name',k)}\n"
    return msg

def generate_report_card(user_name, location_name, lat, lon, health_condition=None):
    try:
        mock = {'queryStringParameters': {'lat': str(lat), 'lon': str(lon), 'mode': 'live'}}
        res = lambda_api_light.lambda_handler(mock, None)
        if res['statusCode'] != 200: return "⚠️ Error consultando modelo."
        data = json.loads(res['body'])
        
        qa = data.get('aire', {})
        meteo = data.get('meteo', {})
        
        # PROCESAR VISUALMENTE EL TIMELINE
        current_ias = qa.get('ias', 0)
        timeline = data.get('pronostico_timeline', [])
        forecast_block = format_forecast_block(timeline)

        cat_map = {"Bajo": "Buena", "Moderado": "Regular", "Alto": "Mala", "Muy Alto": "Muy Mala", "Extremadamente Alto": "Extremadamente Mala"}
        cat_bot = cat_map.get(qa.get('riesgo'), "Regular")
        info = cards.IAS_INFO.get(cat_bot, cards.IAS_INFO['Regular'])
        
        return cards.CARD_REPORT.format(
            user_name=user_name, greeting=get_time_greeting(), location_name=location_name,
            maps_url=get_maps_url(lat, lon), region="ZMVM", report_time=get_official_report_time(),
            ias_value=current_ias, risk_category=cat_bot, risk_circle=info['emoji'], natural_message=info['msg'],
            forecast_block=forecast_block, # <--- AQUI VA EL BLOQUE VISUAL
            health_recommendation=cards.get_health_advice(cat_bot, health_condition),
            temp=meteo.get('tmp', 0), humidity=meteo.get('rh', 0), wind_speed=meteo.get('wsp', 0),
            footer=cards.BOT_FOOTER
        )
    except Exception as e: return f"Error tarjeta: {str(e)}"

def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
        if 'message' not in body: return {'statusCode': 200, 'body': 'OK'}
        msg = body['message']
        chat_id = msg['chat']['id']
        user_id = msg['from']['id']
        first_name = msg['from'].get('first_name', 'Usuario')
        save_user_interaction(user_id, first_name)
        user_profile = get_user_profile(user_id)
        
        locs = user_profile.get('locations', {})
        health = user_profile.get('health_profile', {})
        health_str = ", ".join([v.get('condition','') for v in health.values()]) if health else None
        memoria_str = "\n".join([f"Loc: {v.get('display_name')}" for v in locs.values()])
        
        # --- LÓGICA DE ONBOARDING FORZOSO (V58) ---
        system_extra = "ESTADO: NORMAL."
        has_casa = any(k for k in locs if 'casa' in k)
        has_trabajo = any(k for k in locs if 'trabajo' in k)
        
        if not has_casa:
            system_extra = "ONBOARDING 1 (CASA). Pide la ubicación de CASA obligatoriamente."
        elif not has_trabajo:
            system_extra = "ONBOARDING 2 (TRABAJO). Pide la ubicación de TRABAJO obligatoriamente."
        
        user_content = ""
        if 'location' in msg:
            lat, lon = msg['location']['latitude'], msg['location']['longitude']
            user_content = f"COORDS: {lat},{lon}"
        elif 'text' in msg:
            user_content = msg['text']
            if user_content=="/start": 
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": cards.CARD_ONBOARDING.format(user_name=first_name, footer=cards.BOT_FOOTER), "parse_mode": "Markdown"})
                return {'statusCode': 200, 'body': 'OK'}

        gpt_msgs = [{"role": "system", "content": prompts.get_system_prompt(memoria_str, system_extra, first_name, get_official_report_time())}, {"role": "user", "content": user_content}]
        res = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_msgs, tools=bot_content.TOOLS_SCHEMA, tool_choice="auto", temperature=0.3)
        ai_msg = res.choices[0].message
        
        if ai_msg.tool_calls:
            gpt_msgs.append(ai_msg)
            for tc in ai_msg.tool_calls:
                fn = tc.function.name
                args = json.loads(tc.function.arguments)
                r=""
                if fn == "consultar_calidad_aire": r = generate_report_card(first_name, args.get('nombre_ubicacion','Ubicación'), args['lat'], args['lon'], health_str)
                elif fn == "guardar_ubicacion": r = save_location(user_id, args['nombre'], args['lat'], args['lon'])
                elif fn == "borrar_ubicacion": r = delete_location(user_id, args['nombre'])
                elif fn == "renombrar_ubicacion": r = rename_location(user_id, args['nombre_actual'], args['nombre_nuevo'])
                elif fn == "guardar_perfil_salud": r = save_health_profile(user_id, args['tipo_padecimiento'], args['es_vulnerable'])
                elif fn == "borrar_padecimiento": r = delete_health_profile(user_id, args['tipo_padecimiento'])
                elif fn == "configurar_alerta_ias": r = set_alert_ias(user_id, args['nombre_ubicacion'], args['umbral_ias'])
                elif fn == "borrar_alerta_ias": r = delete_alert_ias(user_id, args['nombre_ubicacion'])
                elif fn == "configurar_recordatorio": r = set_alert_time(user_id, args['nombre_ubicacion'], args['hora'])
                elif fn == "borrar_recordatorio": r = delete_alert_time(user_id, args['nombre_ubicacion'])
                elif fn == "borrar_perfil_completo": r = delete_full_profile(user_id, args.get('confirmacion','SI'))
                elif fn == "consultar_mis_datos": r = get_my_config_str(user_id)
                gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
            final = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_msgs, temperature=0.3).choices[0].message.content
        else: final = ai_msg.content

        if final: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": final, "parse_mode": "Markdown"})
        return {'statusCode': 200, 'body': 'OK'}
    except Exception as e: return {'statusCode': 500, 'body': str(e)}
