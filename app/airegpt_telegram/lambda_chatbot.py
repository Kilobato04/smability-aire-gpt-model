import json
import os
import logging
import requests
import boto3
import unicodedata
import traceback
from datetime import datetime, timedelta
from decimal import Decimal
from openai import OpenAI
import lambda_api_light 
import bot_content
import cards
import prompts

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
DYNAMODB_TABLE = 'SmabilityUsers'

logger = logging.getLogger()
logger.setLevel(logging.INFO)

client = OpenAI(api_key=OPENAI_API_KEY, timeout=20.0)
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

# --- AUXILIARES ---
def clean_key(text):
    try:
        if not text: return "unknown"
        nfd_form = unicodedata.normalize('NFD', str(text))
        text = "".join([c for c in nfd_form if not unicodedata.combining(c)])
        return "".join(e for e in text if e.isalnum() or e.isspace()).strip().lower().replace(" ", "_")
    except: return "unknown"

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal): return float(obj)
        return super(DecimalEncoder, self).default(obj)

def get_official_report_time():
    try:
        utc_now = datetime.utcnow()
        cdmx_now = utc_now - timedelta(hours=6)
        if cdmx_now.minute >= 20: report_time = cdmx_now.replace(minute=20)
        else: report_time = cdmx_now.replace(minute=20) - timedelta(hours=1)
        return report_time.strftime("%H:%M")
    except: return "N/A"

def get_time_greeting():
    try:
        utc_now = datetime.utcnow()
        hour = (utc_now - timedelta(hours=6)).hour
        if 5 <= hour < 12: return "Buenos días"
        elif 12 <= hour < 20: return "Buenas tardes"
        else: return "Buenas noches"
    except: return "Hola"

def get_maps_url(lat, lon):
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

# --- GENERADOR DE TARJETAS ---
def generate_report_card(user_name, location_name, lat, lon, health_condition=None):
    try:
        mock = {'queryStringParameters': {'lat': str(lat), 'lon': str(lon)}}
        api_res = lambda_api_light.lambda_handler(mock, None)
        
        if api_res['statusCode'] != 200: return "⚠️ Error consultando modelo."
        data = json.loads(api_res['body'])
        
        ias_val = data.get('ias', 0)
        cat_raw = data.get('category', 'Buena').title()
        if cat_raw not in cards.IAS_INFO: cat_key = "Regular"
        else: cat_key = cat_raw
        
        info = cards.IAS_INFO[cat_key]
        forecast_msg = str(data.get('forecast_24h', 'Estable'))
        
        # --- PERSONALIZACIÓN SALUD ---
        custom_rec = cards.get_health_advice(cat_key, health_condition)
        
        return cards.CARD_REPORT.format(
            user_name=user_name,
            greeting=get_time_greeting(),
            location_name=location_name,
            maps_url=get_maps_url(lat, lon),
            region="ZMVM",
            report_time=get_official_report_time(),
            ias_value=ias_val,
            risk_category=cat_key,
            risk_circle=info['emoji'],
            natural_message=info['msg'],
            forecast_msg=forecast_msg,
            pollutant=data.get('dominant_pollutant', 'N/A'),
            health_recommendation=custom_rec, # Inyección de lógica personalizada
            temp=data.get('meteorological', {}).get('temperature', 0),
            humidity=data.get('meteorological', {}).get('humidity', 0),
            wind_speed=data.get('meteorological', {}).get('wind_speed', 0),
            footer=cards.BOT_FOOTER
        )
    except Exception as e:
        traceback.print_exc()
        return "Error generando tarjeta."

# --- DB & USER ---
def get_user_profile(user_id):
    try: 
        item = table.get_item(Key={'user_id': str(user_id)}, ConsistentRead=True).get('Item', {})
        # Limpieza básica
        for trash in ['vehicle', 'vehicles', 'commute', 'flood_risk']:
            if trash in item: item.pop(trash, None)
        return item
    except: return {}

