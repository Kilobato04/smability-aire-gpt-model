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

# --- HELPER TIMEZONE ---
def get_mexico_time():
    """Retorna la hora actual en CDMX (UTC-6)"""
    return datetime.utcnow() - timedelta(hours=6)

client = OpenAI(api_key=OPENAI_API_KEY, timeout=20.0)
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

# --- 🧠 REGLAS DE NEGOCIO ---
BUSINESS_RULES = {
    "FREE": {"loc_limit": 2, "alert_limit": 0, "can_contingency": False},
    "PREMIUM": {"loc_limit": 3, "alert_limit": 10, "can_contingency": True}
}

# --- GATEKEEPER: VERIFICADOR DE CUPOS (CON LOGS) ---
def check_quota_and_permissions(user_profile, action_type):
    # 1. Identificar Plan
    sub = user_profile.get('subscription', {})
    status = sub.get('status', 'FREE')
    user_id = user_profile.get('user_id', 'unknown')
    
    rule_key = "PREMIUM" if "PREMIUM" in status.upper() else "FREE"
    rules = BUSINESS_RULES[rule_key]

    print(f"🛡️ [GATEKEEPER] User: {user_id} | Plan Detected: {status} -> Using Rules: {rule_key}")

    # 2. Validar Acción: AGREGAR UBICACIÓN
    if action_type == 'add_location':
        current_locs = len(user_profile.get('locations', {}))
        print(f"📊 [QUOTA] Locations: {current_locs} / {rules['loc_limit']}")
        
        if current_locs >= rules['loc_limit']:
            print(f"🚫 [BLOCK] Location limit reached for {user_id}")
            return False, (
                f"🛑 **Límite Alcanzado ({current_locs}/{rules['loc_limit']})**\n\n"
                "Tu plan **Básico** solo permite 2 ubicaciones guardadas.\n"
                "💎 **Cámbiate a Premium** para agregar más lugares y recibir alertas."
            )

    # 3. Validar Acción: CREAR ALERTA
    if action_type == 'add_alert':
        print(f"📊 [QUOTA] Checking Alert Permissions. Limit: {rules['alert_limit']}")
        
        if rules['alert_limit'] == 0:
            print(f"🚫 [BLOCK] Feature restricted (Alerts) for {user_id}")
            return False, (
                "🔒 **Función Premium**\n\n"
                "Las alertas automáticas (por horario o contaminación alta) son exclusivas de Smability Premium.\n"
                "💎 **Actívalo hoy por solo $49 MXN/mes.**"
            )
        
        # Contar alertas actuales
        alerts = user_profile.get('alerts', {})

        # --- 🛡️ FIX CRÍTICO AQUÍ TAMBIÉN ---
        if isinstance(alerts, str): 
            alerts = {} # Si es string, lo ignoramos y asumimos 0 alertas
        # -----------------------------------

        total_used = 0
        
        # Ahora es seguro usar .get()
        threshold_alerts = alerts.get('threshold', {})
        if isinstance(threshold_alerts, dict):
            for k, v in threshold_alerts.items():
                if isinstance(v, dict) and v.get('active'): total_used += 1
        
        schedule_alerts = alerts.get('schedule', {})
        if isinstance(schedule_alerts, dict):
            for k, v in schedule_alerts.items():
                if isinstance(v, dict) and v.get('active'): total_used += 1
            
        print(f"📊 [QUOTA] Alerts Used: {total_used} / {rules['alert_limit']}")

        if total_used >= rules['alert_limit']:
            print(f"🚫 [BLOCK] Alert limit reached for {user_id}")
            return False, f"🛑 **Has alcanzado tu límite de {rules['alert_limit']} alertas.**"

    return True, ""

# --- DB HELPERS ---
def get_user_profile(user_id):
    try: 
        return table.get_item(Key={'user_id': str(user_id)}, ConsistentRead=True).get('Item', {})
    except Exception as e:
        print(f"❌ [DB READ ERROR]: {e}")
        return {}

