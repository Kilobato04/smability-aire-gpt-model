import json
import os
import logging
import requests
import boto3
import unicodedata
from datetime import datetime
from decimal import Decimal
from openai import OpenAI
import lambda_api_light 
import bot_content 

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
DYNAMODB_TABLE = 'SmabilityUsers'
HORA_INICIO = 6
HORA_FIN = 23

logger = logging.getLogger()
logger.setLevel(logging.INFO)

client = OpenAI(api_key=OPENAI_API_KEY, timeout=20.0)
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

# --- AUXILIARES ---
def clean_key(text):
    try:
        nfd_form = unicodedata.normalize('NFD', text)
        text = "".join([c for c in nfd_form if not unicodedata.combining(c)])
    except: pass
    return "".join(e for e in text if e.isalnum() or e.isspace()).strip().lower().replace(" ", "_")

def find_real_key(user_profile, search_name):
    locs = user_profile.get('locations', {})
    target_clean = clean_key(search_name)
    if target_clean in locs: return target_clean
    for key in locs.keys():
        if clean_key(key) == target_clean: return key 
    for key, val in locs.items():
        if clean_key(val.get('display_name', '')) == target_clean: return key
    return None

def get_user_profile(user_id):
    try: return table.get_item(Key={'user_id': str(user_id)}).get('Item', {})
    except: return {}

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal): return float(obj)
        return super(DecimalEncoder, self).default(obj)

def log_debug_data(user_id, first_name, user_profile):
    try:
        debug_data = {
            "USER_ID": user_id,
            "USER_NAME": first_name,
            "DATA": user_profile
        }
        print(f"📝 [FULL_DEBUG]: {json.dumps(debug_data, cls=DecimalEncoder)}")
    except: pass

def save_user_interaction(user_id, first_name):
    try:
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET first_name=:n, last_interaction=:t, locations=if_not_exists(locations,:e), alerts=if_not_exists(alerts,:e), vehicle=if_not_exists(vehicle,:e), health_profile=if_not_exists(health_profile,:e), commute=if_not_exists(commute,:e), flood_risk=if_not_exists(flood_risk,:e)",
            ExpressionAttributeValues={':n': first_name, ':t': datetime.now().isoformat(), ':e': {}}
        )
    except: pass

# --- TOOLS ---
def save_location(user_id, raw_name, lat, lon):
    try:
        safe_name = clean_key(raw_name)
        if not safe_name: return "❌ Nombre inválido."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set locations.#loc = :val", ExpressionAttributeNames={'#loc': safe_name}, ExpressionAttributeValues={':val': {'lat': str(lat), 'lon': str(lon), 'display_name': raw_name, 'active': True}})
        return f"✅ Ubicación guardada: **{raw_name}**."
    except Exception as e: return f"❌ Error: {str(e)}"

def save_vehicle(user_id, placa, holograma):
    try:
        ultimo_digito = str(placa)[-1]
        periodo = bot_content.INFO_VEHICULAR['calendario'].get(ultimo_digito, "Desconocido")
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set vehicle = :v", ExpressionAttributeValues={':v': {'plate_last_digit': ultimo_digito, 'hologram': str(holograma)}})
        return f"🚗 Auto guardado (Placa ...{ultimo_digito}). Verificas en: **{periodo}**."
    except Exception as e: return f"❌ Error: {str(e)}"

def save_health_profile(user_id, tipo, vulnerable):
    try:
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set health_profile = :h", ExpressionAttributeValues={':h': {'condition': tipo, 'is_vulnerable': vulnerable}})
        return f"🩺 Salud registrada ({tipo}). Ajustaré recomendaciones."
    except Exception as e: return f"❌ Error: {str(e)}"

def save_commute_data(user_id, tipo, horas):
    try:
        # Default a Público si viene vacío o ambiguo, y asegurar float
        h_val = float(horas)
        t_tipo = tipo if tipo and tipo.lower() != "string" else "Transporte Público"
        
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set commute = :c", ExpressionAttributeValues={':c': {'transport_type': t_tipo, 'daily_hours': Decimal(str(h_val))}})
        return f"🚌 Transporte guardado: **{h_val}h/día** en {t_tipo}."
    except Exception as e: return f"❌ Error transporte: {str(e)}"

def save_flood_risk(user_id, nombre_ubicacion, cm_aprox, descripcion):
    try:
        user = get_user_profile(user_id)
        real_key = find_real_key(user, nombre_ubicacion)
        if not real_key: return f"⚠️ No encuentro '{nombre_ubicacion}'."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set flood_risk.#loc = :val", ExpressionAttributeNames={'#loc': real_key}, ExpressionAttributeValues={':val': {'cm': int(cm_aprox), 'description': descripcion, 'active': True}})
        return f"🌧️ Encharcamiento de **{cm_aprox} cm** registrado en **{nombre_ubicacion}**."
    except Exception as e: return f"❌ Error inundación: {str(e)}"