def save_user_interaction(user_id, first_name):
    try:
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET first_name=:n, last_interaction=:t, locations=if_not_exists(locations,:e), alerts=if_not_exists(alerts,:al), health_profile=if_not_exists(health_profile,:e)",
            ExpressionAttributeValues={':n': first_name, ':t': datetime.now().isoformat(), ':e': {}, ':al': {'threshold': {}, 'schedule': {}}}
        )
    except: pass

def find_real_key(user_profile, search_name, sub_map='locations'):
    if sub_map.startswith('alerts'):
        subtype = sub_map.split('_')[1]
        alerts = user_profile.get('alerts', {}).get(subtype, {})
        target = clean_key(search_name)
        return target if target in alerts else None

    data_map = user_profile.get(sub_map, {})
    if not isinstance(data_map, dict): return None
    target_clean = clean_key(search_name)
    
    if target_clean in data_map: return target_clean
    for key in data_map.keys():
        if clean_key(key) == target_clean: return key
    for key, val in data_map.items():
        if isinstance(val, dict):
            if sub_map == 'locations' and clean_key(val.get('display_name', '')) == target_clean: return key
            if sub_map == 'health_profile' and clean_key(val.get('condition', '')) == target_clean: return key
    return None

# --- TOOLS ---
def save_location(user_id, raw_name, lat, lon):
    try:
        user = get_user_profile(user_id)
        locs = user.get('locations', {})
        if not isinstance(locs, dict): locs = {}
        safe_name = clean_key(raw_name)
        if safe_name not in locs and len(locs) >= 3: return "⚠️ Límite de 3 ubicaciones lleno."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set locations.#loc = :val", ExpressionAttributeNames={'#loc': safe_name}, ExpressionAttributeValues={':val': {'lat': str(lat), 'lon': str(lon), 'display_name': raw_name, 'active': True}})
        return f"✅ Ubicación **{raw_name}** guardada."
    except Exception as e: return f"Error: {str(e)}"

def delete_location(user_id, raw_name):
    try:
        user = get_user_profile(user_id)
        real_key = find_real_key(user, raw_name, 'locations')
        if not real_key: return f"⚠️ No encontré '{raw_name}'."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE locations.#loc, alerts.threshold.#loc, alerts.schedule.#loc", ExpressionAttributeNames={'#loc': real_key})
        return f"🗑️ Ubicación **{raw_name}** eliminada."
    except Exception as e: return f"Error: {str(e)}"

def rename_location(user_id, old_name, new_name):
    try:
        user = get_user_profile(user_id)
        real_old_key = find_real_key(user, old_name, 'locations')
        if not real_old_key: return f"⚠️ No encuentro '{old_name}'."
        locs = user.get('locations', {})
        old_data = locs.get(real_old_key)
        safe_new_key = clean_key(new_name)
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set locations.#new = :val", ExpressionAttributeNames={'#new': safe_new_key}, ExpressionAttributeValues={':val': {'lat': old_data['lat'], 'lon': old_data['lon'], 'display_name': new_name, 'active': True}})
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE locations.#old", ExpressionAttributeNames={'#old': real_old_key})
        return f"✅ Renombrado: **{new_name}**."
    except: return "Error renombrando."

def delete_full_profile(user_id, confirmacion="SI"):
    try:
        if confirmacion.upper() not in ["SI", "SÍ", "YES"]: return "⚠️ Confirma con 'SI'."
        table.delete_item(Key={'user_id': str(user_id)})
        return "👋 **PERFIL BORRADO.**"
    except: return "Error borrando."

def save_health_profile(user_id, tipo, vulnerable):
    try:
        user = get_user_profile(user_id)
        health = user.get('health_profile', {})
        if not isinstance(health, dict): health = {}
        key_id = clean_key(tipo)
        if key_id not in health and len(health) >= 2: return "⚠️ Límite salud (Max 2)."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set health_profile.#hid = :val", ExpressionAttributeNames={'#hid': key_id}, ExpressionAttributeValues={':val': {'condition': tipo, 'is_vulnerable': vulnerable}})
        return f"🩺 Salud guardada: **{tipo}**."
    except Exception as e: return f"Error salud: {str(e)}"

