import json
import os
import requests
import boto3
from datetime import datetime, timedelta
from openai import OpenAI
import bot_content
import cards
import prompts
import math
from decimal import Decimal

# --- CONFIG ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
API_LIGHT_URL = os.environ.get('API_LIGHT_URL', 'https://vuy3dprsp2udtuelnrb5leg6ay0ygsky.lambda-url.us-east-1.on.aws/')
DYNAMODB_TABLE = 'SmabilityUsers'

# --- HELPER TIMEZONE ---
def get_mexico_time():
    """Retorna la hora actual en CDMX (UTC-6)"""
    return datetime.utcnow() - timedelta(hours=6)

# --- HELPER TEXTO (PONER ESTO ANTES) ---
def normalize_key(text):
    """Quita acentos y espacios para usar como llave en BD"""
    if not text: return ""
    text = text.lower().strip().replace(" ", "_")
    replacements = (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"))
    for a, b in replacements:
        text = text.replace(a, b)
    return text


def get_verification_deadline(period_txt):
    """Extrae el mes límite del texto del periodo de forma segura"""
    if not period_txt or "EXENTO" in period_txt or "Revisar" in period_txt: 
        return "N/A"
        
    # Lógica de fechas límite basada en el segundo bimestre
    if "Feb" in period_txt: return "28 Feb / 31 Ago"
    if "Mar" in period_txt: return "31 Mar / 30 Sep"
    if "Abr" in period_txt: return "30 Abr / 31 Oct"
    if "May" in period_txt: return "31 May / 30 Nov"
    if "Jun" in period_txt: return "30 Jun / 31 Dic"
    
    return "N/A"

client = OpenAI(api_key=OPENAI_API_KEY, timeout=20.0)
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

# --- 🧠 REGLAS DE NEGOCIO ---
BUSINESS_RULES = {
    "FREE": {"loc_limit": 1, "alert_limit": 0, "can_contingency": False},
    "PREMIUM": {"loc_limit": 3, "alert_limit": 10, "can_contingency": True}
}

# --- GATEKEEPER: VERIFICADOR DE CUPOS (VERSIÓN DESBLOQUEADA) ---
def check_quota_and_permissions(user_profile, action_type):
    # 1. Identificar Plan
    sub = user_profile.get('subscription', {})
    status = sub.get('status', 'FREE')
    user_id = user_profile.get('user_id', 'unknown')
    
    # Flags de Negocio
    is_premium = "PREMIUM" in status.upper() or "TRIAL" in status.upper()
    LIMIT_LOC_FREE = 1
    LIMIT_LOC_PREM = 3
    
    print(f"🛡️ [GATEKEEPER] User: {user_id} | Plan: {status} | Premium: {is_premium}")

    # 2. Validar Acción: AGREGAR UBICACIÓN
    if action_type == 'add_location':
        current_locs = len(user_profile.get('locations', {}))
        limit = LIMIT_LOC_PREM if is_premium else LIMIT_LOC_FREE
        
        if current_locs >= limit:
            if not is_premium:
                return False, f"🛑 **Límite Alcanzado ({current_locs}/{limit})**\n\nTu plan Básico solo permite 1 ubicación.\n💎 **Hazte Premium** para guardar hasta 3."
            else:
                return False, f"🛑 **Espacios Llenos.** Tienes ocupados tus {limit} espacios. Borra uno para agregar otro."

    # 3. Validar Acción: CREAR ALERTA (Schedule o Threshold)
    if action_type == 'add_alert':
        # REGLA SIMPLE: Free = 0 alertas auto. Premium = Ilimitadas (dentro de lo lógico).
        if not is_premium:
             return False, (
                "🔒 **Función Premium**\n\n"
                "Las alertas automáticas (diarias o por contaminación) son exclusivas de Smability Premium.\n"
                "💎 **Actívalo hoy por solo $49 MXN/mes.**"
            )
        
        # Si es Premium, ¡Pase usted! 
        # No ponemos límite numérico porque la estructura de la DB (1 por key) ya evita el abuso.
        return True, ""

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

# --- FIX: PERSISTENCIA DEL DRAFT (Sustituye tu función actual por esta) ---
def save_interaction_and_draft(user_id, first_name, lat=None, lon=None):
    update_expr = "SET first_name=:n, last_interaction=:t, locations=if_not_exists(locations,:e), alerts=if_not_exists(alerts,:al), subscription=if_not_exists(subscription,:sub)"
    vals = {
        ':n': first_name, 
        ':t': datetime.now().isoformat(), 
        ':e': {}, 
        ':al': {'threshold': {}, 'schedule': {}},
        ':sub': {'status': 'FREE'}
    }
    
    # OJO: Solo tocamos 'draft_location' si realmente recibimos coordenadas nuevas
    # Esto evita que un mensaje de texto ("Gym") borre las coordenadas pendientes.
    if lat and lon:
        update_expr += ", draft_location = :d"
        vals[':d'] = {'lat': str(lat), 'lon': str(lon), 'ts': datetime.now().isoformat()}
    
    try: table.update_item(Key={'user_id': str(user_id)}, UpdateExpression=update_expr, ExpressionAttributeValues=vals)
    except Exception as e: print(f"❌ [DB SAVE ERROR]: {e}")

def delete_location_from_db(user_id, location_name):
    """
    Borra ubicación Y sus alertas asociadas (Cascading Delete).
    Elimina: locations.key, alerts.threshold.key, alerts.schedule.key
    """
    key = location_name.lower().strip()
    try:
        table.update_item(
            Key={'user_id': str(user_id)},
            # BORRADO TRIPLE EN UNA SOLA OPERACIÓN ATÓMICA
            UpdateExpression="REMOVE locations.#k, alerts.threshold.#k, alerts.schedule.#k",
            ExpressionAttributeNames={'#k': key},
            ReturnValues="UPDATED_NEW"
        )
        return True
    except Exception as e:
        print(f"❌ Error deleting location cascade: {e}")
        return False

def rename_location_in_db(user_id, old_name, new_name):
    """
    Cambia la llave en DynamoDB para que el motor matemático la reconozca.
    Ej. 'ecatepec' -> 'trabajo'
    """
    old_key = normalize_key(old_name)
    new_key = normalize_key(new_name)

    user = get_user_profile(user_id)
    locs = user.get('locations', {})
    
    if old_key not in locs:
        return False, f"⚠️ No encontré la ubicación '{old_name}' en tu perfil."
        
    if new_key in locs:
        return False, f"⚠️ Ya tienes una ubicación llamada '{new_name}'. Por favor bórrala primero."

    # 1. Extraemos los datos de la ubicación vieja
    loc_data = locs[old_key]
    loc_data['display_name'] = new_name.strip().capitalize() # Actualizamos el nombre visual
    
    # 2. Guardamos la nueva llave y borramos la vieja (en cascada)
    update_expr = "SET locations.#newk = :val REMOVE locations.#oldk, alerts.threshold.#oldk, alerts.schedule.#oldk"
    
    try:
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression=update_expr,
            ExpressionAttributeNames={'#newk': new_key, '#oldk': old_key},
            ExpressionAttributeValues={':val': loc_data}
        )
        return True, f"✅ Listo. He renombrado '{old_name}' a '{new_name}'."
    except Exception as e:
        print(f"❌ Error rename DB: {e}")
        return False, "⚠️ Hubo un error al actualizar la base de datos."