def rename_location(user_id, old_name, new_name):
    try:
        user = get_user_profile(user_id)
        real_old_key = find_real_key(user, old_name)
        if not real_old_key: return f"⚠️ No encuentro '{old_name}'."
        old_data = user['locations'][real_old_key]
        safe_new_key = clean_key(new_name)
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set locations.#new = :val", ExpressionAttributeNames={'#new': safe_new_key}, ExpressionAttributeValues={':val': {'lat': old_data['lat'], 'lon': old_data['lon'], 'display_name': new_name, 'active': True}})
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE locations.#old", ExpressionAttributeNames={'#old': real_old_key})
        return f"✅ Renombrado: **{old_name}** -> **{new_name}**."
    except: return "Error renombrando."

def delete_location(user_id, raw_name):
    try:
        user = get_user_profile(user_id)
        real_key = find_real_key(user, raw_name)
        if not real_key: return f"⚠️ No encuentro '{raw_name}'."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE locations.#loc, alerts.threshold.#loc, alerts.schedule.#loc", ExpressionAttributeNames={'#loc': real_key})
        return f"🗑️ Eliminado: **{raw_name}**."
    except: return "Error borrando."

def delete_full_profile(user_id):
    try:
        table.delete_item(Key={'user_id': str(user_id)})
        return "☢️ **PERFIL BORRADO**. Envíame tu ubicación para empezar de cero."
    except: return "Error reset."

def set_alert_ias(user_id, nombre_ubicacion, umbral_ias):
    try:
        user = get_user_profile(user_id)
        real_key = find_real_key(user, nombre_ubicacion)
        if not real_key: return f"⚠️ No encuentro '{nombre_ubicacion}'."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set alerts.threshold.#loc = :val", ExpressionAttributeNames={'#loc': real_key}, ExpressionAttributeValues={':val': {'umbral': int(umbral_ias), 'active': True}})
        return f"🔔 Alerta activa: **{nombre_ubicacion}** > {umbral_ias} IAS."
    except Exception as e: return f"Error alerta: {str(e)}"

def set_alert_time(user_id, nombre_ubicacion, hora):
    try:
        h = int(hora.split(':')[0])
        if h < HORA_INICIO or h >= HORA_FIN: return "⚠️ Horario no válido."
        user = get_user_profile(user_id)
        real_key = find_real_key(user, nombre_ubicacion)
        if not real_key: return f"⚠️ No encuentro '{nombre_ubicacion}'."
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="set alerts.schedule.#loc = :val", ExpressionAttributeNames={'#loc': real_key}, ExpressionAttributeValues={':val': {'time': hora, 'active': True}})
        return f"⏰ Recordatorio: **{nombre_ubicacion}** a las {hora}."
    except Exception as e: return f"Error recordatorio: {str(e)}"

def get_my_config_str(user_id):
    try:
        data = get_user_profile(user_id)
        msg = "📂 **EXPEDIENTE SMABILITY**\\n"
        locs = data.get('locations', {})
        if locs:
            msg += "\\n📍 **Ubicaciones:**\\n"
            for k,v in locs.items(): msg += f"- {v.get('display_name', k)}\\n"
        veh = data.get('vehicle', {})
        if veh: msg += f"\\n🚗 **Auto:** ...{veh.get('plate_last_digit')} (Holo {veh.get('hologram')})\\n"
        salud = data.get('health_profile', {})
        if salud: msg += f"\\n🩺 **Salud:** {salud.get('condition')}\\n"
        commute = data.get('commute', {})
        if commute: msg += f"\\n🚌 **Transporte:** {commute.get('daily_hours')}h ({commute.get('transport_type')})\\n"
        flood = data.get('flood_risk', {})
        if flood:
            msg += "\\n🌧️ **Inundación:**\\n"
            for k,v in flood.items(): msg += f"- {k}: {v.get('cm')} cm\\n"
        
        alerts = data.get('alerts', {})
        thr = alerts.get('threshold', {})
        sch = alerts.get('schedule', {})
        if thr or sch:
            msg += "\\n🔔 **Alertas:**\\n"
            for k,v in thr.items(): msg += f"- IAS > {v.get('umbral')} en {k}\\n"
            for k,v in sch.items(): msg += f"- {v.get('time')} en {k}\\n"
            
        return msg
    except Exception as e: return f"Error perfil: {str(e)}"