def update_user_status(user_id, new_status):
    print(f"🔑 [PROMO] Switching User {user_id} to {new_status}")
    try:
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET subscription = :s",
            ExpressionAttributeValues={':s': {'status': new_status, 'tier': f"{new_status}_MANUAL"}}
        )
        return True
    except Exception as e: 
        print(f"❌ [DB UPDATE ERROR]: {e}")
        return False

def save_interaction_and_draft(user_id, first_name, lat=None, lon=None):
    update_expr = "SET first_name=:n, last_interaction=:t, locations=if_not_exists(locations,:e), alerts=if_not_exists(alerts,:al), subscription=if_not_exists(subscription,:sub)"
    vals = {
        ':n': first_name, 
        ':t': datetime.now().isoformat(), 
        ':e': {}, 
        ':al': {'threshold': {}, 'schedule': {}},
        ':sub': {'status': 'FREE'}
    }
    if lat and lon:
        update_expr += ", draft_location = :d"
        vals[':d'] = {'lat': str(lat), 'lon': str(lon), 'ts': datetime.now().isoformat()}
    try: table.update_item(Key={'user_id': str(user_id)}, UpdateExpression=update_expr, ExpressionAttributeValues=vals)
    except Exception as e: print(f"❌ [DB SAVE ERROR]: {e}")

# --- TOOLS ---
def confirm_saved_location(user_id, tipo):
    try:
        user = get_user_profile(user_id)
        
        # 🛡️ GATEKEEPER CHECK
        can_proceed, msg_bloqueo = check_quota_and_permissions(user, 'add_location')
        if not can_proceed: return msg_bloqueo

        draft = user.get('draft_location')
        if not draft: return "⚠️ No encontré la ubicación en memoria."
        
        key = tipo.lower()
        print(f"💾 [ACTION] Saving Location: {key} for {user_id}")
        
        table.update_item(
            Key={'user_id': str(user_id)}, 
            UpdateExpression="set locations.#loc = :val", 
            ExpressionAttributeNames={'#loc': key}, 
            ExpressionAttributeValues={':val': {'lat': draft['lat'], 'lon': draft['lon'], 'display_name': key.capitalize(), 'active': True}}
        )
        
        user_updated = get_user_profile(user_id)
        locs = user_updated.get('locations', {})
        has_casa, has_trabajo = 'casa' in locs, 'trabajo' in locs
        
        msg = f"✅ **{key.capitalize()} guardada.**"
        if has_casa and has_trabajo: msg += "\n\n🎉 **¡Perfil Completo!**\n💬 Prueba: *\"¿Cómo está el aire en Casa?\"*"
        elif key == 'casa': msg += "\n\n🏢 **Falta:** Envíame la ubicación de tu **TRABAJO**."
        elif key == 'trabajo': msg += "\n\n🏠 **Falta:** Envíame la ubicación de tu **CASA**."
        return msg
    except Exception as e:
        print(f"❌ [TOOL ERROR]: {e}")
        return f"Error DB: {str(e)}"

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
    can_proceed, msg_bloqueo = check_quota_and_permissions(user, 'add_alert')
    if not can_proceed: return msg_bloqueo

    key = resolve_location_key(user_id, nombre_ubicacion)
    if not key: return f"⚠️ Primero guarda '{nombre_ubicacion}'."
    
    try:
        print(f"💾 [ACTION] Setting IAS Alert for {user_id} in {key} > {umbral}")
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET alerts.threshold.#loc = :val", ExpressionAttributeNames={'#loc': key}, ExpressionAttributeValues={':val': {'umbral': int(umbral), 'active': True, 'consecutive_sent': 0}})
        return f"✅ **Alerta Configurada:** Te avisaré si el IAS en **{key.capitalize()}** supera {umbral}."
    except Exception as e:
        print(f"❌ [ALERT ERROR]: {e}")
        return "Error guardando alerta."