class CalculadoraRiesgoSmability:
    def __init__(self):
        self.K_CIGARRO = 22.0  
        self.K_O3_A_PM = 0.5   
        self.K_ENVEJECIMIENTO = 2.0  
        self.FACTORES_TRANSPORTE = {
            "auto_ac": 0.4, "suburbano": 0.5, "cablebus": 0.7,
            "metro": 0.8, "metrobus": 0.9, "auto_ventana": 1.0,
            "combi": 1.2, "caminar": 1.3, "bicicleta": 1.5, "home_office": 1.0
        }

    def calcular_usuario(self, vector_casa, perfil_usuario, vector_trabajo=None, es_home_office=False):
        if es_home_office or not vector_trabajo:
            vector_trabajo = vector_casa
            hora_salida, hora_llegada_casa, factor_transporte = 25, 25, 1.0
        else:
            hora_salida = 7  
            duracion_traslado = float(perfil_usuario.get('tiempo_traslado_horas', 2)) # <--- FIX AQUÍ
            mitad_traslado = math.ceil(duracion_traslado / 2.0)
            hora_llegada_trabajo = hora_salida + mitad_traslado
            hora_salida_trabajo = 18 
            hora_llegada_casa = hora_salida_trabajo + mitad_traslado
            modo_transporte = perfil_usuario.get('transporte_default', 'auto_ventana')
            factor_transporte = self.FACTORES_TRANSPORTE.get(modo_transporte, 1.0)

        suma_exposicion_acumulada = 0.0
        
        for hora in range(24):
            riesgo_casa = vector_casa['pm25_12h'][hora] + (vector_casa['o3_1h'][hora] * self.K_O3_A_PM)
            riesgo_trabajo = vector_trabajo['pm25_12h'][hora] + (vector_trabajo['o3_1h'][hora] * self.K_O3_A_PM)

            if es_home_office:
                nivel_hora = riesgo_casa
            else:
                if hora < hora_salida or hora >= hora_llegada_casa:
                    nivel_hora = riesgo_casa 
                elif hora_salida <= hora < hora_llegada_trabajo:
                    nivel_hora = ((riesgo_casa + riesgo_trabajo) / 2) * factor_transporte
                elif hora_llegada_trabajo <= hora < hora_salida_trabajo:
                    nivel_hora = riesgo_trabajo 
                elif hora_salida_trabajo <= hora < hora_llegada_casa:
                    nivel_hora = ((riesgo_casa + riesgo_trabajo) / 2) * factor_transporte 

            suma_exposicion_acumulada += nivel_hora

        promedio = suma_exposicion_acumulada / 24.0
        cigarros = promedio / self.K_CIGARRO
        
        return {
            "cigarros": round(cigarros, 1), 
            "dias_perdidos": round(cigarros * self.K_ENVEJECIMIENTO, 1),
            "promedio_riesgo": round(promedio, 1)
        }

# --- TOOLS ---
def confirm_saved_location(user_id, tipo):
    try:
        user = get_user_profile(user_id)
        draft = user.get('draft_location')
        
        # Validación de seguridad: Si no hay mapa, no guardamos nada.
        if not draft: return "⚠️ No encontré coordenadas recientes. Por favor toca el clip 📎 y envía la ubicación de nuevo."
        
        # 1. Normalización Robusta (Zócalo -> zocalo)
        # IMPORTANTE: Asegúrate de haber agregado la función 'normalize_key' arriba (FIX 1)
        key = normalize_key(tipo)
        display_name = tipo.strip().capitalize() # Mantiene tilde visualmente (Zócalo)

        locs = user.get('locations', {})
        is_new = key not in locs
        
        # 2. Gatekeeper (Límite de ubicaciones)
        if is_new:
            can_proceed, msg_bloqueo = check_quota_and_permissions(user, 'add_location')
            if not can_proceed: return msg_bloqueo
        
        # 3. Query: Guardamos y BORRAMOS el draft para no reusarlo por error
        # "alerts..." borra basura vieja. "draft_location" borra el mapa usado.
        if is_new: 
            update_expr = "SET locations.#loc = :val REMOVE alerts.threshold.#loc, alerts.schedule.#loc, draft_location"
        else: 
            update_expr = "SET locations.#loc = :val REMOVE draft_location"

        table.update_item(
            Key={'user_id': str(user_id)}, 
            UpdateExpression=update_expr, 
            ExpressionAttributeNames={'#loc': key}, 
            ExpressionAttributeValues={':val': {
                'lat': draft['lat'], 'lon': draft['lon'], 'display_name': display_name, 'active': True
            }}
        )
        
        # 4. Confirmación
        user = get_user_profile(user_id)
        count = len(user.get('locations', {}))
        msg = f"✅ **{display_name} guardada.**"
        if count >= 2: msg += f"\n\n🎉 **Tienes {count} lugares guardados.**"
        
        return msg

    except Exception as e:
        print(f"❌ [TOOL ERROR]: {e}")
        return f"Error al guardar: {str(e)}"

# --- HELPER DE BÚSQUEDA ---
def resolve_location_key(user_id, input_name):
    user = get_user_profile(user_id)
    locs = user.get('locations', {})
    
    # 1. Búsqueda exacta normalizada (zocalo == zocalo)
    target = normalize_key(input_name)
    if target in locs: return target
    
    # 2. Búsqueda inteligente (alias comunes)
    if "casa" in target and "casa" in locs: return "casa"
    if "trabajo" in target and "trabajo" in locs: return "trabajo"
    if "oficina" in target and "trabajo" in locs: return "trabajo"
    
    # 3. Búsqueda parcial (ej. usuario dice "el zocalo" -> encuentra "zocalo")
    for k in locs.keys():
        if k in target or target in k:
            return k
            
    return None

def configure_ias_alert(user_id, nombre_ubicacion, umbral):
    # --- 🔒 CANDADO DE CALIDAD: MÍNIMO 100 ---
    # Validamos antes de cualquier otra cosa para educar al usuario
    try:
        umbral_int = int(umbral)
        if umbral_int < 100:
            return "⚠️ **Umbral muy bajo.**\n\nPara que la alerta sea útil (Emergencia), el mínimo es **100 puntos** (Calidad Mala).\n\nPor favor, elige un valor de 100 o más."
    except ValueError:
        return "⚠️ El umbral debe ser un número entero (ej. 100, 150)."
    # -----------------------------------------

    user = get_user_profile(user_id)
    can_proceed, msg_bloqueo = check_quota_and_permissions(user, 'add_alert')
    if not can_proceed: return msg_bloqueo

    key = resolve_location_key(user_id, nombre_ubicacion)
    if not key: return f"⚠️ Primero guarda '{nombre_ubicacion}'."
    
    try:
        print(f"💾 [ACTION] Setting IAS Alert for {user_id} in {key} > {umbral_int}")
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET alerts.threshold.#loc = :val",
            ExpressionAttributeNames={'#loc': key},
            ExpressionAttributeValues={':val': {'umbral': umbral_int, 'active': True, 'consecutive_sent': 0}}
        )
        return f"✅ **Alerta Configurada:** Te avisaré si el IAS en **{key.capitalize()}** supera {umbral_int}."
    except Exception as e:
        print(f"❌ [ALERT ERROR]: {e}")
        return "Error guardando alerta."