def delete_health_profile(user_id, tipo_padecimiento):
    try:
        user = get_user_profile(user_id)
        real_key = find_real_key(user, tipo_padecimiento, 'health_profile')
        if not real_key: return f"⚠️ No encuentro '{tipo_padecimiento}'."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE health_profile.#hid", ExpressionAttributeNames={'#hid': real_key})
        return f"🗑️ Salud eliminada."
    except: return "Error borrando salud."

def set_alert_ias(user_id, nombre_ubicacion, umbral_ias):
    try:
        user = get_user_profile(user_id)
        real_key = find_real_key(user, nombre_ubicacion, 'locations')
        if not real_key: return f"⚠️ Ubicación '{nombre_ubicacion}' no encontrada."
        if 'alerts' not in user: user['alerts'] = {}
        if 'threshold' not in user['alerts']: user['alerts']['threshold'] = {}
        user['alerts']['threshold'][real_key] = {'umbral': int(umbral_ias), 'active': True}
        table.put_item(Item=user)
        return f"🔔 Alerta IAS > {umbral_ias} configurada."
    except: return "Error alerta."

def delete_alert_ias(user_id, nombre_ubicacion):
    try:
        user = get_user_profile(user_id)
        real_key = find_real_key(user, nombre_ubicacion, 'locations')
        if not real_key: real_key = find_real_key(user, nombre_ubicacion, 'alerts_threshold')
        if not real_key: return f"⚠️ No encuentro alerta para '{nombre_ubicacion}'."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE alerts.threshold.#loc", ExpressionAttributeNames={'#loc': real_key})
        return f"🔕 Alerta eliminada."
    except: return "Error borrando alerta."

def set_alert_time(user_id, nombre_ubicacion, hora):
    try:
        user = get_user_profile(user_id)
        real_key = find_real_key(user, nombre_ubicacion, 'locations')
        if not real_key: return f"⚠️ Ubicación '{nombre_ubicacion}' no encontrada."
        if 'alerts' not in user: user['alerts'] = {}
        if 'schedule' not in user['alerts']: user['alerts']['schedule'] = {}
        user['alerts']['schedule'][real_key] = {'time': hora, 'active': True}
        table.put_item(Item=user)
        return f"⏰ Recordatorio a las {hora} configurado."
    except: return "Error recordatorio."

def delete_alert_time(user_id, nombre_ubicacion):
    try:
        user = get_user_profile(user_id)
        real_key = find_real_key(user, nombre_ubicacion, 'locations')
        if not real_key: real_key = find_real_key(user, nombre_ubicacion, 'alerts_schedule')
        if not real_key: return f"⚠️ No encuentro recordatorio."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE alerts.schedule.#loc", ExpressionAttributeNames={'#loc': real_key})
        return f"🔕 Recordatorio eliminado."
    except: return "Error borrando recordatorio."

def get_my_config_str(user_id):
    try:
        data = get_user_profile(user_id)
        msg = "📂 **DATOS**\n"
        locs = data.get('locations', {})
        if isinstance(locs, dict) and locs:
            for k,v in locs.items(): msg += f"📍 {v.get('display_name', k)}\n"
        else: msg += "(Sin ubicaciones)\n"
        return msg
    except: return "Error."

def send_telegram_message(chat_id, text):
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