# --- HELPER DE DÍAS (NUEVO) ---
def parse_days_input(dias_str):
    """Traduce texto natural a lista de días [0-6]"""
    if not dias_str: return [0,1,2,3,4,5,6] # Default Diario
    txt = dias_str.lower()
    
    if any(x in txt for x in ["diario", "todos", "siempre"]): return [0,1,2,3,4,5,6]
    if "fin" in txt and "semana" in txt: return [5,6]
    if "laboral" in txt or ("lunes" in txt and "viernes" in txt and "a" in txt): return [0,1,2,3,4]

    mapping = {"lun":0, "mar":1, "mie":2, "mié":2, "jue":3, "vie":4, "sab":5, "sáb":5, "dom":6}
    days = {idx for word, idx in mapping.items() if word in txt}
    return sorted(list(days)) if days else [0,1,2,3,4,5,6]

# --- FUNCIÓN ACTUALIZADA (SOPORTA DÍAS) ---
def configure_schedule_alert(user_id, nombre_ubicacion, hora, dias_str=None):
    user = get_user_profile(user_id)
    can_proceed, msg_bloqueo = check_quota_and_permissions(user, 'add_alert')
    if not can_proceed: return msg_bloqueo

    key = resolve_location_key(user_id, nombre_ubicacion)
    if not key: return f"⚠️ Primero guarda '{nombre_ubicacion}'."
    
    days_list = parse_days_input(dias_str)
    
    try:
        print(f"💾 [ACTION] Schedule {user_id} in {key} at {hora} days={days_list}")
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET alerts.schedule.#loc = :val", ExpressionAttributeNames={'#loc': key}, ExpressionAttributeValues={':val': {'time': str(hora), 'days': days_list, 'active': True}})
        
        # Formatear respuesta bonita
        from cards import format_days_text
        return f"✅ **Recordatorio:** {key.capitalize()} a las {hora} ({format_days_text(days_list)})."
    except Exception as e:
        print(f"❌ [SCHEDULE ERROR]: {e}")
        return "Error guardando recordatorio."
# --- MOTOR HNC Y VEHÍCULO (NUEVO) ---
MATRIZ_SEMANAL = {5:0, 6:0, 7:1, 8:1, 3:2, 4:2, 1:3, 2:3, 9:4, 0:4} # Key:Digito -> Val:DiaSemana (0=Lun)

def check_driving_status(plate_last_digit, hologram, date_str, contingency_phase=0):
    """Calcula si circula o no (Motor HNC)"""
    try:
        # SI LA FECHA ES "HOY" O VACÍA, USAR TIEMPO MÉXICO
        if not date_str or date_str.lower() == "hoy":
            date_str = get_mexico_time().strftime("%Y-%m-%d")
            
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_week = dt.weekday() # 0=Lun, 6=Dom
        day_month = dt.day
        holo = str(hologram).lower()
        plate = int(plate_last_digit)

        # 1. Domingo
        if day_week == 6: return True, "Es domingo, todos circulan."

        # 2. Contingencia (MVP: Asumimos Fase 0 si no hay dato, Fase 1 si phase>=1)
        if contingency_phase >= 1:
            if holo == '2': return False, "⛔ CONTINGENCIA: Holo 2 no circula."
            if holo == '1': return False, "⛔ CONTINGENCIA: Holo 1 no circula."
            if MATRIZ_SEMANAL.get(plate) == day_week: return False, "⛔ CONTINGENCIA: Tu engomado descansa hoy."

        # 3. Exentos
        if holo in ['0', '00', 'exento', 'hibrido']: return True, "Holograma Exento circula diario."

        # 4. Sábados
        if day_week == 5:
            if holo == '2' or holo == 'foraneo': return False, "🚫 Holo 2 no circula en sábado."
            if holo == '1':
                sat_idx = (day_month - 1) // 7 + 1
                is_impar = (plate % 2 != 0)
                if is_impar and sat_idx in [1, 3]: return False, f"🚫 Holo 1 Impar descansa el {sat_idx}º sábado."
                if not is_impar and sat_idx in [2, 4]: return False, f"🚫 Holo 1 Par descansa el {sat_idx}º sábado."
                if sat_idx == 5: return False, "🚫 5to Sábado: Descansan todos los Holo 1."
            return True, "✅ Tu holograma circula este sábado."

        # 5. Entre Semana
        dia_no_circula = MATRIZ_SEMANAL.get(plate)
        if dia_no_circula == day_week: return False, "🚫 Hoy no circulas por terminación de placa."
            
        return True, "✅ Puedes circular."
    except Exception as e:
        print(f"❌ Error HNC Logic: {e}")
        return False, "Error en cálculo de fecha."