def toggle_contingency_alert(user_id, activar):
    """Activa/Desactiva el flag de contingencia en DynamoDB"""
    try:
        # Guardamos el estado en alerts.contingency (Booleano)
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET alerts.contingency = :val",
            ExpressionAttributeValues={':val': bool(activar)}
        )
        estado = "✅ ACTIVADA" if activar else "🔕 DESACTIVADA"
        return f"Enterado. La alerta de contingencia ha sido {estado}."
    except Exception as e:
        print(f"❌ Error toggle contingency: {e}")
        return "Hubo un error al actualizar tu preferencia."

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
    # --- 🔒 VALIDACIÓN DE HORARIO (6:00 AM - 11:00 PM) ---
    # Sincronizado con el Scheduler para no prometer reportes que no saldrán
    try:
        # Extraemos la hora del string "HH:MM"
        parts = hora.split(':')
        h_int = int(parts[0])
        
        # Si es antes de las 6am o después de las 11pm (23h)
        if h_int < 6 or h_int > 23:
            return (
                f"⚠️ **Horario fuera de rango.**\n\n"
                f"Los reportes de calidad del aire solo están disponibles entre las **06:00 AM** y las **11:00 PM**.\n\n"
                "Por favor, elige una hora dentro de este horario operativo."
            )
    except Exception:
        return "⚠️ Formato de hora inválido. Intenta de nuevo (ej. 07:00)."
    # -----------------------------------------------------

    user = get_user_profile(user_id)
    can_proceed, msg_bloqueo = check_quota_and_permissions(user, 'add_alert')
    if not can_proceed: return msg_bloqueo

    key = resolve_location_key(user_id, nombre_ubicacion)
    if not key: return f"⚠️ Primero guarda '{nombre_ubicacion}'."
    
    # Reutilizamos tu helper de parseo de días
    days_list = parse_days_input(dias_str)
    
    try:
        print(f"💾 [ACTION] Schedule {user_id} in {key} at {hora} days={days_list}")
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET alerts.schedule.#loc = :val",
            ExpressionAttributeNames={'#loc': key},
            ExpressionAttributeValues={':val': {'time': str(hora), 'days': days_list, 'active': True}}
        )
        
        # Importamos el formateador visual
        from cards import format_days_text
        return f"✅ **Recordatorio:** {key.capitalize()} a las {hora} ({format_days_text(days_list)})."
    except Exception as e:
        print(f"❌ [SCHEDULE ERROR]: {e}")
        return "Error guardando recordatorio."

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
        can_drive, _, _ = cards.check_driving_status(plate, holo, date_str)
        
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
    dia_idx = cards.MATRIZ_SEMANAL.get(plate) # <--- FIX AQUÍ
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

def get_official_report_time(ts_str):
    if ts_str: return ts_str[11:16]
    now = datetime.utcnow() - timedelta(hours=6)
    return now.strftime("%H:%M")

def get_time_greeting():
    h = (datetime.utcnow() - timedelta(hours=6)).hour
    return "Buenos días" if 5<=h<12 else "Buenas tardes" if 12<=h<20 else "Buenas noches"

def get_maps_url(lat, lon): return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

# --- REPORTE DIARIO CON PÍLDORA HNC Y VERIFICACIÓN INTEGRADAS ---
def generate_report_card(user_name, location_name, lat, lon, vehicle=None, contingency_phase="None"):
    try:
        url = f"{API_LIGHT_URL}?lat={lat}&lon={lon}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200: return f"⚠️ Error de red ({r.status_code})."
        
        data = r.json()
        if data.get('status') == 'out_of_bounds': return f"📍 **Fuera de rango.** ({lat:.2f}, {lon:.2f})"

        qa, meteo, ubic = data.get('aire', {}), data.get('meteo', {}), data.get('ubicacion', {})
        calidad = qa.get('calidad', 'Regular')
        
        # Inyección HNC centralizada desde cards.py
        hnc_pill = cards.build_hnc_pill(vehicle, contingency_phase)
        combined_footer = f"{hnc_pill}\n\n{cards.BOT_FOOTER}" if hnc_pill else cards.BOT_FOOTER

        return cards.CARD_REPORT.format(
            user_name=user_name, greeting=get_time_greeting(), location_name=location_name,
            maps_url=get_maps_url(lat, lon), region=f"{ubic.get('mun', 'ZMVM')}, {ubic.get('edo', 'CDMX')}",
            report_time=get_official_report_time(data.get('ts')), ias_value=qa.get('ias', 0),
            risk_category=calidad, risk_circle=cards.get_emoji_for_quality(calidad),
            pollutant=qa.get('dominante', 'N/A'),
            forecast_block=cards.format_forecast_block(data.get('pronostico_timeline', [])),
            health_recommendation=cards.get_health_advice(calidad), # Ya no requiere el perfil de salud aquí si no lo pasas, o puedes pasarle 'Ninguno'
            temp=meteo.get('tmp', 0), humidity=meteo.get('rh', 0), wind_speed=meteo.get('wsp', 0),
            footer=combined_footer
        )
    except Exception as e: return f"⚠️ Error visual: {str(e)}"

