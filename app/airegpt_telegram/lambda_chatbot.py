import json
import os
import requests
import boto3
from datetime import datetime, timedelta
from openai import OpenAI
import bot_content
import cards
import prompts

# --- CONFIG ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
API_LIGHT_URL = os.environ.get('API_LIGHT_URL', 'https://vuy3dprsp2udtuelnrb5leg6ay0ygsky.lambda-url.us-east-1.on.aws/')
DYNAMODB_TABLE = 'SmabilityUsers'

client = OpenAI(api_key=OPENAI_API_KEY, timeout=20.0)
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

# --- 🧠 REGLAS DE NEGOCIO (IDENTICAS AL CRM) ---
BUSINESS_RULES = {
    "FREE": {"loc_limit": 2, "alert_limit": 0, "can_contingency": False},
    "PREMIUM": {"loc_limit": 3, "alert_limit": 10, "can_contingency": True}
}

# --- GATEKEEPER: VERIFICADOR DE CUPOS ---
def check_quota_and_permissions(user_profile, action_type):
    """
    Retorna (True, "") si puede proceder.
    Retorna (False, "Mensaje de Venta") si está bloqueado.
    """
    # 1. Identificar Plan
    sub = user_profile.get('subscription', {})
    status = sub.get('status', 'FREE')
    # Mapeo simple: Si dice PREMIUM en cualquier lado, usa reglas Premium
    rule_key = "PREMIUM" if "PREMIUM" in status.upper() else "FREE"
    rules = BUSINESS_RULES[rule_key]

    # 2. Validar Acción: AGREGAR UBICACIÓN
    if action_type == 'add_location':
        current_locs = len(user_profile.get('locations', {}))
        if current_locs >= rules['loc_limit']:
            return False, (
                f"🛑 **Límite Alcanzado ({current_locs}/{rules['loc_limit']})**\n\n"
                "Tu plan **Básico** solo permite 2 ubicaciones guardadas.\n"
                "💎 **Cámbiate a Premium** para agregar más lugares y recibir alertas."
            )

    # 3. Validar Acción: CREAR ALERTA (Horario o Umbral)
    if action_type == 'add_alert':
        if rules['alert_limit'] == 0:
            return False, (
                "🔒 **Función Premium**\n\n"
                "Las alertas automáticas (por horario o contaminación alta) son exclusivas de Smability Premium.\n"
                "💎 **Actívalo hoy por solo $49 MXN/mes.**"
            )
        
        # Contar alertas actuales
        alerts = user_profile.get('alerts', {})
        total_used = 0
        # Sumar thresholds activos
        for k, v in alerts.get('threshold', {}).items():
            if v.get('active'): total_used += 1
        # Sumar schedules activos
        for k, v in alerts.get('schedule', {}).items():
            if v.get('active'): total_used += 1 # Asumimos que si existe está activa o check flag

        if total_used >= rules['alert_limit']:
            return False, f"🛑 **Has alcanzado tu límite de {rules['alert_limit']} alertas.**"

    return True, ""

# --- DB HELPERS ---
def get_user_profile(user_id):
    try: return table.get_item(Key={'user_id': str(user_id)}, ConsistentRead=True).get('Item', {})
    except: return {}

def update_user_status(user_id, new_status):
    """Backdoor para pruebas"""
    try:
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET subscription = :s",
            ExpressionAttributeValues={':s': {'status': new_status, 'tier': f"{new_status}_MANUAL"}}
        )
        return True
    except: return False

def save_interaction_and_draft(user_id, first_name, lat=None, lon=None):
    update_expr = "SET first_name=:n, last_interaction=:t, locations=if_not_exists(locations,:e), alerts=if_not_exists(alerts,:al), subscription=if_not_exists(subscription,:sub)"
    vals = {
        ':n': first_name, 
        ':t': datetime.now().isoformat(), 
        ':e': {}, 
        ':al': {'threshold': {}, 'schedule': {}},
        ':sub': {'status': 'FREE'} # Default a FREE si es nuevo
    }
    if lat and lon:
        update_expr += ", draft_location = :d"
        vals[':d'] = {'lat': str(lat), 'lon': str(lon), 'ts': datetime.now().isoformat()}
    try: table.update_item(Key={'user_id': str(user_id)}, UpdateExpression=update_expr, ExpressionAttributeValues=vals)
    except Exception as e: print(f"DB WRITE ERROR: {e}")