def send_telegram_message(chat_id, text):
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

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
        log_debug_data(user_id, first_name, user_profile)
        
        locations_map = user_profile.get('locations', {})
        vehicle_data = user_profile.get('vehicle', {})
        health_data = user_profile.get('health_profile', {})
        commute_data = user_profile.get('commute', {})
        flood_data = user_profile.get('flood_risk', {})
        
        has_home = 'casa' in locations_map
        has_work = 'trabajo' in locations_map
        
        memoria_str = "SIN DATOS."
        if locations_map:
            memoria_str = "📍 LUGARES:\\n"
            for k, v in locations_map.items(): 
                riesgo = flood_data.get(k, {}).get('cm', 0)
                memoria_str += f"- {v.get('display_name', k)}: {v['lat']}, {v['lon']} {f'(Agua: {riesgo}cm)' if riesgo else ''}\\n"
        
        info_estatica = f"CDMX: Multa Verif: {bot_content.INFO_VEHICULAR['multa_extemporanea']}"

        user_content = ""
        system_instruction_extra = ""

        if 'location' in msg:
            lat, lon = msg['location']['latitude'], msg['location']['longitude']
            user_content = f"COORDENADAS: {lat}, {lon}."
            if not has_home:
                system_instruction_extra = "MODO ONBOARDING CASA: Guarda como 'Casa'. Confirma."
            elif not has_work:
                system_instruction_extra = "MODO ONBOARDING TRABAJO: Guarda como 'Trabajo'. Confirma."
            else:
                system_instruction_extra = "Perfil base listo. Da reporte de aire."

        elif 'text' in msg:
            user_content = msg['text']
            if user_content.strip() == "/start":
                welcome = bot_content.get_welcome_message(first_name)
                send_telegram_message(chat_id, welcome)
                return {'statusCode': 200, 'body': 'OK'}
            elif user_content.strip() == "/version":
                send_telegram_message(chat_id, f"🤖 **{bot_content.BOT_VERSION}**")
                return {'statusCode': 200, 'body': 'OK'}
        else: return {'statusCode': 200, 'body': 'OK'}

        gpt_messages = [
            {"role": "system", "content": bot_content.get_system_prompt(memoria_str, info_estatica, system_instruction_extra)},
            {"role": "user", "content": user_content}
        ]

        try:
            response = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_messages, tools=bot_content.TOOLS_SCHEMA, tool_choice="auto")
            ai_msg = response.choices[0].message
            final_text = ai_msg.content
        except Exception as api_err:
            logger.error(f"OpenAI Error: {api_err}")
            return {'statusCode': 200, 'body': 'Timeout Handled'}

        if ai_msg.tool_calls:
            gpt_messages.append(ai_msg)
            for tool_call in ai_msg.tool_calls:
                fn = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                res = ""
                # Router
                if fn == "consultar_calidad_aire":
                    mock = {'queryStringParameters': {'lat': str(args['lat']), 'lon': str(args['lon'])}}
                    res = lambda_api_light.lambda_handler(mock, None)['body']
                elif fn == "guardar_ubicacion": res = save_location(user_id, args['nombre'], args['lat'], args['lon'])
                elif fn == "guardar_vehiculo": res = save_vehicle(user_id, args['terminacion_placa'], args['holograma'])
                elif fn == "guardar_perfil_salud": res = save_health_profile(user_id, args['tipo_padecimiento'], args['es_vulnerable'])
                elif fn == "guardar_transporte": res = save_commute_data(user_id, args['tipo_transporte'], args['horas_diarias'])
                elif fn == "guardar_riesgo_inundacion": res = save_flood_risk(user_id, args['nombre_ubicacion'], args['cm_aprox'], args['descripcion'])
                elif fn == "renombrar_ubicacion": res = rename_location(user_id, args['nombre_actual'], args['nombre_nuevo'])
                elif fn == "borrar_ubicacion": res = delete_location(user_id, args['nombre'])
                elif fn == "borrar_perfil_completo": res = delete_full_profile(user_id)
                elif fn == "configurar_alerta_ias": res = set_alert_ias(user_id, args['nombre_ubicacion'], args['umbral_ias'])
                elif fn == "configurar_recordatorio": res = set_alert_time(user_id, args['nombre_ubicacion'], args['hora'])
                elif fn == "consultar_mis_datos": res = get_my_config_str(user_id)
                
                gpt_messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": fn, "content": str(res)})

            final_res = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_messages)
            final_text = final_res.choices[0].message.content

        if final_text: send_telegram_message(chat_id, final_text)
        return {'statusCode': 200, 'body': 'OK'}

    except Exception as e:
        logger.error(f"Error: {e}")
        return {'statusCode': 500, 'body': str(e)}