# --- CONSTANTES HNC ---
VALOR_UMA_2025 = 108.57 # Actualizar cada febrero
MULTA_CDMX_MIN = VALOR_UMA_2025 * 20
MULTA_CDMX_MAX = VALOR_UMA_2025 * 30
MULTA_EDOMEX = VALOR_UMA_2025 * 20

def get_monthly_prohibited_dates(plate, holo, year, month):
    """
    Genera la lista de fechas prohibidas del mes completo.
    """
    import calendar
    prohibited_dates = []
    num_days = calendar.monthrange(year, month)[1]
    
    # Barrer todo el mes
    for day in range(1, num_days + 1):
        date_obj = datetime(year, month, day)
        date_str = date_obj.strftime("%Y-%m-%d")
        
        # Usamos tu motor existente check_driving_status
        can_drive, _ = check_driving_status(plate, holo, date_str)
        
        if not can_drive:
            # Formato bonito: "Lun 03", "Sáb 15"
            dias_abr = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
            prohibited_dates.append(f"• {dias_abr[date_obj.weekday()]} {day}")
            
    return prohibited_dates

def get_restriction_summary(plate, holo):
    """Calcula texto genérico de reglas (ej. 'Todos los Lunes')"""
    plate = int(plate)
    holo = str(holo).lower()
    
    # Texto Semanal
    dias_map = {0:"Lunes", 1:"Martes", 2:"Miércoles", 3:"Jueves", 4:"Viernes"}
    dia_idx = MATRIZ_SEMANAL.get(plate)
    texto_semanal = f"• Todos los **{dias_map[dia_idx]}**"
    
    # Texto Sábados
    texto_sabados = "• Ningún sábado" # Default para Holo 2
    if holo == '1':
        es_impar = (plate % 2 != 0)
        texto_sabados = "• Sábados: **1º y 3º** (Impares)" if es_impar else "• Sábados: **2º y 4º** (Pares)"
    elif holo in ['0', '00', 'exento']:
        texto_semanal = "• Ninguno (Exento)"
        texto_sabados = "• Ninguno"
        
    return texto_semanal, texto_sabados

def save_vehicle_profile(user_id, digit, hologram):
    """Guarda auto y calcula color de engomado"""
    try:
        digit = int(digit)
        holo = str(hologram).lower().replace("holograma", "").strip()
        colors = {5:"Amarillo", 6:"Amarillo", 7:"Rosa", 8:"Rosa", 3:"Rojo", 4:"Rojo", 1:"Verde", 2:"Verde", 9:"Azul", 0:"Azul"}
        color = colors.get(digit, "Desconocido")
        
        vehicle_data = {
            "active": True,
            "plate_last_digit": digit,
            "hologram": holo,
            "engomado": color,
            "updated_at": datetime.now().isoformat(),
            "alert_config": {"enabled": True, "time": "20:00"}
        }
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET vehicle = :v", ExpressionAttributeValues={':v': vehicle_data})
        return f"✅ Auto guardado: Terminación {digit} (Engomado {color}), Holograma {holo.upper()}. Alertas HNC activadas."
    except Exception as e:
        print(f"❌ Error Saving Vehicle: {e}")
        return "Error al guardar el vehículo."