# --- TOOLS ---
def confirm_saved_location(user_id, tipo):
    try:
        user = get_user_profile(user_id)
        
        # 🛡️ GATEKEEPER CHECK
        can_proceed, msg_bloqueo = check_quota_and_permissions(user, 'add_location')
        if not can_proceed:
            return msg_bloqueo

        draft = user.get('draft_location')
        if not draft: return "⚠️ No encontré la ubicación en memoria."
        
        key = tipo.lower()
        table.update_item(
            Key={'user_id': str(user_id)}, 
            UpdateExpression="set locations.#loc = :val", 
            ExpressionAttributeNames={'#loc': key}, 
            ExpressionAttributeValues={':val': {'lat': draft['lat'], 'lon': draft['lon'], 'display_name': key.capitalize(), 'active': True}}
        )
        
        # Feedback inteligente
        user_updated = get_user_profile(user_id)
        locs = user_updated.get('locations', {})
        has_casa, has_trabajo = 'casa' in locs, 'trabajo' in locs
        
        msg = f"✅ **{key.capitalize()} guardada.**"
        if has_casa and has_trabajo: msg += "\n\n🎉 **¡Perfil Completo!**\n💬 Prueba: *\"¿Cómo está el aire en Casa?\"*"
        elif key == 'casa': msg += "\n\n🏢 **Falta:** Envíame la ubicación de tu **TRABAJO**."
        elif key == 'trabajo': msg += "\n\n🏠 **Falta:** Envíame la ubicación de tu **CASA**."
        return msg
    except Exception as e: return f"Error DB: {str(e)}"

def resolve_location_key(user_id, input_name):
    user = get_user_profile(user_id)
    locs = user.get('locations', {})
    input_clean = input_name.lower()
    if input_clean in locs: return input_clean
    if "casa" in input_clean: return "casa" if "casa" in locs else None
    if "trabajo" in input_clean: return "trabajo" if "trabajo" in locs else None
    return None

def configure_ias_alert(user_id, nombre_ubicacion, umbral):
    user = get_user_profile(user_id)
    
    # 🛡️ GATEKEEPER CHECK
    can_proceed, msg_bloqueo = check_quota_and_permissions(user, 'add_alert')
    if not can_proceed: return msg_bloqueo

    key = resolve_location_key(user_id, nombre_ubicacion)
    if not key: return f"⚠️ Primero guarda '{nombre_ubicacion}'."
    
    try:
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET alerts.threshold.#loc = :val", ExpressionAttributeNames={'#loc': key}, ExpressionAttributeValues={':val': {'umbral': int(umbral), 'active': True, 'consecutive_sent': 0}})
        return f"✅ **Alerta Configurada:** Te avisaré si el IAS en **{key.capitalize()}** supera {umbral}."
    except: return "Error guardando alerta."

def configure_schedule_alert(user_id, nombre_ubicacion, hora):
    user = get_user_profile(user_id)
    
    # 🛡️ GATEKEEPER CHECK
    can_proceed, msg_bloqueo = check_quota_and_permissions(user, 'add_alert')
    if not can_proceed: return msg_bloqueo

    key = resolve_location_key(user_id, nombre_ubicacion)
    if not key: return f"⚠️ Primero guarda '{nombre_ubicacion}'."
    
    try:
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET alerts.schedule.#loc = :val", ExpressionAttributeNames={'#loc': key}, ExpressionAttributeValues={':val': {'time': str(hora), 'active': True}})
        return f"✅ **Recordatorio:** Reporte diario de **{key.capitalize()}** a las {hora}."
    except: return "Error guardando recordatorio."

# --- VISUALES (Sin cambios grandes) ---
def format_forecast_block(timeline):
    if not timeline or not isinstance(timeline, list): return "➡️ Estable"
    block = ""
    emoji_map = {"Bajo": "🟢", "Moderado": "🟡", "Alto": "🟠", "Muy Alto": "🔴", "Extremadamente Alto": "🟣"}
    count = 0
    for t in timeline:
        if count >= 4: break 
        hora = t.get('hora', '--:--')
        riesgo = t.get('riesgo', 'Bajo')
        ias = t.get('ias', 0)
        emoji = emoji_map.get(riesgo, "⚪")
        block += f"`{hora}` | {emoji} {ias} pts\n"
        count += 1
    return block.strip()