# --- SENDING ---
def get_inline_markup(tag):
    if tag == "CONFIRM_HOME": return {"inline_keyboard": [[{"text": "✅ Sí, es Casa", "callback_data": "SAVE_HOME"}], [{"text": "🔄 Cambiar", "callback_data": "RESET"}]]}
    if tag == "CONFIRM_WORK": return {"inline_keyboard": [[{"text": "✅ Sí, es Trabajo", "callback_data": "SAVE_WORK"}], [{"text": "🔄 Cambiar", "callback_data": "RESET"}]]}
    
    # --- UPDATE: MENÚ DE 3 OPCIONES ---
    if tag == "SELECT_TYPE": return {"inline_keyboard": [
        [{"text": "🏠 Casa", "callback_data": "SAVE_HOME"}, {"text": "🏢 Trabajo", "callback_data": "SAVE_WORK"}],
        [{"text": "📍 Guardar con otro nombre", "callback_data": "SAVE_OTHER"}], # <--- NUEVO BOTÓN
        [{"text": "❌ Cancelar", "callback_data": "RESET"}]
    ]}
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
    # ---------------------------------------------------------
    # 1. MODO BROADCAST (Invocado por Scheduler)
    # ---------------------------------------------------------
    if event.get('action') == "BROADCAST_CONTINGENCY":
        print("📢 Iniciando Broadcast...")
        
        # 👇 NOTA: Ya quitamos el 'try:' problemático de aquí.
        data = event.get('data', {})
        phase = data.get('phase')
        now_mx = get_mexico_time().strftime("%H:%M")
        msg = ""

        # --- FIX: EXTRAER EL LINK OFICIAL DE LA CAME ---
        link_came = data.get('oficial_link', 'https://www.gob.mx/comisionambiental')

        if phase == "SUSPENDIDA":
            msg = cards.CARD_CONTINGENCY_LIFTED.format(
                report_time=now_mx,
                oficial_link=link_came, # <--- ENLACE INYECTADO
                footer=cards.BOT_FOOTER
            )
        else:
            # 1. Datos del Contaminante
            val = data.get('value', {}).get('value', '')
            unit = data.get('value', {}).get('unit', '')
            tipo = data.get('alert_type', 'Contaminación').capitalize()
            pollutant_str = f"{tipo} ({val} {unit})"
            
            # --- NUEVO: Extraer nombre de la estación ---
            station_name = data.get('trigger_station_name', 'Red Oficial SIMAT')
            station_id = data.get('trigger_station_id', '')
            station_display = f"{station_name} ({station_id})" if station_id else station_name
            
            # 2. Extraer Restricciones 
            recs = data.get('recommendations', {})
            categories = recs.get('categories', [])
            restricciones_list = []
            
            for cat in categories:
                if "VEHICULAR" in cat.get('name', '').upper():
                    restricciones_list = cat.get('items', [])
                    break 
            
            res_txt = "\n".join([f"🚫 {item}" for item in restricciones_list]) if restricciones_list else "🚫 Consulta fuentes oficiales."

            # 3. Formatear Tarjeta (Pasando el nuevo parámetro)
            msg = cards.CARD_CONTINGENCY.format(
                report_time=now_mx,
                phase=phase.upper(),
                pollutant_info=pollutant_str,
                station_info=station_display,
                restrictions_txt=res_txt,
                oficial_link=link_came, # <--- ENLACE INYECTADO
                footer=cards.BOT_FOOTER
            )

        # B. Enviar a Usuarios (Scan Eficiente)
        try:
            scan_kwargs = {
                'FilterExpression': "alerts.contingency = :a",
                'ExpressionAttributeValues': {":a": True},
                'ProjectionExpression': "user_id"
            }
            done = False
            start_key = None
            count = 0
            
            while not done:
                if start_key: scan_kwargs['ExclusiveStartKey'] = start_key
                response = table.scan(**scan_kwargs)
                for u in response.get('Items', []):
                    send_telegram(u['user_id'], msg)
                    count += 1
                start_key = response.get('LastEvaluatedKey')
                if not start_key: done = True
            
            print(f"✅ Broadcast enviado a {count} usuarios.")
            return {'statusCode': 200, 'body': f'Sent to {count}'}
        except Exception as e:
            print(f"❌ Error Broadcast: {e}")
            return {'statusCode': 500, 'body': str(e)}

    # ---------------------------------------------------------
    # 2. MESSAGES (Manejo de Telegram)
    # ---------------------------------------------------------
    try:
        body = json.loads(event.get('body', '{}'))
        
        # 1. CALLBACKS
        if 'callback_query' in body:
            cb = body['callback_query']
            chat_id = cb['message']['chat']['id']
            user_id = cb['from']['id']
            data = cb['data']
            raw_name = cb['from'].get('first_name', 'Usuario')
            first_name = str(raw_name).replace("_", " ").replace("*", "").replace("`", "")
            
            print(f"👆 [CALLBACK] User: {user_id} | Data: {data}") 
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb['id']})
            
            resp = ""
            
            # --- GUARDADO ---
            if data == "SAVE_HOME": resp = confirm_saved_location(user_id, 'casa')
            elif data == "SAVE_WORK": resp = confirm_saved_location(user_id, 'trabajo')
            elif data == "RESET": resp = "🗑️ Cancelado."
            
            # --- ACCESOS RÁPIDOS (Resumen y Ubicaciones) ---
            # Detectamos cualquier botón que empiece con CHECK_AIR_ o los viejos CHECK_HOME/WORK
            elif data.startswith("CHECK_AIR_") or data.startswith("CHECK_HOME") or data.startswith("CHECK_WORK"):
                # 1. Normalizar la llave (Key)
                if "HOME" in data: loc_key = "casa"
                elif "WORK" in data: loc_key = "trabajo"
                else: 
                    # Extraer "unam" de "CHECK_AIR_unam"
                    loc_key = data.replace("CHECK_AIR_", "").lower()
                
                # 2. Buscar en BD
                user = get_user_profile(user_id)
                locs = user.get('locations', {})
                
                # Intentamos buscar directo o normalizado
                # (Usamos el helper normalize_key si ya lo agregaste, sino búsqueda directa)
                found_key = None
                if loc_key in locs: found_key = loc_key
                
                if found_key:
                    lat, lon = float(locs[found_key]['lat']), float(locs[found_key]['lon'])
                    disp_name = locs[found_key].get('display_name', found_key.capitalize())
                    
                    # --- FIX: Inyectar datos para la Píldora HNC desde el botón ---
                    veh = user.get('vehicle')
                    sys_state = table.get_item(Key={'user_id': 'SYSTEM_STATE'}).get('Item', {})
                    current_phase = sys_state.get('last_contingency_phase', 'None')
                    
                    report = generate_report_card(first_name, disp_name, lat, lon, vehicle=veh, contingency_phase=current_phase)
                    send_telegram(chat_id, report, markup=cards.get_exposure_button())
                    return {'statusCode': 200, 'body': 'OK'}
                else:
                    resp = f"⚠️ No encontré la ubicación '{loc_key}'. Intenta actualizar tu menú."

            # --- BORRADO DE UBICACIONES (BOTONES) ---
            elif data.startswith("DELETE_LOC_"):
                # Viene de: "DELETE_LOC_CASA"
                loc_name = data.replace("DELETE_LOC_", "").lower()
                # Mostramos advertencia antes de borrar
                resp = (
                    f"⚠️ **¿Estás seguro de borrar '{loc_name.capitalize()}'?**\n\n"
                    "🛑 Al hacerlo, **también se eliminarán** todas las alertas y recordatorios configurados para esta ubicación."
                )
                # Botones de Si/No
                markup = cards.get_delete_confirmation_buttons(loc_name)
                send_telegram(chat_id, resp, markup)
                return {'statusCode': 200, 'body': 'OK'}

            # --- PASO 2: EJECUTAR BORRADO (CASCADA) ---
            elif data.startswith("CONFIRM_DEL_"):
                loc_name = data.replace("CONFIRM_DEL_", "").lower()
                
                if delete_location_from_db(user_id, loc_name):
                    resp = f"🗑️ **{loc_name.capitalize()} eliminada.**"
                    
                    # FIX: Cargar usuario y detectar status para botones correctos
                    user = get_user_profile(user_id)
                    status = user.get('subscription', {}).get('status', 'FREE')
                    is_prem = "PREMIUM" in status.upper() or "TRIAL" in status.upper()
                    
                    # Pasamos is_prem a la función de botones
                    markup = cards.get_summary_buttons(user.get('locations', {}), is_prem)
                else:
                    resp = "⚠️ Error al eliminar."
                    markup = None
                
                send_telegram(chat_id, resp, markup)
                return {'statusCode': 200, 'body': 'OK'}

            # --- CANCELAR ---
            elif data == "CANCEL_DELETE":
                resp = "✅ Operación cancelada. Tu ubicación sigue segura."

            # --- RESPUESTAS DEL ONBOARDING TRANSPORTE ---
            elif data.startswith("SET_TRANS_"):
                medio = data.replace("SET_TRANS_", "")
                if medio == "home_office":
                    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET profile_transport = :p", ExpressionAttributeValues={':p': {'medio': 'home_office', 'horas': 0}})
                    send_telegram(chat_id, "✅ Perfil guardado (Home Office).\n\n👇 Presiona de nuevo el botón para ver tu resultado:", markup=cards.get_exposure_button())
                else:
                    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET profile_transport = :p", ExpressionAttributeValues={':p': {'medio': medio, 'horas': 2}})
                    send_telegram(chat_id, "📍 **¡Entendido!**\n\nPor último, ¿cuántas horas en total pasas al día en ese transporte? (Ida y vuelta).", markup=cards.get_time_buttons())
                return {'statusCode': 200, 'body': 'OK'}

            elif data.startswith("SET_TIME_"):
                horas_str = data.replace("SET_TIME_", "")
                horas_db = Decimal(horas_str) # Boto3 exige Decimal
                # 1. Guardamos las horas
                table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET profile_transport.horas = :h", ExpressionAttributeValues={':h': horas_db})
                send_telegram(chat_id, "✅ **¡Perfil completado!**\n\n⏳ *Calculando tu desgaste celular...*")
                
                # 2. Simulamos el clic de CHECK_EXPOSURE forzando el dato
                # Al cambiar el valor de 'data', el bloque de abajo (CHECK_EXPOSURE) 
                # NO se ejecutará automáticamente porque ya pasamos por los 'elif'.
                # La forma correcta es volver a llamar a la función internamente o copiar el código.
                # Para evitar código duplicado o recursión riesgosa en Lambda, usaremos la vía segura:
                
                # RECONSTRUCCIÓN RÁPIDA DEL CÁLCULO
                try:
                    user = get_user_profile(user_id)
                    locs = user.get('locations', {})
                    transp = user.get('profile_transport') # Ya incluye las horas actualizadas
                    
                    lat_c, lon_c = locs['casa']['lat'], locs['casa']['lon']
                    resp_c = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_c}&lon={lon_c}").json()
                    vector_c = resp_c.get("vector_exposicion_ayer")
                    
                    vector_t = None
                    es_ho = False
                    if 'trabajo' in locs:
                        lat_t, lon_t = locs['trabajo']['lat'], locs['trabajo']['lon']
                        resp_t = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_t}&lon={lon_t}").json()
                        vector_t = resp_t.get("vector_exposicion_ayer")

                    if vector_c:
                        calc = CalculadoraRiesgoSmability()
                        perfil = {"transporte_default": transp.get('medio', 'auto_ventana'), "tiempo_traslado_horas": transp.get('horas', 2)}
                        res = calc.calcular_usuario(vector_c, perfil, vector_t, es_home_office=es_ho)
                        
                        cigs, dias = res['cigarros'], res['dias_perdidos']
                        
                        # Nota: En lugar de pintar cigarros reales, pintamos humo tóxico
                        grafico_humo = "🌫️" * int(cigs) if cigs >= 1 else "🌫️"

                        card = cards.CARD_EXPOSICION.format(
                            user_name=first_name, 
                            emoji_alerta="⚠️" if cigs >= 0.5 else "ℹ️", 
                            emoji_cigarro=grafico_humo, 
                            cigarros=cigs, 
                            emoji_edad="⏳🧓" if dias >= 1.0 else "🕰️", 
                            dias=dias,
                            promedio_riesgo=res['promedio_riesgo'],
                            footer=cards.BOT_FOOTER
                        )
                        send_telegram(chat_id, card)
                    else:
                        send_telegram(chat_id, "⚠️ Aún no tengo los datos atmosféricos de ayer procesados.")
                except Exception as e:
                    print(f"Error forzando calculo final: {e}")
                    send_telegram(chat_id, "Hubo un error al procesar tu exposición.")
                    
                return {'statusCode': 200, 'body': 'OK'}

            # --- MENÚ AVANZADO (Placeholder) ---
            elif data == "CONFIG_ADVANCED":
                resp = "⚙️ **Configuración Avanzada**\n\nAquí podrás gestionar tu suscripción y métodos de pago.\n*(Próximamente)*"

            elif data == "SAVE_OTHER":
                resp = "✍️ **¿Qué nombre le ponemos?**\n\nEscribe el nombre que quieras (Ej. *'Escuela'*, *'Gym'*, *'Casa Mamá'*)."

            # =========================================================
            # 🚬 FLUJO GAMIFICACIÓN: CIGARROS, EDAD URBANA Y ONBOARDING
            # =========================================================
            elif data == "CHECK_EXPOSURE":
                user = get_user_profile(user_id)
                locs = user.get('locations', {})
                transp = user.get('profile_transport') 
                
                if 'casa' not in locs:
                    send_telegram(chat_id, "⚠️ Necesito tu ubicación de CASA para calcular tu exposición. Toca el clip 📎 y envíala.")
                    return {'statusCode': 200, 'body': 'OK'}

                if not transp:
                    send_telegram(chat_id, "⚙️ **¡Vamos a personalizar tu cálculo!**\n\nPara decirte exactamente cuántos cigarros respiraste, necesito saber a qué te expones en el tráfico.\n\n👇 **¿Qué transporte usas más en tu rutina diaria?**", markup=cards.get_transport_buttons())
                    return {'statusCode': 200, 'body': 'OK'}

                try:
                    lat_c, lon_c = locs['casa']['lat'], locs['casa']['lon']
                    resp_c = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_c}&lon={lon_c}").json()
                    vector_c = resp_c.get("vector_exposicion_ayer")
                    
                    vector_t = None
                    es_ho = (transp.get('medio') == 'home_office')
                    
                    if 'trabajo' in locs and not es_ho:
                        lat_t, lon_t = locs['trabajo']['lat'], locs['trabajo']['lon']
                        resp_t = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_t}&lon={lon_t}").json()
                        vector_t = resp_t.get("vector_exposicion_ayer")

                    if vector_c:
                        calc = CalculadoraRiesgoSmability()
                        perfil = {"transporte_default": transp.get('medio', 'auto_ventana'), "tiempo_traslado_horas": transp.get('horas', 2)}
                        res = calc.calcular_usuario(vector_c, perfil, vector_t, es_home_office=es_ho)
                        
                        cigs, dias = res['cigarros'], res['dias_perdidos']
                        
                        # Nota: En lugar de pintar cigarros reales, pintamos humo tóxico
                        grafico_humo = "🌫️" * int(cigs) if cigs >= 1 else "🌫️"

                        card = cards.CARD_EXPOSICION.format(
                            user_name=first_name, 
                            emoji_alerta="⚠️" if cigs >= 0.5 else "ℹ️", 
                            emoji_cigarro=grafico_humo, 
                            cigarros=cigs, 
                            emoji_edad="⏳🧓" if dias >= 1.0 else "🕰️", 
                            dias=dias,
                            promedio_riesgo=res['promedio_riesgo'],
                            footer=cards.BOT_FOOTER
                        )
                        if 'trabajo' not in locs and not es_ho: 
                            card += "\n\n💡 *Tip: Guarda la ubicación de tu 'Trabajo' para un cálculo más exacto.*"
                        
                        send_telegram(chat_id, card)
                    else:
                        send_telegram(chat_id, "⚠️ Aún no tengo los datos atmosféricos de ayer procesados.")
                except Exception as e:
                    print(f"Error EXPOSURE: {e}")
                return {'statusCode': 200, 'body': 'OK'}

            # --- RESPUESTAS DEL ONBOARDING TRANSPORTE ---
            elif data.startswith("SET_TRANS_"):
                medio = data.replace("SET_TRANS_", "")
                if medio == "home_office":
                    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET profile_transport = :p", ExpressionAttributeValues={':p': {'medio': 'home_office', 'horas': 0}})
                    send_telegram(chat_id, "✅ Perfil guardado (Home Office).\n\n👇 Presiona de nuevo el botón para ver tu resultado:", markup=cards.get_exposure_button())
                else:
                    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET profile_transport = :p", ExpressionAttributeValues={':p': {'medio': medio, 'horas': 2}})
                    send_telegram(chat_id, "📍 **¡Entendido!**\n\nPor último, ¿cuántas horas en total pasas al día en ese transporte? (Ida y vuelta).", markup=cards.get_time_buttons())
                return {'statusCode': 200, 'body': 'OK'}

            elif data.startswith("SET_TIME_"):
                horas_str = data.replace("SET_TIME_", "")
                horas_db = Decimal(horas_str) # Boto3 exige Decimal
                # 1. Guardamos las horas
                table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET profile_transport.horas = :h", ExpressionAttributeValues={':h': horas_db})
                send_telegram(chat_id, "✅ **¡Perfil completado!**\n\n⏳ *Calculando tu desgaste celular...*")
                
                try:
                    user = get_user_profile(user_id)
                    locs = user.get('locations', {})
                    transp = user.get('profile_transport') 
                    
                    lat_c, lon_c = locs['casa']['lat'], locs['casa']['lon']
                    resp_c = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_c}&lon={lon_c}").json()
                    vector_c = resp_c.get("vector_exposicion_ayer")
                    
                    vector_t = None
                    es_ho = False
                    if 'trabajo' in locs:
                        lat_t, lon_t = locs['trabajo']['lat'], locs['trabajo']['lon']
                        resp_t = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_t}&lon={lon_t}").json()
                        vector_t = resp_t.get("vector_exposicion_ayer")

                    if vector_c:
                        calc = CalculadoraRiesgoSmability()
                        perfil = {"transporte_default": transp.get('medio', 'auto_ventana'), "tiempo_traslado_horas": transp.get('horas', 2)}
                        res = calc.calcular_usuario(vector_c, perfil, vector_t, es_home_office=es_ho)
                        
                        cigs, dias = res['cigarros'], res['dias_perdidos']
                        
                        # Nota: En lugar de pintar cigarros reales, pintamos humo tóxico
                        grafico_humo = "🌫️" * int(cigs) if cigs >= 1 else "🌫️"

                        card = cards.CARD_EXPOSICION.format(
                            user_name=first_name, 
                            emoji_alerta="⚠️" if cigs >= 0.5 else "ℹ️", 
                            emoji_cigarro=grafico_humo, 
                            cigarros=cigs, 
                            emoji_edad="⏳🧓" if dias >= 1.0 else "🕰️", 
                            dias=dias,
                            promedio_riesgo=res['promedio_riesgo'],
                            footer=cards.BOT_FOOTER
                        )
                        send_telegram(chat_id, card)
                    else:
                        send_telegram(chat_id, "⚠️ Aún no tengo los datos atmosféricos de ayer procesados.")
                except Exception as e:
                    print(f"Error forzando calculo final: {e}")
                    send_telegram(chat_id, "Hubo un error al procesar tu exposición.")
                    
                return {'statusCode': 200, 'body': 'OK'}

            # =========================================================
            # --- FINAL DE CALLBACKS: RESPUESTA DEFAULT (CATCH-ALL) ---
            # =========================================================
            if not resp: 
                resp = "⚠️ Opción no reconocida o sesión expirada."
                
            send_telegram(chat_id, resp)
            return {'statusCode': 200, 'body': 'OK'}

        # 2. MESSAGES
        if 'message' not in body: return {'statusCode': 200, 'body': 'OK'}
        msg = body['message']
        chat_id = msg['chat']['id']
        user_id = msg['from']['id']
        raw_name = msg['from'].get('first_name', 'Usuario')
        first_name = str(raw_name).replace("_", " ").replace("*", "").replace("`", "")
        
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
        memoria_str += f"\n**Auto:** {veh_info}" 
        memoria_str += f"\n**Alertas:** {alerts}"
        memoria_str += f"\n**Plan:** {plan_status}"
        
        has_casa, has_trabajo = 'casa' in locs, 'trabajo' in locs
        forced_tag = None
        system_extra = "NORMAL"
        
        # 1. Prioridad: Si hay mapa en este mensaje (Override total)
        if lat:
            if not has_casa: forced_tag = "CONFIRM_HOME"
            elif not has_trabajo: forced_tag = "CONFIRM_WORK"
            else: forced_tag = "SELECT_TYPE"
        
        # 2. Prioridad: Si NO hay mapa, revisamos si hay uno pendiente en la DB
        else:
            # FIX: Detectar draft pendiente en la DB para avisar al Prompt
            draft = user_profile.get('draft_location')
            if draft and isinstance(draft, dict) and 'lat' in draft:
                system_extra = "ESTADO: PENDING_NAME_FOR_LOCATION (Tengo coordenadas en memoria esperando nombre)."
            
            # Solo sugerimos onboarding si no hay draft pendiente
            elif not has_casa: system_extra = "ONBOARDING 1: Pide CASA"
            elif not has_trabajo: system_extra = "ONBOARDING 2: Pide TRABAJO"

        # --- FIX: MINI-CALENDARIO ANTI-ALUCINACIONES ---
        now_mx = get_mexico_time()
        dias_es = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        
        # Generamos la "hoja de respuestas" para los próximos 7 días
        calendario = []
        for i in range(7):
            d = now_mx + timedelta(days=i)
            calendario.append(f"{dias_es[d.weekday()]} {d.strftime('%Y-%m-%d')}")
        
        # Ejemplo: "HOY: Lunes 2026-02-16 | PRÓXIMOS: Martes 2026-02-17, Miércoles 2026-02-18..."
        fecha_str = f"HOY: {calendario[0]} | PRÓXIMOS DÍAS: " + ", ".join(calendario[1:])
        hora_str = now_mx.strftime("%H:%M")

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
                        
                        # --- FIX: ESCUDO ANTI-ALUCINACIÓN ---
                        # Si el LLM olvidó pasar el nombre, lo buscamos en el texto original del usuario
                        if not key:
                            key = resolve_location_key(user_id, user_content)
                            
                        if key and key in locs:
                            in_lat = float(locs[key]['lat'])
                            in_lon = float(locs[key]['lon'])
                            in_name = locs[key].get('display_name', key.capitalize()) # Usa el nombre bonito real
                    
                    # 2. DECISIÓN: ¿Tenemos datos válidos?
                    if in_lat != 0 and in_lon != 0:
                        sys_state = table.get_item(Key={'user_id': 'SYSTEM_STATE'}).get('Item', {})
                        current_phase = sys_state.get('last_contingency_phase', 'None')
                        
                        r = generate_report_card(first_name, in_name, in_lat, in_lon, vehicle=veh, contingency_phase=current_phase)
                        send_telegram(chat_id, r, markup=cards.get_exposure_button())
                        return {'statusCode': 200, 'body': 'OK'}
                    else:
                        # ❌ FALLO: No hay coordenadas. Avisamos al LLM para que pregunte al usuario.
                        r = f"⚠️ No encontré coordenadas para '{in_name}'. Pide al usuario que guarde la ubicación o envíe su ubicación actual."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                        # Aquí NO hacemos return, dejamos que el flujo baje para que GPT explique el error en texto.
                    
                # --- INICIO DE NUEVAS TOOLS (TEXTO/LLM) ---
                elif fn == "configurar_transporte":
                    medio = args.get('medio', 'auto_ventana')
                    horas_raw = args.get('horas_al_dia', 2)
                    
                    # 1. Validación y Redondeo de Horas (Convertimos a float y redondeamos a 1 decimal)
                    try:
                        horas_float = round(float(horas_raw), 1)
                    except:
                        horas_float = 2.0 # Default si falla
                        
                    # 2. Límite de sentido común (Max 6 horas, Min 0)
                    if horas_float > 6.0:
                        r = "⚠️ El tiempo máximo de exposición que puedo calcular son 6 horas diarias. Por favor, explícale esto amablemente al usuario y pídele un tiempo real."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                        continue # Saltamos el guardado en BD
                        
                    if horas_float < 0:
                        horas_float = 0.0
                        
                    # 3. Validar Transporte soportado
                    MEDIOS_VALIDOS = ["auto_ac", "suburbano", "cablebus", "metro", "metrobus", "auto_ventana", "combi", "caminar", "bicicleta", "home_office"]
                    if medio not in MEDIOS_VALIDOS:
                        r = f"⚠️ '{medio}' no es un modo válido. Dile que elija entre: Auto, Metro, Metrobús, Combi, Tren, Cablebús, Caminar, Bici o Home Office."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                        continue # Saltamos el guardado en BD

                    # 4. Guardar en DynamoDB
                    horas_db = Decimal(str(horas_float))
                    table.update_item(
                        Key={'user_id': str(user_id)},
                        UpdateExpression="SET profile_transport = :p",
                        ExpressionAttributeValues={':p': {'medio': medio, 'horas': horas_db}}
                    )
                    r = f"✅ Perfil actualizado: Viajas en {medio} por {horas_float} horas. Dile al usuario de forma amigable que su transporte ha sido guardado y que ya puede consultar sus cigarros."
                    gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})

                elif fn == "calcular_exposicion_diaria":
                    # Este es el código que el LLM ejecutará si el usuario pregunta "cuántos cigarros respiré"
                    user = get_user_profile(user_id)
                    locs = user.get('locations', {})
                    transp = user.get('profile_transport', {'medio': 'auto_ventana', 'horas': 2})
                    
                    if 'casa' not in locs:
                        r = "⚠️ Necesito tu ubicación de CASA para calcular esto. Pídesela al usuario."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                    else:
                        lat_c, lon_c = locs['casa']['lat'], locs['casa']['lon']
                        resp_c = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_c}&lon={lon_c}").json()
                        vector_c = resp_c.get("vector_exposicion_ayer")
                        
                        vector_t = None
                        es_ho = True
                        if 'trabajo' in locs:
                            lat_t, lon_t = locs['trabajo']['lat'], locs['trabajo']['lon']
                            resp_t = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_t}&lon={lon_t}").json()
                            vector_t = resp_t.get("vector_exposicion_ayer")
                            es_ho = False

                        if vector_c:
                            calc = CalculadoraRiesgoSmability()
                            perfil = {"transporte_default": transp.get('medio', 'auto_ventana'), "tiempo_traslado_horas": transp.get('horas', 2)}
                            res = calc.calcular_usuario(vector_c, perfil, vector_t, es_home_office=es_ho)
                            
                            # AQUÍ ESTÁ EL FIX: Le devolvemos un string limpio a GPT para que él lo hable.
                            r = f"El usuario respiró el equivalente a {res['cigarros']} cigarros ayer, perdiendo {res['dias_perdidos']} días de vida celular (Edad Urbana). Promedio de exposición integral: {res['promedio_riesgo']} ug/m3. Transmítele esto de forma empática usando emojis."
                            gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                        else:
                            r = "⚠️ Aún no tengo los datos atmosféricos de ayer procesados."
                            gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                # --- FIN DE NUEVAS TOOLS ---
                
                # --- NUEVA TOOL: ELIMINAR UBICACIÓN (TEXTO) ---
                elif fn == "eliminar_ubicacion":
                    nombre = args.get('nombre_ubicacion')
                    if not nombre:
                        r = "⚠️ ¿Qué ubicación quieres borrar? (Casa, Trabajo...)"
                    else:
                        # Intentar borrar
                        success = delete_location_from_db(user_id, nombre)
                        if success:
                            r = f"✅ Ubicación '{nombre}' eliminada de tu perfil."
                        else:
                            r = f"⚠️ No encontré '{nombre}' o ya estaba borrada."
                    
                    gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})

                elif fn == "renombrar_ubicacion":
                    nombre_viejo = args.get('nombre_actual')
                    nombre_nuevo = args.get('nombre_nuevo')
                    
                    if not nombre_viejo or not nombre_nuevo:
                        r = "⚠️ Necesito saber qué nombre cambiar y cuál es el nuevo."
                    else:
                        success, r = rename_location_in_db(user_id, nombre_viejo, nombre_nuevo)
                        
                    gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                
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
                    # 1. Obtener datos del usuario
                    user = get_user_profile(user_id)
                    sub_data = user.get('subscription', {})
                    status_str = sub_data.get('status', 'FREE')
                    
                    # Detectar si es Premium o Trial para la lógica visual
                    is_prem = "PREMIUM" in status_str.upper() or "TRIAL" in status_str.upper()
                    
                    # 2. Generar Tarjeta Visual (Ahora funciona para FREE y PREMIUM)
                    # La diferencia visual la maneja cards.py internamente usando 'status_str'
                    r = cards.generate_summary_card(
                        first_name, 
                        user.get('alerts', {}), 
                        user.get('vehicle', None), 
                        user.get('locations', {}), # OJO: Pasamos locations para listar "Casa/Trabajo"
                        status_str, # Nuevo argumento vital para el Tag de Contingencia
                        user.get('profile_transport', None) # <--- EL FIX FINAL AQUÍ
                    )
                    
                    # 3. Generar Botones Inteligentes (Upselling si es Free)
                    markup = cards.get_summary_buttons(user.get('locations', {}), is_prem)
                    
                    # 4. Enviar y Cortar (Hard Stop)
                    send_telegram(chat_id, r, markup)
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
                        verif_txt = cards.get_verification_period(digit, holo) # <--- FIX APLICADO

                        # Formatear Tarjeta
                        card = cards.CARD_HNC_DETAILED.format(
                            mes_nombre=nombre_mes_actual,
                            plate=digit,
                            color=cards.ENGOMADOS.get(int(digit), "Desconocido"), # <--- FIX LIMPIO Y SEGURO
                            holo=str(holo).upper(),
                            verificacion_txt=verif_txt, # <--- NUEVO CAMPO
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
                        fecha = args.get('fecha_referencia', get_mexico_time().strftime("%Y-%m-%d"))
                        
                        # 1. Extraer fase para contingencia
                        sys_state = table.get_item(Key={'user_id': 'SYSTEM_STATE'}).get('Item', {})
                        current_phase = sys_state.get('last_contingency_phase', 'None')
                        
                        # 2. Extraer 3 valores del nuevo motor
                        can_drive, r_short, r_detail = cards.check_driving_status(plate, holo, fecha, current_phase)
                        
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
                            reason=r_detail, # <--- Usamos el Detalle Visual
                            footer=cards.BOT_FOOTER
                        )
                        send_telegram(chat_id, card)
                        return {'statusCode': 200, 'body': 'OK'}
                
                # --- NUEVA TOOL: CALENDARIO MENSUAL (READ ONLY) ---
                elif fn == "obtener_calendario_mensual":
                    user = get_user_profile(user_id)
                    veh = user.get('vehicle')
                    
                    if not veh or not veh.get('active'):
                        r = "⚠️ No tienes auto configurado. Pide al usuario: 'Dime tu terminación de placa y holograma'."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                    else:
                        # Extraer datos de la DB
                        digit = veh.get('plate_last_digit')
                        holo = veh.get('hologram')
                        engomado = veh.get('engomado', 'Desconocido') # Leemos el color guardado
                        
                        # Generar Cálculos (Igual que en configurar_auto)
                        now_mx = get_mexico_time()
                        lista_dias = get_monthly_prohibited_dates(digit, holo, now_mx.year, now_mx.month)
                        txt_sem, txt_sab = get_restriction_summary(digit, holo)
                        
                        # Traducir Mes
                        meses_es = {1:"Enero", 2:"Febrero", 3:"Marzo", 4:"Abril", 5:"Mayo", 6:"Junio", 7:"Julio", 8:"Agosto", 9:"Septiembre", 10:"Octubre", 11:"Noviembre", 12:"Diciembre"}
                        nombre_mes_actual = meses_es[now_mx.month]
                        verif_txt = cards.get_verification_period(digit, holo)
                        
                        # Generar Tarjeta Detallada
                        card = cards.CARD_HNC_DETAILED.format(
                            mes_nombre=nombre_mes_actual,
                            plate=digit,
                            color=cards.ENGOMADOS.get(int(digit), "Desconocido"), # <--- FIX LIMPIO Y SEGURO
                            holo=str(holo).upper(),
                            verificacion_txt=verif_txt, # <--- NUEVO CAMPO
                            dias_semana_txt=txt_sem,
                            sabados_txt=txt_sab,
                            lista_fechas="\n".join(lista_dias) if lista_dias else "¡Circulas todo el mes! 🎉",
                            multa_cdmx=f"${MULTA_CDMX_MIN:,.0f} - ${MULTA_CDMX_MAX:,.0f}",
                            multa_edomex=f"${MULTA_EDOMEX:,.0f}",
                            footer=cards.BOT_FOOTER
                        )
                        
                        # Enviar y Cortar
                        send_telegram(chat_id, card)
                        return {'statusCode': 200, 'body': 'OK'}
                # --- NUEVA TOOL: TARJETA DE VERIFICACIÓN ---
                elif fn == "consultar_verificacion":
                    user = get_user_profile(user_id)
                    veh = user.get('vehicle')
                    
                    if not veh or not veh.get('active'):
                        r = "⚠️ No tienes auto guardado. Dime: 'Mi placa termina en 5 y es holograma 1'."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                    else:
                        plate = veh.get('plate_last_digit')
                        holo = veh.get('hologram')
                        color = veh.get('engomado', 'Desconocido')
                        
                        periodo = cards.get_verification_period(plate, holo)
                        limite = get_verification_deadline(periodo)
                        multa_aprox = f"{20 * 108.57:,.2f}" # Valor UMA 2025 aprox
                        
                        card = cards.CARD_VERIFICATION.format(
                            plate_info=f"Terminación {plate}",
                            engomado=f"Engomado {color}",
                            period_txt=periodo,
                            deadline=limite,
                            fine_amount=multa_aprox,
                            footer=cards.BOT_FOOTER
                        )
                        send_telegram(chat_id, card)
                        return {'statusCode': 200, 'body': 'OK'}

                # --- NUEVA TOOL: GUARDADO PERSONALIZADO ---
                elif fn == "guardar_ubicacion_personalizada":
                    # Extraemos el nombre que dijo el usuario (ej. "Gym")
                    nombre_raw = args.get('nombre', 'Personalizado')
                    
                    # Limpiamos el nombre para que sea una key válida en BD (sin espacios ni caracteres raros)
                    # "Casa Mamá" -> "casa_mama" (para la key)
                    nombre_key = str(nombre_raw).lower().strip().replace(" ", "_")
                    
                    # Llamamos a la misma función poderosa de guardado
                    # Nota: confirm_saved_location usa 'nombre_key' para la base de datos
                    # pero usará 'nombre_key.capitalize()' para mostrarlo.
                    # FIX: Para que se vea bonito "Gym" y no "gym", pasamos el raw saneado visualmente luego.
                    
                    # Ejecutar guardado
                    r = confirm_saved_location(user_id, nombre_key)
                    
                    gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                    
                # --- NUEVA TOOL: MIS UBICACIONES (URL FIX) ---
                elif fn == "consultar_ubicaciones_guardadas":
                    user = get_user_profile(user_id)
                    locs = user.get('locations', {})
                    
                    if not locs:
                        r = "📭 No tienes ubicaciones guardadas."
                        gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})
                    else:
                        lista_txt = ""
                        for k, v in locs.items():
                            # --- FIX: SANITIZACIÓN DE TEXTO Y URL ---
                            # 1. Limpiamos caracteres peligrosos del nombre
                            raw_disp = v.get('display_name', 'Ubicación')
                            display = str(raw_disp).replace("_", " ").replace("*", "").replace("[", "").replace("]", "")
                            key_clean = str(k).capitalize().replace("_", " ")
                            
                            # 2. Validamos coordenadas para evitar None
                            lat = v.get('lat', 0)
                            lon = v.get('lon', 0)
                            
                            # 3. URL Segura (Formato API Google Maps Universal)
                            maps_url = f"http://www.google.com/maps/place/{lat},{lon}"
                            
                            lista_txt += f"🏠 **{key_clean}:** {display}\n🔗 [Ver en Mapa]({maps_url})\n\n"
                        
                        card = cards.CARD_MY_LOCATIONS.format(
                            user_name=first_name, # Ya viene limpio del FIX 1
                            locations_list=lista_txt.strip(),
                            footer=cards.BOT_FOOTER
                        )
                        
                        markup = cards.get_locations_buttons(locs)
                        send_telegram(chat_id, card, markup)
                        return {'statusCode': 200, 'body': 'OK'}

                elif fn == "configurar_alerta_contingencia":
                    val = args.get('activar')
                    # Manejo robusto de booleanos que vienen como string
                    if isinstance(val, str): is_active = val.lower() == 'true'
                    else: is_active = bool(val)
                    
                    r = toggle_contingency_alert(user_id, is_active)
                    gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})

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