# --- HANDLER ---
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
        
        locations_map = user_profile.get('locations', {})
        health_map = user_profile.get('health_profile', {})
        
        # --- EXTRACCIÓN DE SALUD PARA PERSONALIZACIÓN ---
        health_str = None
        if isinstance(health_map, dict) and health_map:
            conditions = [v.get('condition', 'Padecimiento') for v in health_map.values() if isinstance(v, dict)]
            if conditions:
                health_str = ", ".join(conditions)
        
        has_home = False
        has_work = False
        if isinstance(locations_map, dict):
            has_home = 'casa' in locations_map
            has_work = 'trabajo' in locations_map
        
        items = []
        if isinstance(locations_map, dict):
            for k, v in locations_map.items(): 
                if isinstance(v, dict): items.append(f"Ubicación '{v.get('display_name', k)}': Lat {v.get('lat')}, Lon {v.get('lon')}")
        if health_str:
             items.append(f"Salud: {health_str}")
        
        memoria_str = "SIN DATOS."
        if items: memoria_str = "\n".join(items)

        official_time = get_official_report_time()
        user_content = ""
        system_extra = ""

        if 'location' in msg:
            lat, lon = msg['location']['latitude'], msg['location']['longitude']
            user_content = f"COORDENADAS: {lat}, {lon}."
            if not has_home:
                system_extra = "MODO ONBOARDING 1: Guárdala como 'Casa'."
            elif not has_work:
                system_extra = "MODO ONBOARDING 2: Guárdala como 'Trabajo'."
            else:
                system_extra = "Usuario envió ubicación. MUESTRA REPORTE DE AIRE."

        elif 'text' in msg:
            user_content = msg['text']
            if user_content.strip() == "/start":
                welcome = cards.CARD_ONBOARDING.format(user_name=first_name, footer=cards.BOT_FOOTER)
                send_telegram_message(chat_id, welcome)
                return {'statusCode': 200, 'body': 'OK'}
            elif user_content.strip() == "/version":
                send_telegram_message(chat_id, f"🤖 **{bot_content.BOT_VERSION}**")
                return {'statusCode': 200, 'body': 'OK'}
        else: return {'statusCode': 200, 'body': 'OK'}

        gpt_messages = [
            {"role": "system", "content": prompts.get_system_prompt(memoria_str, system_extra, first_name, official_time)},
            {"role": "user", "content": user_content}
        ]

        try:
            response = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_messages, tools=bot_content.TOOLS_SCHEMA, tool_choice="auto", temperature=0.3)
            ai_msg = response.choices[0].message
            final_text = ai_msg.content
        except: return {'statusCode': 200, 'body': 'Timeout'}

        if ai_msg.tool_calls:
            gpt_messages.append(ai_msg)
            for tool_call in ai_msg.tool_calls:
                fn = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                res = ""
                
                if fn == "consultar_calidad_aire":
                    loc_name = args.get('nombre_ubicacion', 'Ubicación Actual')
                    # PASAMOS LA SALUD AL GENERADOR DE TARJETA
                    res = generate_report_card(first_name, loc_name, args['lat'], args['lon'], health_str)
                elif fn == "guardar_ubicacion": res = save_location(user_id, args['nombre'], args['lat'], args['lon'])
                elif fn == "borrar_ubicacion": res = delete_location(user_id, args['nombre'])
                elif fn == "renombrar_ubicacion": res = rename_location(user_id, args['nombre_actual'], args['nombre_nuevo'])
                elif fn == "guardar_perfil_salud": res = save_health_profile(user_id, args['tipo_padecimiento'], args['es_vulnerable'])
                elif fn == "borrar_padecimiento": res = delete_health_profile(user_id, args['tipo_padecimiento'])
                elif fn == "configurar_alerta_ias": res = set_alert_ias(user_id, args['nombre_ubicacion'], args['umbral_ias'])
                elif fn == "borrar_alerta_ias": res = delete_alert_ias(user_id, args['nombre_ubicacion'])
                elif fn == "configurar_recordatorio": res = set_alert_time(user_id, args['nombre_ubicacion'], args['hora'])
                elif fn == "borrar_recordatorio": res = delete_alert_time(user_id, args['nombre_ubicacion'])
                elif fn == "borrar_perfil_completo": res = delete_full_profile(user_id, args.get('confirmacion', 'SI'))
                elif fn == "consultar_mis_datos": res = get_my_config_str(user_id)
                
                gpt_messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": fn, "content": str(res)})

            final_res = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_messages, temperature=0.3)
            final_text = final_res.choices[0].message.content

        if final_text: send_telegram_message(chat_id, final_text)
        return {'statusCode': 200, 'body': 'OK'}

    except Exception as e:
        logger.error(f"Error Lambda: {e}")
        return {'statusCode': 500, 'body': str(e)}