def get_official_report_time(ts_str):
    if ts_str: return ts_str[11:16]
    now = datetime.utcnow() - timedelta(hours=6)
    return now.strftime("%H:%M")

def get_time_greeting():
    h = (datetime.utcnow() - timedelta(hours=6)).hour
    return "Buenos días" if 5<=h<12 else "Buenas tardes" if 12<=h<20 else "Buenas noches"

def get_maps_url(lat, lon): return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

# --- REPORT CARD ---
def generate_report_card(user_name, location_name, lat, lon):
    try:
        url = f"{API_LIGHT_URL}?lat={lat}&lon={lon}"
        r = requests.get(url, timeout=5)
        if r.status_code != 200: return f"⚠️ Error de red ({r.status_code})."
        data = r.json()
        if data.get('status') == 'out_of_bounds': return f"📍 **Fuera de rango.** ({lat:.2f}, {lon:.2f})"

        qa = data.get('aire', {})
        meteo = data.get('meteo', {})
        ubic = data.get('ubicacion', {})
        
        ias_val = qa.get('ias', 0)
        calidad = qa.get('calidad', 'Regular')
        color_emoji = cards.get_emoji_for_quality(calidad)
        mensaje_corto = qa.get('mensaje_corto', 'Sin datos.')
        tendencia = qa.get('tendencia', 'Estable')
        forecast_block = format_forecast_block(data.get('pronostico_timeline', []))
        region_str = f"{ubic.get('mun', 'ZMVM')}, {ubic.get('edo', 'CDMX')}"

        return cards.CARD_REPORT.format(
            user_name=user_name, 
            greeting=get_time_greeting(), 
            location_name=location_name,
            maps_url=get_maps_url(lat, lon), 
            region=region_str, 
            report_time=get_official_report_time(data.get('ts')),
            ias_value=ias_val, 
            risk_category=calidad, 
            risk_circle=color_emoji, 
            natural_message=mensaje_corto,
            forecast_block=forecast_block, 
            trend_arrow=tendencia,
            health_recommendation=cards.get_health_advice(calidad),
            temp=meteo.get('tmp', 0), 
            humidity=meteo.get('rh', 0), 
            wind_speed=meteo.get('wsp', 0), 
            footer=cards.BOT_FOOTER
        )
    except Exception as e: return f"⚠️ Error visual: {str(e)}"

# --- SENDING ---
def get_inline_markup(tag):
    if tag == "CONFIRM_HOME": return {"inline_keyboard": [[{"text": "✅ Sí, es Casa", "callback_data": "SAVE_HOME"}], [{"text": "🔄 Cambiar", "callback_data": "RESET"}]]}
    if tag == "CONFIRM_WORK": return {"inline_keyboard": [[{"text": "✅ Sí, es Trabajo", "callback_data": "SAVE_WORK"}], [{"text": "🔄 Cambiar", "callback_data": "RESET"}]]}
    if tag == "SELECT_TYPE": return {"inline_keyboard": [[{"text": "🏠 Guardar Casa", "callback_data": "SAVE_HOME"}], [{"text": "🏢 Guardar Trabajo", "callback_data": "SAVE_WORK"}], [{"text": "❌ Cancelar", "callback_data": "RESET"}]]}
    return None