# --- VISUALES ---
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
        print(f"🔌 [API CALL] {url}") # LOG CRITICO DE RED
        
        r = requests.get(url, timeout=10)
        if r.status_code != 200: 
            print(f"❌ [API FAIL] Status: {r.status_code}")
            return f"⚠️ Error de red ({r.status_code})."
        
        data = r.json()
        if data.get('status') == 'out_of_bounds': 
            print(f"⚠️ [API BOUNDS] Coordinates out of range: {lat}, {lon}")
            return f"📍 **Fuera de rango.** ({lat:.2f}, {lon:.2f})"

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
    except Exception as e: 
        print(f"❌ [VISUAL ERROR]: {e}")
        return f"⚠️ Error visual: {str(e)}"

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
    try: 
        r = requests.post(url, json=payload)
        if r.status_code != 200: print(f"❌ [TG FAIL] {r.text}")
    except Exception as e: print(f"❌ [TG NET ERROR]: {e}")

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
            print(f"👆 [CALLBACK] User: {user_id} | Data: {data}") # LOG
            
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb['id']})
            resp = ""
            if data == "SAVE_HOME": resp = confirm_saved_location(user_id, 'casa')
            elif data == "SAVE_WORK": resp = confirm_saved_location(user_id, 'trabajo')
            elif data == "RESET": resp = "🗑️ Cancelado."
            # --- AGREGAR ESTO (Lógica de Botones Resumen) ---
            elif data in ["CHECK_HOME", "CHECK_WORK"]:
                user = get_user_profile(user_id)
                locs = user.get('locations', {})
                key = 'casa' if data == "CHECK_HOME" else 'trabajo'
                
                if key in locs:
                    lat, lon = float(locs[key]['lat']), float(locs[key]['lon'])
                    first_name = cb['from'].get('first_name', 'Usuario')
                    # Generamos reporte visual
                    report_card = generate_report_card(first_name, key.capitalize(), lat, lon)
                    send_telegram(chat_id, report_card)
                    return {'statusCode': 200, 'body': 'OK'} # Salimos para no enviar resp texto
                else:
                    resp = f"⚠️ No tienes ubicación de {key} guardada."
            
            # Enviar respuesta de texto simple si no fue reporte
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
            user_content = f"📍 [COORDS]: {lat},{lon}"
        elif 'text' in msg:
            user_content = msg['text']
            
            # 🕵️‍♂️ BACKDOOR
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
                print(f"🆕 [START] User: {user_id}")
                send_telegram(chat_id, cards.CARD_ONBOARDING.format(user_name=first_name, footer=cards.BOT_FOOTER))
                return {'statusCode': 200, 'body': 'OK'}

        print(f"📨 [MSG] User: {user_id} | Content: {user_content}") # LOG CRITICO

        save_interaction_and_draft(user_id, first_name, lat, lon)
        user_profile = get_user_profile(user_id)
        # Parche de seguridad
        if isinstance(user_profile.get('alerts'), str): user_profile['alerts'] = {}
        
        # Preparar memoria para GPT
        locs = user_profile.get('locations', {})
        if isinstance(locs, str): locs = {} 
        alerts = user_profile.get('alerts', {})
        if isinstance(alerts, str): alerts = {} 
        
        # --- FIX: RECUPERAR DATOS DEL AUTO ---
        veh = user_profile.get('vehicle', {})
        veh_info = "No registrado"
        if veh and veh.get('active'):
            veh_info = f"Placa terminación {veh.get('plate_last_digit')} (Holo {veh.get('hologram')})"
        # -------------------------------------

        plan_status = user_profile.get('subscription',{}).get('status','FREE')
        
        memoria_str = "**Tus lugares:**\n" + "\n".join([f"- {v.get('display_name')}" for k, v in locs.items()])
        memoria_str += f"\n**Auto:** {veh_info}" # <--- AQUÍ SE LO RECORDAMOS
        memoria_str += f"\n**Alertas:** {alerts}"
        memoria_str += f"\n**Plan:** {plan_status}"
        
        has_casa, has_trabajo = 'casa' in locs, 'trabajo' in locs
        forced_tag, system_extra = None, "NORMAL"
        
        if lat:
            if not has_casa: forced_tag = "CONFIRM_HOME"
            elif not has_trabajo: forced_tag = "CONFIRM_WORK"
            else: forced_tag = "SELECT_TYPE"
        else:
            if not has_casa: system_extra = "ONBOARDING 1: Pide CASA"
            elif not has_trabajo: system_extra = "ONBOARDING 2: Pide TRABAJO"

        now_mx = get_mexico_time()
        fecha_str = now_mx.strftime("%Y-%m-%d") # Ej: 2026-02-03
        hora_str = now_mx.strftime("%H:%M")     # Ej: 19:45

        # Llamada Actualizada al Prompt (5 argumentos)
        gpt_msgs = [
            {"role": "system", "content": prompts.get_system_prompt(memoria_str, system_extra, first_name, hora_str, fecha_str)}, 
            {"role": "user", "content": user_content}
        ]
        
        print(f"🤖 [GPT] Calling OpenAI... (Date: {fecha_str})")
        
        res = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_msgs, tools=bot_content.TOOLS_SCHEMA, tool_choice="auto", temperature=0.3)
        ai_msg = res.choices[0].message
        
        final_text = ""
        if ai_msg.tool_calls:
            print(f"🛠️ [TOOL] GPT wants to call: {len(ai_msg.tool_calls)} tools")
            gpt_msgs.append(ai_msg)
            for tc in ai_msg.tool_calls:
                fn = tc.function.name
                args = json.loads(tc.function.arguments)
                print(f"🔧 [EXEC] Tool: {fn} | Args: {args}")
                
                r = ""
                if fn == "confirmar_guardado": r = "Usa los botones."
                elif fn == "consultar_calidad_aire":
                    in_lat = args.get('lat', 0)
                    in_lon = args.get('lon', 0)
                    in_name = args.get('nombre_ubicacion', 'Ubicación')
                    
                    # 1. Intentar resolver coordenadas si vienen vacías
                    if in_lat == 0 or in_lon == 0:
                        key = resolve_location_key(user_id, in_name)
                        if key and key in locs:
                            in_lat = float(locs[key]['lat'])
                            in_lon = float(locs[key]['lon'])
                    
                    # 2. DECISIÓN: ¿Tenemos datos válidos?
                    if in_lat != 0 and in_lon != 0:
                        # ✅ ÉXITO: Generamos tarjeta, enviamos y CORTAMOS (Hard Stop)
                        # Esto soluciona el Error 400 porque ya no llamamos a OpenAI de nuevo
                        r = generate_report_card(first_name, in_name, in_lat, in_lon)
                        send_telegram(chat_id, r)
                        return {'statusCode': 200, 'body': 'OK'}
                    else:
                        # ❌ FALLO: No hay coordenadas. Avisamos al LLM para que pregunte al usuario.
                        r = f"⚠️ No encontré coordenadas para '{in_name}'. Pide al usuario que guarde la ubicación o envíe su ubicación actual."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                        # Aquí NO hacemos return, dejamos que el flujo baje para que GPT explique el error en texto.
                    
                elif fn == "configurar_alerta_ias": 
                    r = configure_ias_alert(user_id, args['nombre_ubicacion'], args['umbral_ias'])
                    # IMPORTANTE: Avisar al LLM que ya se hizo
                    gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})

                elif fn == "configurar_recordatorio": 
                    # AHORA PASAMOS EL ARGUMENTO 'dias' QUE VIENE DEL LLM
                    r = configure_schedule_alert(user_id, args['nombre_ubicacion'], args['hora'], args.get('dias'))
                    # IMPORTANTE: Avisar al LLM que ya se hizo
                    gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})

                elif fn == "consultar_resumen_configuracion":
                    # GATEKEEPER DE RESUMEN
                    user = get_user_profile(user_id)
                    status = user.get('subscription', {}).get('status', 'FREE')
                    
                    if "PREMIUM" not in status.upper() and "TRIAL" not in status.upper():
                        # CASO FREE: Dejamos que el LLM explique
                        r = "🚫 El usuario es FREE. Dile amablemente que no tiene alertas activas y que requiere Premium."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                    else:
                        # CASO PREMIUM: Generamos la tarjeta visual
                        r = cards.generate_summary_card(
                            first_name, 
                            user.get('alerts', {}), 
                            user.get('vehicle', None), 
                            user.get('exposure_profile', None)
                        )
                        markup = cards.get_summary_buttons(
                            'casa' in user.get('locations',{}), 
                            'trabajo' in user.get('locations',{})
                        )
                        
                        # 1. Enviar tarjeta visual
                        send_telegram(chat_id, r, markup)
                        
                        # 2. 🛑 HARD STOP: Detenemos la Lambda aquí.
                        return {'statusCode': 200, 'body': 'OK'}

                # --- NUEVOS BLOQUES HNC (PEGAR AQUÍ) ---
                elif fn == "configurar_hora_alerta_auto":
                    new_time = args.get('nueva_hora')
                    # Validación simple de formato HH:MM
                    if not new_time or ":" not in new_time or len(new_time) > 5:
                        r = "⚠️ Formato inválido. Usa HH:MM (ej. 07:00)."
                    else:
                        try:
                            # Actualizar solo la hora y activar la alerta
                            table.update_item(
                                Key={'user_id': str(user_id)},
                                UpdateExpression="SET vehicle.alert_config.#t = :t, vehicle.alert_config.enabled = :e",
                                ExpressionAttributeNames={'#t': 'time'},
                                ExpressionAttributeValues={':t': new_time, ':e': True}
                            )
                            r = f"✅ **Configurado.** Te avisaré a las **{new_time} hrs** si no circulas al día siguiente."
                        except Exception as e:
                            print(f"❌ Error update time: {e}")
                            r = "Error al guardar la hora en la base de datos."
                    
                    # ⚠️ FIX: AGREGAR ESTA LÍNEA (FALTABA)
                    gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                    
                    # Recordatorio: Aquí usamos la lógica 'should_append_result = True' (default)
                    # para que se agregue al historial al final del bucle.
                
                elif fn == "configurar_auto":
                    digit = args.get('ultimo_digito')
                    holo = args.get('hologram') or args.get('holograma')
                    
                    if digit is None or holo is None:
                        r = "⚠️ Faltan datos. Necesito último dígito y holograma."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                    else:
                        # 1. GUARDAR (Acción Silenciosa)
                        save_resp = save_vehicle_profile(user_id, digit, holo)
                        
                        # 2. GENERAR REPORTE MENSUAL (Acción Visible)
                        now = datetime.now()
                        lista_dias = get_monthly_prohibited_dates(digit, holo, now.year, now.month)
                        txt_sem, txt_sab = get_restriction_summary(digit, holo)
                        
                        # Colores y Multas
                        colors = {5:"Amarillo", 6:"Amarillo", 7:"Rosa", 8:"Rosa", 3:"Rojo", 4:"Rojo", 1:"Verde", 2:"Verde", 9:"Azul", 0:"Azul"}
                        
                        # --- CÁLCULO DEL MES (Hacerlo ANTES del format) ---
                        meses_es = {1:"Enero", 2:"Febrero", 3:"Marzo", 4:"Abril", 5:"Mayo", 6:"Junio", 7:"Julio", 8:"Agosto", 9:"Septiembre", 10:"Octubre", 11:"Noviembre", 12:"Diciembre"}
                        nombre_mes_actual = meses_es[now.month]

                        # Formatear Tarjeta
                        card = cards.CARD_HNC_DETAILED.format(
                            mes_nombre=nombre_mes_actual,  # Aquí pasamos la variable ya lista
                            plate=digit,
                            color=colors.get(int(digit), ""),
                            holo=str(holo).upper(),
                            dias_semana_txt=txt_sem,
                            sabados_txt=txt_sab,
                            lista_fechas="\n".join(lista_dias) if lista_dias else "¡Circulas todo el mes! 🎉",
                            multa_cdmx=f"${MULTA_CDMX_MIN:,.0f} - ${MULTA_CDMX_MAX:,.0f}",
                            multa_edomex=f"${MULTA_EDOMEX:,.0f}",
                            footer=cards.BOT_FOOTER
                        )
                        
                        # 3. ENVIAR Y CORTAR
                        send_telegram(chat_id, f"✅ **Datos guardados.** Aquí tienes tu proyección del mes:\n\n{save_resp}") # Confirmación texto breve
                        send_telegram(chat_id, card) # Tarjeta detallada
                        return {'statusCode': 200, 'body': 'OK'}

                elif fn == "consultar_hoy_no_circula":
                    user = get_user_profile(user_id)
                    veh = user.get('vehicle')
                    
                    if not veh or not veh.get('active'):
                        r = "⚠️ No tienes auto configurado. Dime algo como: *'Mi auto es placas 555 y holograma 0'*."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                    else:
                        plate = veh.get('plate_last_digit')
                        holo = veh.get('hologram')
                        # Default a 'hoy' si no especifica fecha, pero el prompt suele mandar fecha
                        fecha = args.get('fecha_referencia', datetime.now().strftime("%Y-%m-%d"))
                        
                        can_drive, reason = check_driving_status(plate, holo, fecha)
                        
                        # Visuales
                        dt_obj = datetime.strptime(fecha, "%Y-%m-%d")
                        dias_map = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
                        status_emoji = "✅" if can_drive else "⛔"
                        status_title = "PUEDES CIRCULAR" if can_drive else "NO CIRCULAS"
                        status_msg = "¡Vámonos! Tu auto está libre." if can_drive else "Evita multas, déjalo en casa."
                        
                        card = cards.CARD_HNC_RESULT.format(
                            fecha_str=fecha,
                            dia_semana=dias_map[dt_obj.weekday()],
                            plate_info=f"Terminación {plate}",
                            hologram=holo,
                            status_emoji=status_emoji,
                            status_title=status_title,
                            status_message=status_msg,
                            reason=reason,
                            footer=cards.BOT_FOOTER
                        )
                        send_telegram(chat_id, card)
                        return {'statusCode': 200, 'body': 'OK'} # Hard Stop

                else: 
                    # Para cualquier otra tool genérica no contemplada arriba
                    r = "Acción realizada."
                    gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
            
            # --- FINAL DEL PROCESAMIENTO DE TOOLS ---
            # Solo llegamos aquí si NO hubo un Hard Stop (return)
            final_text = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_msgs, temperature=0.3).choices[0].message.content
        else:
            # Si no hubo tools, usamos el contenido directo
            final_text = ai_msg.content

        markup = None
        if forced_tag:
            markup = get_inline_markup(forced_tag)
            final_text = "📍 **Ubicación recibida.**\n\n👇 Confirma:"
        
        send_telegram(chat_id, final_text, markup)
        return {'statusCode': 200, 'body': 'OK'}
        
    except Exception as e:
        print(f"🔥 [CRITICAL FAIL]: {e}")
        return {'statusCode': 500, 'body': str(e)}