def send_telegram(chat_id, text, markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if markup: payload["reply_markup"] = markup
    try: requests.post(url, json=payload)
    except Exception as e: print(f"NET ERROR: {e}")

# --- HANDLER ---
def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
        
        # 1. CALLBACKS
        if 'callback_query' in body:
            cb = body['callback_query']
            chat_id = cb['message']['chat']['id']
            user_id = cb['from']['id']
            data = cb['data']
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb['id']})
            resp = ""
            if data == "SAVE_HOME": resp = confirm_saved_location(user_id, 'casa')
            elif data == "SAVE_WORK": resp = confirm_saved_location(user_id, 'trabajo')
            elif data == "RESET": resp = "🗑️ Cancelado."
            send_telegram(chat_id, resp)
            return {'statusCode': 200, 'body': 'OK'}

        # 2. MESSAGES
        if 'message' not in body: return {'statusCode': 200, 'body': 'OK'}
        msg = body['message']
        chat_id = msg['chat']['id']
        user_id = msg['from']['id']
        first_name = msg['from'].get('first_name', 'Usuario')
        
        lat, lon = None, None
        user_content = ""
        
        if 'location' in msg:
            lat, lon = msg['location']['latitude'], msg['location']['longitude']
            user_content = f"📍 [COORDENADAS RECIBIDAS]: {lat},{lon}"
        elif 'text' in msg:
            user_content = msg['text']
            
            # 🕵️‍♂️ BACKDOOR PARA PRUEBAS (PROMO CODES)
            if user_content.strip().startswith('/promo '):
                code = user_content.split(' ')[1]
                if code == "SOY_DEV_PREMIUM":
                    if update_user_status(user_id, 'PREMIUM'): send_telegram(chat_id, "💎 **¡Modo DEV activado!** Ahora eres PREMIUM.")
                    else: send_telegram(chat_id, "❌ Error DB.")
                elif code == "SOY_MORTAL":
                    if update_user_status(user_id, 'FREE'): send_telegram(chat_id, "📉 **Modo DEV desactivado.** Ahora eres FREE.")
                    else: send_telegram(chat_id, "❌ Error DB.")
                return {'statusCode': 200, 'body': 'OK'}

            if user_content=="/start": 
                send_telegram(chat_id, cards.CARD_ONBOARDING.format(user_name=first_name, footer=cards.BOT_FOOTER))
                return {'statusCode': 200, 'body': 'OK'}

        save_interaction_and_draft(user_id, first_name, lat, lon)
        user_profile = get_user_profile(user_id)
        locs = user_profile.get('locations', {})
        alerts = user_profile.get('alerts', {})
        memoria_str = "**Tus lugares:**\n" + "\n".join([f"- {v.get('display_name')}" for k, v in locs.items()])
        memoria_str += f"\n**Alertas:** {alerts}"
        memoria_str += f"\n**Plan:** {user_profile.get('subscription',{}).get('status','FREE')}"
        
        has_casa, has_trabajo = 'casa' in locs, 'trabajo' in locs
        forced_tag, system_extra = None, "NORMAL"
        
        if lat:
            if not has_casa: forced_tag = "CONFIRM_HOME"
            elif not has_trabajo: forced_tag = "CONFIRM_WORK"
            else: forced_tag = "SELECT_TYPE"
        else:
            if not has_casa: system_extra = "ONBOARDING 1: Pide CASA"
            elif not has_trabajo: system_extra = "ONBOARDING 2: Pide TRABAJO"

        gpt_msgs = [{"role": "system", "content": prompts.get_system_prompt(memoria_str, system_extra, first_name, get_official_report_time(None))}, {"role": "user", "content": user_content}]
        res = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_msgs, tools=bot_content.TOOLS_SCHEMA, tool_choice="auto", temperature=0.3)
        ai_msg = res.choices[0].message
        
        final_text = ""
        if ai_msg.tool_calls:
            gpt_msgs.append(ai_msg)
            for tc in ai_msg.tool_calls:
                fn = tc.function.name
                args = json.loads(tc.function.arguments)
                r = ""
                if fn == "confirmar_guardado": r = "Usa los botones."
                elif fn == "consultar_calidad_aire":
                    in_lat = args.get('lat', 0)
                    in_lon = args.get('lon', 0)
                    in_name = args.get('nombre_ubicacion', 'Ubicación')
                    
                    if in_lat == 0 or in_lon == 0:
                        key = resolve_location_key(user_id, in_name)
                        if key and key in locs:
                            in_lat = float(locs[key]['lat'])
                            in_lon = float(locs[key]['lon'])
                        else: r = "⚠️ No encontré coordenadas válidas para esa ubicación."
                    
                    if in_lat != 0 and in_lon != 0:
                        r = generate_report_card(first_name, in_name, in_lat, in_lon)
                    
                elif fn == "configurar_alerta_ias": r = configure_ias_alert(user_id, args['nombre_ubicacion'], args['umbral_ias'])
                elif fn == "configurar_recordatorio": r = configure_schedule_alert(user_id, args['nombre_ubicacion'], args['hora'])
                else: r = "Acción realizada."
                gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
            final_text = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_msgs, temperature=0.3).choices[0].message.content
        else:
            final_text = ai_msg.content

        markup = None
        if forced_tag:
            markup = get_inline_markup(forced_tag)
            final_text = "📍 **Ubicación recibida.**\n\n👇 Confirma:"
        
        send_telegram(chat_id, final_text, markup)
        return {'statusCode': 200, 'body': 'OK'}
    except Exception as e: return {'statusCode': 500, 'body': str(e)}
