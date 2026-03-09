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
import pytz
import tools_logic
import stripeairegpt
import business_logic
from decimal import Decimal


# --- CONFIG ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
API_LIGHT_URL = os.environ.get('API_LIGHT_URL', 'https://vuy3dprsp2udtuelnrb5leg6ay0ygsky.lambda-url.us-east-1.on.aws/')
URL_LAMBDA_GRAFICAS = "https://myvuewtfcagpeqoesc6tcdsopi0lbmgv.lambda-url.us-east-1.on.aws/" # <--- NUEVA LÍNEA
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

# --- GATEKEEPER: VERIFICADOR DE STRIPE Y CUPOS ---
def check_quota_and_permissions(user_profile, action_type, user_id):
    tier, days_left = stripeairegpt.evaluate_user_tier(user_profile)
    print(f"🛡️ [GATEKEEPER] User: {user_id} | Plan: {tier} | Days Left: {days_left}")

    # Funciones SIEMPRE permitidas para todos (Free y Premium)
    if action_type in ['configurar_auto', 'consultar_hoy_no_circula']:
        return True, "", None

    LIMIT_LOC_FREE = 2
    LIMIT_LOC_PREM = 3
    
    # 1. Lógica EXCLUSIVA para agregar ubicaciones (Aquí dejamos pasar a los Free)
    if action_type == 'add_location':
        current_locs = len(user_profile.get('locations', {}))
        if tier == 'FREE':
            if current_locs >= LIMIT_LOC_FREE:
                texto, botones = stripeairegpt.get_paywall_response(tier, days_left, action_type, str(user_id))
                return False, f"🛑 **Límite Básico Alcanzado.** Tienes ocupados tus {LIMIT_LOC_FREE} espacios gratuitos.\n\n" + texto, botones
            return True, "", None # Pasa libremente si tiene menos de 2
        else: # PREMIUM / TRIAL
            if current_locs >= LIMIT_LOC_PREM:
                return False, f"🛑 **Espacios Llenos.** Tienes ocupados tus {LIMIT_LOC_PREM} espacios. Borra uno para agregar otro.", None
            if tier == 'TRIAL':
                texto_aviso, _ = stripeairegpt.get_paywall_response(tier, days_left, action_type, str(user_id))
                return True, texto_aviso, None
            return True, "", None

    # 2. Lógica para el RESTO de acciones (alertas, salud, rutina, gráficas)
    if tier == 'FREE':
        texto, botones = stripeairegpt.get_paywall_response(tier, days_left, action_type, str(user_id))
        return False, texto, botones
        
    if tier == 'TRIAL':
        texto_aviso, _ = stripeairegpt.get_paywall_response(tier, days_left, action_type, str(user_id))
        return True, texto_aviso, None

    return True, "", None

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

# --- FIX: PERSISTENCIA DEL DRAFT Y SELLO DE TRIAL ---
def save_interaction_and_draft(user_id, first_name, lat=None, lon=None):
    now_iso = datetime.now().isoformat()
    
    # 🚀 FIX TRIAL: Agregamos created_at con if_not_exists para fijar el "Día 1" del usuario para siempre
    update_expr = "SET first_name=:n, last_interaction=:t, created_at=if_not_exists(created_at, :t), locations=if_not_exists(locations,:e), alerts=if_not_exists(alerts,:al), subscription=if_not_exists(subscription,:sub)"
    vals = {
        ':n': first_name, 
        ':t': now_iso, 
        ':e': {}, 
        ':al': {'threshold': {}, 'schedule': {}},
        ':sub': {'status': 'FREE'}
    }
    
    # OJO: Solo tocamos 'draft_location' si realmente recibimos coordenadas nuevas
    if lat and lon:
        update_expr += ", draft_location = :d"
        vals[':d'] = {'lat': str(lat), 'lon': str(lon), 'ts': now_iso}
    
    try: 
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression=update_expr, ExpressionAttributeValues=vals)
    except Exception as e: 
        print(f"❌ [DB SAVE ERROR]: {e}")

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
        
        # NUEVO FIX: Escudo protector de edificios (Paredes/Ventanas filtran el 60%)
        self.FACTOR_INTRAMUROS = 0.4 
        
        self.FACTORES_TRANSPORTE = {
            "auto_ac": 0.4, "suburbano": 0.5, "cablebus": 0.7,
            "metro": 0.8, "metrobus": 0.9, "auto_ventana": 1.0,
            "combi": 1.2, "caminar": 1.3, "bicicleta": 1.5, "home_office": 1.0
        }

    def calcular_usuario(self, vector_casa, perfil_usuario, vector_trabajo=None, es_home_office=False):
        # 1. 🛡️ ESCUDO: Asegurar que los vectores sean válidos
        if not vector_casa:
            return None # Fallo crítico, no hay datos base

        # Si es home office o no mandaron trabajo, el trabajo ES la casa
        if es_home_office or not vector_trabajo:
            vector_trabajo = vector_casa
            hora_salida, hora_llegada_casa, factor_transporte = 25, 25, 1.0
        else:
            hora_salida = 7  
            duracion_traslado = float(perfil_usuario.get('tiempo_traslado_horas', 2)) 
            mitad_traslado = math.ceil(duracion_traslado / 2.0)
            hora_llegada_trabajo = hora_salida + mitad_traslado
            hora_salida_trabajo = 18 
            hora_llegada_casa = hora_salida_trabajo + mitad_traslado
            modo_transporte = perfil_usuario.get('transporte_default', 'auto_ventana')
            factor_transporte = self.FACTORES_TRANSPORTE.get(modo_transporte, 1.0)

        suma_exposicion_acumulada = 0.0
        suma_ias_acumulada = 0.0 
        
        # 2. 🛡️ ESCUDO: Extracción segura con fallbacks de 24 horas
        vector_casa_ias = vector_casa.get('ias', [0]*24)
        vector_trabajo_ias = vector_trabajo.get('ias', [0]*24)
        
        c_pm25 = vector_casa.get('pm25_12h', [0.0]*24)
        c_o3 = vector_casa.get('o3_1h', [0.0]*24)
        t_pm25 = vector_trabajo.get('pm25_12h', [0.0]*24)
        t_o3 = vector_trabajo.get('o3_1h', [0.0]*24)

        for hora in range(24):
            # 3. Contaminación EXTERIOR bruta (de la calle)
            ext_casa = c_pm25[hora] + (c_o3[hora] * self.K_O3_A_PM)
            ext_trab = t_pm25[hora] + (t_o3[hora] * self.K_O3_A_PM)
            
            ias_ext_casa = vector_casa_ias[hora]
            ias_ext_trab = vector_trabajo_ias[hora]

            # 4. Contaminación INTERIOR (Con escudo del edificio aplicado)
            int_casa = ext_casa * self.FACTOR_INTRAMUROS
            int_trab = ext_trab * self.FACTOR_INTRAMUROS
            
            # El IAS también disminuye si estás protegido en interiores
            ias_int_casa = ias_ext_casa * self.FACTOR_INTRAMUROS
            ias_int_trab = ias_ext_trab * self.FACTOR_INTRAMUROS

            # 5. Reconstrucción de la película del día
            if es_home_office:
                nivel_hora = int_casa # Todo el día protegido en casa
                ias_hora = ias_int_casa
            else:
                if hora < hora_salida or hora >= hora_llegada_casa:
                    nivel_hora = int_casa # Durmiendo / En casa
                    ias_hora = ias_int_casa
                elif hora_salida <= hora < hora_llegada_trabajo:
                    # En la calle (Sin escudo de edificio, solo escudo de vehículo)
                    nivel_hora = ((ext_casa + ext_trab) / 2) * factor_transporte 
                    ias_hora = ((ias_ext_casa + ias_ext_trab) / 2) * factor_transporte
                elif hora_llegada_trabajo <= hora < hora_salida_trabajo:
                    nivel_hora = int_trab # Protegido dentro de la oficina
                    ias_hora = ias_int_trab
                elif hora_salida_trabajo <= hora < hora_llegada_casa:
                    # En la calle regresando
                    nivel_hora = ((ext_casa + ext_trab) / 2) * factor_transporte 
                    ias_hora = ((ias_ext_casa + ias_ext_trab) / 2) * factor_transporte

            suma_exposicion_acumulada += nivel_hora
            suma_ias_acumulada += ias_hora

        promedio = suma_exposicion_acumulada / 24.0
        cigarros = promedio / self.K_CIGARRO
        
        # Redondeo final
        promedio_ias = math.ceil(suma_ias_acumulada / 24.0) 
        
        # Categorías
        from cards import get_emoji_for_quality
        if promedio_ias <= 50: cat_ias = "Buena"
        elif promedio_ias <= 100: cat_ias = "Regular"
        elif promedio_ias <= 150: cat_ias = "Mala"
        elif promedio_ias <= 200: cat_ias = "Muy Mala"
        else: cat_ias = "Extremadamente Mala"
        
        return {
            "cigarros": round(cigarros, 1), 
            "dias_perdidos": round(cigarros * self.K_ENVEJECIMIENTO, 1),
            "promedio_riesgo": round(promedio, 1),
            "promedio_ias": promedio_ias,
            "calidad_ias": f"{get_emoji_for_quality(cat_ias)} Calidad {cat_ias}" 
        }

# --- TOOLS ---
def confirm_saved_location(user_id, tipo):
    try:
        user = get_user_profile(user_id)
        draft = user.get('draft_location')
        
        # Validación de seguridad: Si no hay mapa, no guardamos nada.
        if not draft: return "⚠️ No encontré coordenadas recientes. Por favor toca el clip 📎 y envía la ubicación de nuevo."
        
        # 1. Normalización Robusta (Zócalo -> zocalo)
        key = normalize_key(tipo)
        display_name = tipo.strip().capitalize() # Mantiene tilde visualmente (Zócalo)

        locs = user.get('locations', {})
        is_new = key not in locs
        
        # 2. Gatekeeper (Límite de ubicaciones)
        if is_new:
            can_proceed, msg, markup = check_quota_and_permissions(user, 'add_location', user_id)
            if not can_proceed: 
                send_telegram(user_id, msg, markup) # Le mandamos el Paywall con los botones
                return "🛑 Límite alcanzado o suscripción requerida." # Mensaje interno para que GPT entienda

        # --- 🎯 FIX: LÓGICA DE DESTINO FLEXIBLE ---
        # Regla: Si NO es 'casa', es un destino potencial.
        es_destino = (key != 'casa') 

        # Si es un nuevo destino, primero quitamos el sello de destino a cualquier otra ubicación
        if es_destino:
            for k, v in locs.items():
                if v.get('is_destination'):
                    table.update_item(
                        Key={'user_id': str(user_id)},
                        UpdateExpression=f"REMOVE locations.{k}.is_destination"
                    )
        # --------------------------------------------
        
        # --- FIX ÍTEM 6: INYECTAR ALERTA DEFAULT DE 100 PTS ---
        alerta_default = {'umbral': 100, 'active': True, 'consecutive_sent': 0}

        # 3. Query: Guardamos y BORRAMOS el draft para no reusarlo por error
        # Inyectamos alerts.threshold.#loc = :alert_val en la misma operación
        if is_new: 
            update_expr = "SET locations.#loc = :val, alerts.threshold.#loc = :alert_val REMOVE alerts.schedule.#loc, draft_location"
        else: 
            update_expr = "SET locations.#loc = :val, alerts.threshold.#loc = :alert_val REMOVE draft_location"

        table.update_item(
            Key={'user_id': str(user_id)}, 
            UpdateExpression=update_expr, 
            ExpressionAttributeNames={'#loc': key}, 
            ExpressionAttributeValues={
                ':val': {
                    'lat': draft['lat'], 
                    'lon': draft['lon'], 
                    'display_name': display_name, 
                    'active': True,
                    'is_destination': es_destino # <--- AQUÍ SE ANCLA EL DESTINO
                },
                ':alert_val': alerta_default
            }
        )
        
        # 4. Confirmación
        user_final = get_user_profile(user_id)
        count = len(user_final.get('locations', {}))
        
        msg = f"✅ **{display_name} guardada.**\n🚨 *Alerta de emergencia activada (>100 pts).*"
        
        # Feedback visual de la ruta
        if es_destino:
            msg += f"\n📍 *Tu nueva ruta de exposición es: Casa ↔ {display_name}*"

        tier, _ = stripeairegpt.evaluate_user_tier(user_final)
        if tier == 'FREE':
            msg += "\n\n🎁 *Bonus:* Recibirás hasta **3 alertas automáticas gratis** para probar el servicio."
            
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
    can_proceed, msg, markup = check_quota_and_permissions(user, 'alertas', user_id)
    if not can_proceed: 
        send_telegram(user_id, msg, markup)
        return "🛑 Suscripción requerida para usar alertas programadas."

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
    can_proceed, msg, markup = check_quota_and_permissions(user, 'alertas', user_id)
    if not can_proceed: 
        send_telegram(user_id, msg, markup)
        return "🛑 Suscripción requerida para usar alertas programadas."

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
    # ts_str viene de la API, ej: "2026-02-23T07:20:00"
    if ts_str and len(ts_str) >= 16:
        hora_dato = ts_str[11:16] # Extrae "07:20"
        return hora_dato
    
    # Solo como paracaídas si la API no manda el dato por alguna falla
    return "Reciente"

def get_time_greeting():
    h = (datetime.utcnow() - timedelta(hours=6)).hour
    return "Buenos días" if 5<=h<12 else "Buenas tardes" if 12<=h<20 else "Buenas noches"

def get_maps_url(lat, lon): return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

# --- REPORTE DIARIO CON PÍLDORA HNC Y VERIFICACIÓN INTEGRADAS ---
def generate_report_card(user_name, location_name, lat, lon, vehicle=None, contingency_phase="None", user_profile=None, is_premium=False):

    try:
        url = f"{API_LIGHT_URL}?lat={lat}&lon={lon}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200: return f"⚠️ Error de red ({r.status_code}).", "Regular"
        
        data = r.json()
        if data.get('status') == 'out_of_bounds': return f"📍 **Fuera de rango.** ({lat:.2f}, {lon:.2f})", "Regular"

        qa, meteo, ubic = data.get('aire', {}), data.get('meteo', {}), data.get('ubicacion', {})
        calidad = qa.get('calidad', 'Regular')
        tendencia_actual = qa.get('tendencia', 'Estable ➡️')
        
        # --- FIX: EXTRAER SALUD DEL USUARIO ---
        user_condition = "Ninguno"
        if user_profile and 'health_profile' in user_profile:
            health_data = user_profile['health_profile']
            # Filtramos solo las condiciones activas
            condiciones = [info['condition'] for key, info in health_data.items() if info.get('active')]
            if condiciones:
                user_condition = ", ".join(condiciones) # Ej: "Asma, Alergias"

        # Inyección HNC
        hnc_pill = cards.build_hnc_pill(vehicle, contingency_phase, is_premium)
        combined_footer = f"{hnc_pill}\n\n{cards.BOT_FOOTER}" if hnc_pill else cards.BOT_FOOTER

        # Guardamos la tarjeta
        card_text = cards.CARD_REPORT.format(
            user_name=user_name, greeting=get_time_greeting(), location_name=location_name,
            maps_url=get_maps_url(lat, lon), region=f"{ubic.get('mun', 'ZMVM')}, {ubic.get('edo', 'CDMX')}",
            report_time=get_official_report_time(data.get('ts')), ias_value=qa.get('ias', 0),
            risk_category=calidad, risk_circle=cards.get_emoji_for_quality(calidad),
            pollutant=qa.get('dominante', 'N/A'),
            trend=tendencia_actual,
            forecast_block=cards.format_forecast_block(data.get('pronostico_timeline', [])),
            # --- AQUÍ INYECTAMOS LA CONDICIÓN MÉDICA ---
            health_recommendation=cards.get_health_advice(calidad, user_condition, is_premium), 
            temp=meteo.get('tmp', 0), humidity=meteo.get('rh', 0), wind_speed=meteo.get('wsp', 0),
            footer=combined_footer
        )
        
        return card_text, calidad
    except Exception as e: return f"⚠️ Error visual: {str(e)}", "Regular"

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
    
    # 🛡️ FIX 3: Inyectamos link_preview_options para limpiar el footer
    payload = {
        "chat_id": chat_id, 
        "text": text, 
        "parse_mode": "Markdown",
        "link_preview_options": {"is_disabled": True}
    }
    
    if markup: payload["reply_markup"] = markup
    try: 
        r = requests.post(url, json=payload)
        if r.status_code != 200: print(f"❌ [TG FAIL] {r.text}")
    except Exception as e: print(f"❌ [TG NET ERROR]: {e}")

def send_telegram_action(chat_id, action="typing"):
    """Envía un estado a Telegram (ej. 'escribiendo...') para que el usuario sepa que el bot está procesando"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
    payload = {"chat_id": chat_id, "action": action}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"❌ Error enviando chat action: {e}")

def send_persistent_gps_button(chat_id):
    """Envía el botón nativo de Telegram que solicita el GPS en tiempo real"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    markup = {
        "keyboard": [
            [{"text": "📍 Calidad del Aire", "request_location": True}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }
    payload = {
        "chat_id": chat_id, 
        "text": "👇 *Pst... Usa este botón en cualquier momento para escanear el aire de donde estás parado:*", 
        "parse_mode": "Markdown",
        "reply_markup": markup
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"❌ Error mandando botón GPS: {e}")

def send_telegram_photo_local(chat_id, photo_path, caption, markup=None):
    """Sube una foto desde la carpeta local de la Lambda hacia Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
    if markup: data["reply_markup"] = json.dumps(markup)
    
    try:
        with open(photo_path, 'rb') as photo_file:
            files = {"photo": photo_file}
            r = requests.post(url, data=data, files=files, timeout=15)
            
            if r.status_code != 200:
                print(f"❌ [TG PHOTO FAIL]: {r.text}")
                # Paracaídas: si falla la foto, enviamos solo el texto
                send_telegram(chat_id, caption, markup)
    except FileNotFoundError:
        print(f"❌ [FILE ERROR] No se encontró la imagen en: {photo_path}")
        send_telegram(chat_id, caption, markup)
    except Exception as e:
        print(f"❌ [TG UPLOAD ERROR]: {e}")
        send_telegram(chat_id, caption, markup)

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
            # NUEVO: Teclado Inline combinado (Compartir + Mi Resumen)
            # Asumiendo que tu archivo cards.py tiene una función get_contingency_buttons
            # Si no la tienes, inyectamos el JSON directamente aquí:
            markup_contingencia = {
                "inline_keyboard": [
                    [{"text": "👤 Mi Perfil", "callback_data": "ver_resumen"}],
                    [{"text": "📲 Compartir Alerta", "switch_inline_query": "contingencia"}] 
                ]
            }

        # B. Enviar a Usuarios (Scan Eficiente)
        try:
            # --- FIX BANNERS: Calculamos la foto ANTES del bucle para no gastar CPU ---
            import os
            directorio_actual = os.path.dirname(os.path.abspath(__file__))
            if phase == "SUSPENDIDA":
                ruta_imagen = os.path.join(directorio_actual, "banners", "banner_buena.png")
            else:
                ruta_imagen = os.path.join(directorio_actual, "banners", "banner_contingencia.png")
            # -------------------------------------------------------------------------

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
                    # --- FIX BANNERS: Usamos la nueva función con foto ---
                    send_telegram_photo_local(u['user_id'], ruta_imagen, msg, markup=markup_contingencia)
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

        # --- 🕵️‍♂️ PASO 0: UNIFICAR IDENTIDAD (Extraer ID sin importar qué mandaron) ---
        # Detectamos si es botón (callback_query) o mensaje (message)
        update_origin = body.get('callback_query') or body.get('message')
        
        if not update_origin: 
            return {'statusCode': 200, 'body': 'OK'} # No es nada procesable

        user_id = update_origin['from']['id']
        raw_name = update_origin['from'].get('first_name', 'Usuario')
        first_name = str(raw_name).replace("_", " ").replace("*", "").replace("`", "")

        # --- 🛡️ PASO 1: CARGAR PERFIL Y SANITIZAR DE INMEDIATO ---
        user_profile = get_user_profile(user_id)
        
        # Agregamos 'profile_transport' a la limpieza obligatoria
        campos_dict = ['locations', 'alerts', 'vehicle', 'health_profile', 'subscription', 'profile_transport']
        for campo in campos_dict:
            if not isinstance(user_profile.get(campo), dict):
                user_profile[campo] = {}
        
        # Limpieza profunda de ubicaciones
        locs_validadas = {}
        for k, v in user_profile.get('locations', {}).items():
            if isinstance(v, dict): 
                locs_validadas[k] = v
        user_profile['locations'] = locs_validadas
        # --- 🏁 TERMINA SANITIZACIÓN ---
        
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
            
            # --- GUARDADO Y CASCADA DE ONBOARDING ---
            if data == "SAVE_HOME": 
                resp = confirm_saved_location(user_id, 'casa')
                user = get_user_profile(user_id)
                
                # FIX: Usamos la referencia global de stripeairegpt (ya importado al inicio)
                tier, _ = stripeairegpt.evaluate_user_tier(user)
                
                if 'trabajo' not in user.get('locations', {}):
                    resp += "\n\n🚀 **PASO 2:**\nAhora, envíame la ubicación de tu **TRABAJO** (o escuela) tocando el clip 📎."
                else:
                    if tier in ['PREMIUM', 'TRIAL']:
                        if not user.get('vehicle', {}).get('active'):
                            resp += "\n\n🚗 **PASO FINAL:**\nRegistra tu auto para evitar multas. Escríbeme:\n💬 *'Mi placa termina en 5 y soy holograma 0'.*"
                    else:
                        resp += "\n\n🎉 **¡Listo! Tu perfil básico está completo.**"

            elif data == "SAVE_WORK": 
                resp = confirm_saved_location(user_id, 'trabajo')
                user = get_user_profile(user_id)
                tier, _ = stripeairegpt.evaluate_user_tier(user)
                
                if tier in ['PREMIUM', 'TRIAL'] and not user.get('vehicle', {}).get('active'):
                    resp += "\n\n🚗 **PASO FINAL:**\nPara protegerte de multas, registra tu auto. Escríbeme:\n💬 *'Mi placa termina en 5 y soy holograma 0'.*"
                else:
                    resp += "\n\n🎉 **¡Listo! Tu perfil básico está completo.**"

            elif data == "RESET": 
                resp = "🗑️ Cancelado."
                
            # --- BOTONES DEL ONBOARDING INICIAL (/start) ---
            elif data == "SET_LOC_casa":
                resp = "🏠 **Paso 1: Configurar Casa**\n\nPor favor, toca el clip 📎 (abajo a la derecha) y envíame la **Ubicación** de tu casa.\n\n*(No te preocupes, tus datos están protegidos por nuestro Aviso de Privacidad).* "
                
            elif data == "SET_VEHICLE_start":
                resp = "🚗 **Registrar Auto**\n\nPara avisarte si circulas o si hay Contingencia, escríbeme de forma natural:\n\n💬 *'Mi auto tiene placas terminación 5 y holograma 0'.*"
            
            # --- ACCESOS RÁPIDOS (Resumen y Ubicaciones) ---
            elif data.startswith("CHECK_AIR_") or data.startswith("CHECK_HOME") or data.startswith("CHECK_WORK"):
                # 1. Inicialización de seguridad (Scope Fix)
                loc_key = ""
                disp_name = "Ubicación" 
                
                # Determinamos la llave
                if "HOME" in data: loc_key = "casa"
                elif "WORK" in data: loc_key = "trabajo"
                else: loc_key = data.replace("CHECK_AIR_", "").lower()
                
                # 2. Obtener datos
                user = get_user_profile(user_id)
                
                # 👇 [NUEVO AJUSTE C] - Calculamos si es premium aquí mismo de forma SEGURA 👇
                tier_eval_btn, _ = stripeairegpt.evaluate_user_tier(user)
                es_premium_seguro = tier_eval_btn in ['PREMIUM', 'TRIAL']
                
                locs = user.get('locations', {})
                
                # 3. Validación y Extracción
                if loc_key in locs and isinstance(locs[loc_key], dict):
                    target_loc = locs[loc_key]
                    lat = float(target_loc.get('lat', 0))
                    lon = float(target_loc.get('lon', 0))
                    disp_name = target_loc.get('display_name', loc_key.capitalize())
                    
                    veh = user.get('vehicle')
                    sys_state = table.get_item(Key={'user_id': 'SYSTEM_STATE'}).get('Item', {})
                    current_phase = sys_state.get('last_contingency_phase', 'None')
                    
                    # Generamos reporte - 👇 PASAMOS is_premium=es_premium_seguro 👇
                    report_text, calidad = generate_report_card(
                        first_name, disp_name, lat, lon, 
                        vehicle=veh, 
                        contingency_phase=current_phase, 
                        user_profile=user,
                        is_premium=es_premium_seguro  # <--- CORREGIDO Y BLINDADO
                    )
                    
                    # Selección de Banner
                    mapa_archivos = {
                        "Buena": "banner_buena.png", "Regular": "banner_regular.png", "Mala": "banner_mala.png",
                        "Muy Mala": "banner_muy_mala.png", "Extremadamente Mala": "banner_extrema.png"
                    }
                    calidad_clean = calidad.replace("Extremadamente Alta", "Extremadamente Mala").replace("Muy Alta", "Muy Mala").replace("Alta", "Mala")
                    nombre_png = mapa_archivos.get(calidad_clean, "banner_regular.png")
                    
                    import os
                    directorio_actual = os.path.dirname(os.path.abspath(__file__))
                    ruta_imagen = os.path.join(directorio_actual, "banners", nombre_png)
                    
                    # Envío
                    send_telegram_photo_local(chat_id, ruta_imagen, report_text, markup=cards.get_exposure_button())
                    return {'statusCode': 200, 'body': 'OK'}
                
                else:
                    # Este era el primer else. El código entra aquí si no hay llave.
                    resp = f"⚠️ No encontré la ubicación '{loc_key}'. Intenta actualizar tu menú o configurarla de nuevo."
                    send_telegram(chat_id, resp)
                    return {'statusCode': 200, 'body': 'OK'}

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
            # --- FIX TAREA 9 (NUEVO): EJECUTOR DE BORRADO TOTAL ---
            elif data == "CONFIRM_RESET_ALL":
                try:
                    table.update_item(
                        Key={'user_id': str(user_id)},
                        UpdateExpression="REMOVE locations, alerts, vehicle, health_profile, profile_transport, draft_location, last_graphic_ts, last_tetris_ts"
                    )
                    send_telegram(chat_id, "💥 **Tus datos han sido eliminados correctamente.**\n\nEstás en blanco como el primer día. Si deseas volver a configurarme, escribe /start.", markup={"inline_keyboard": [[{"text": "🚀 Volver a empezar", "callback_data": "SET_LOC_casa"}]]})
                except Exception as e:
                    send_telegram(chat_id, f"❌ Error limpiando perfil: {e}")
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
                    vector_c = resp_c.get("vectores", {}).get("ayer")
                    
                    vector_t = None
                    es_ho = False
                    if 'trabajo' in locs:
                        lat_t, lon_t = locs['trabajo']['lat'], locs['trabajo']['lon']
                        resp_t = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_t}&lon={lon_t}").json()
                        vector_t = resp_t.get("vectores", {}).get("ayer")

                    if vector_c:
                        calc = CalculadoraRiesgoSmability()
                        perfil = {"transporte_default": transp.get('medio', 'auto_ventana'), "tiempo_traslado_horas": transp.get('horas', 2)}
                        res = calc.calcular_usuario(vector_c, perfil, vector_t, es_home_office=es_ho)
                        
                        cigs, dias = res['cigarros'], res['dias_perdidos']

                        # 1. Calcular fecha de ayer en texto
                        ahora_cdmx = datetime.utcnow() - timedelta(hours=6)
                        ayer = ahora_cdmx - timedelta(days=1)
                        meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
                        fecha_ayer_str = f"{ayer.day} de {meses[ayer.month]}"

                        # 2. Generación de la Rutina Visual
                        nombres_medios = {
                            "auto_ac": "🚗 Auto (A/C)", "suburbano": "🚆 Tren Suburbano", "cablebus": "🚡 Cablebús",
                            "metro": "🚇 Metro/Tren", "metrobus": "🚌 Metrobús", "auto_ventana": "🚗 Auto (Ventanillas)",
                            "combi": "🚐 Combi/Micro", "caminar": "🚶 Caminar", "bicicleta": "🚲 Bici", "home_office": "🏠 Home Office"
                        }
                        
                        medio_raw = transp.get('medio', 'auto_ventana')
                        horas_val = transp.get('horas', 2)
                        medio_str = nombres_medios.get(medio_raw, medio_raw.capitalize())
                        
                        if es_ho: 
                            rutina_txt = "🏠 **Tu rutina:** Modalidad Home Office"
                            cigs_txt = f"Respiraste el equivalente a *{cigs} cigarros invisibles* filtrados por tu casa."
                        else:
                            emoji_rut = medio_str.split(' ')[0] if ' ' in medio_str else '📍'
                            # Inyectamos la aclaración de Casa ↔ Trabajo
                            rutina_txt = f"{emoji_rut} **Tu rutina:** Casa ↔ Trabajo\n⏱️ **Tiempo:** {horas_val} hrs en {medio_str.replace(emoji_rut, '').strip()}"
                            cigs_txt = f"Respiraste el equivalente a *{cigs} cigarros invisibles* en tu recorrido y estancia."
                        
                        grafico_humo = "🌫️" * int(cigs) if cigs >= 1 else "🌫️"

                        # 3. Armar la tarjeta con las nuevas variables
                        card = cards.CARD_EXPOSICION.format(
                            user_name=first_name, 
                            fecha_ayer=fecha_ayer_str, 
                            emoji_alerta="⚠️" if cigs >= 0.5 else "ℹ️", 
                            rutina_str=rutina_txt,
                            calidad_ias=res['calidad_ias'],    # <--- NUEVO
                            promedio_ias=res['promedio_ias'],  # <--- NUEVO
                            emoji_cigarro=grafico_humo, 
                            texto_cigarros=cigs_txt,
                            cigarros=cigs, 
                            emoji_edad="⏳🧓" if dias >= 1.0 else "🕰️", 
                            dias=dias,
                            promedio_riesgo=res['promedio_riesgo'],
                            footer=cards.BOT_FOOTER
                        )
                        
                        # Generamos botón para compartir
                        markup_viral = cards.get_share_exposure_button(cigs, dias)
                        # --- INYECCIÓN DEL BOTÓN DE GRÁFICA ---
                        if markup_viral and "inline_keyboard" in markup_viral:
                            markup_viral["inline_keyboard"].insert(0, [{"text": "🚇 Ver exposición de hoy", "callback_data": "GET_GRAPHIC"}])
                        else:
                            markup_viral = {"inline_keyboard": [[{"text": "🚇 Ver exposición de hoy", "callback_data": "GET_GRAPHIC"}]]}
                        # --------------------------------------
                        
                        if 'trabajo' not in locs and not es_ho: 
                            card += "\n\n💡 *Tip: Guarda la ubicación de tu 'Trabajo' para un cálculo más exacto.*"
                        
                        send_telegram(chat_id, card, markup=markup_viral)
                    else:
                        send_telegram(chat_id, "⚠️ Aún no tengo los datos atmosféricos de ayer procesados.")
                except Exception as e:
                    print(f"Error forzando calculo final: {e}")
                    send_telegram(chat_id, "Hubo un error al procesar tu exposición.")
                    
                return {'statusCode': 200, 'body': 'OK'}

            # --- NUEVO: BOTÓN GENERAR GRÁFICA VISUAL (CON CANDADO) ---
            elif data == "GET_GRAPHIC":
                # 0. 🔒 GATEKEEPER STRIPE
                user = get_user_profile(user_id)
                can_proceed, msg, markup = check_quota_and_permissions(user, 'rutina', user_id)
                if not can_proceed:
                    send_telegram(chat_id, msg, markup)
                    return {'statusCode': 200, 'body': 'OK'}
                if msg: send_telegram(chat_id, msg)
                    
                # 0. 🔒 EL CANDADO (15 Minutos)
                last_req = user_profile.get('last_graphic_ts')
                now_utc = datetime.utcnow()
                
                if last_req:
                    last_dt = datetime.fromisoformat(last_req)
                    segundos_pasados = (now_utc - last_dt).total_seconds()
                    
                    if segundos_pasados < 900: # 900 segundos = 15 minutos
                        minutos_faltantes = 15 - int(segundos_pasados / 60)
                        send_telegram(chat_id, f"⏳ *¡Calma, velocista!*\nYa dibujé tu ruta hace ratito. Por favor espera **{minutos_faltantes} minutos** para generar una nueva gráfica actualizada.")
                        return {'statusCode': 200, 'body': 'OK'}
                
                # Si pasa el candado, le cobramos el "ticket" guardando la hora actual
                table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET last_graphic_ts = :t", ExpressionAttributeValues={':t': now_utc.isoformat()})

                # 1. UX: Mensaje de espera y capturar su ID para borrarlo después
                msg_id = None
                url_send = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                payload = {"chat_id": chat_id, "text": "🚇 *Dibujando tu ruta tóxica...*\nDame unos 10 a 15 segunditos ⏱️", "parse_mode": "Markdown"}
                try:
                    r = requests.post(url_send, json=payload).json()
                    if r.get("ok"): msg_id = r["result"]["message_id"]
                except: pass

                # 2. Llamar a tu nueva Lambda de Gráficas
                try:
                    send_telegram_action(chat_id, "upload_photo") 
                    resp = requests.get(f"{URL_LAMBDA_GRAFICAS}?action=serpiente&user_id={user_id}", timeout=30).json()
                    
                    if resp.get("status") == "success":
                        photo_url = resp["url"]
                        url_photo = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
                        requests.post(url_photo, json={"chat_id": chat_id, "photo": photo_url, "caption": "¡Aquí tienes tu exposición al humo de hoy! 🐍💨", "parse_mode": "Markdown"})
                    else:
                        # Si hubo error en la generación, le borramos el candado para que pueda reintentar
                        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE last_graphic_ts")
                        send_telegram(chat_id, "Hubo un error al generar tu gráfica 😔. Revisa tus ubicaciones.")
                except Exception as e:
                    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE last_graphic_ts")
                    send_telegram(chat_id, "El dibujante se tardó un poco de más 😅. Intenta de nuevo por favor.")

                # 4. Borrar el mensajito de "espera" para mantener limpio el chat
                if msg_id:
                    url_del = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
                    requests.post(url_del, json={"chat_id": chat_id, "message_id": msg_id})
                    
                return {'statusCode': 200, 'body': 'OK'}
            
            # ==========================================
            # 🧱 BOTÓN: HISTORIAL SEMANAL (TETRIS)
            # ==========================================
            elif data == "GET_TETRIS":
                # 0. 🔒 GATEKEEPER STRIPE
                can_proceed, msg, markup = check_quota_and_permissions(user_profile, 'rutina', user_id)
                if not can_proceed:
                    send_telegram(chat_id, msg, markup)
                    return {'statusCode': 200, 'body': 'OK'}
                if msg: send_telegram(chat_id, msg)
                # 0. 🔒 EL CANDADO (15 Minutos independientes para el Tetris)
                last_req = user_profile.get('last_tetris_ts')
                now_utc = datetime.utcnow()
                
                if last_req:
                    last_dt = datetime.fromisoformat(last_req)
                    segundos_pasados = (now_utc - last_dt).total_seconds()
                    
                    if segundos_pasados < 900: # 15 minutos
                        minutos_faltantes = 15 - int(segundos_pasados / 60)
                        send_telegram(chat_id, f"⏳ *¡Calma!*\nYa dibujé tu historial hace ratito. Por favor espera **{minutos_faltantes} minutos** para generar uno nuevo.")
                        return {'statusCode': 200, 'body': 'OK'}
                
                # Cobramos el "ticket" guardando la hora actual
                table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="SET last_tetris_ts = :t", ExpressionAttributeValues={':t': now_utc.isoformat()})

                # 1. UX: Mensaje de espera
                msg_id = None
                try:
                    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                      json={"chat_id": chat_id, "text": "🧱 *Dibujando tu historial acumulado...*\nDame unos 15 segunditos ⏱️", "parse_mode": "Markdown"}).json()
                    if r.get("ok"): msg_id = r["result"]["message_id"]
                except: pass

                # 2. Llamar a Lambda Gráficas (Tetris)
                try:
                    send_telegram_action(chat_id, "upload_photo") 
                    resp = requests.get(f"{URL_LAMBDA_GRAFICAS}?action=tetris&user_id={user_id}", timeout=30).json()
                    
                    if resp.get("status") == "success":
                        photo_url = resp["url"]
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", 
                                      json={"chat_id": chat_id, "photo": photo_url, "caption": "¡Aquí está tu Tetris de exposición! 🧱🏙️\n_Cada bloque es una semana. Mantente en colores claros._", "parse_mode": "Markdown"})
                    else:
                        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE last_tetris_ts")
                        send_telegram(chat_id, "Aún no tengo suficientes datos tuyos para generar el historial. Intenta mañana 😔")
                except Exception as e:
                    print(f"Error generando Tetris: {e}")
                    table.update_item(Key={'user_id': str(user_id)}, UpdateExpression="REMOVE last_tetris_ts")
                    send_telegram(chat_id, "El dibujante se tardó un poco de más 😅. Intenta de nuevo por favor.")

                # 3. Borrar el mensajito de "espera"
                if msg_id:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage", json={"chat_id": chat_id, "message_id": msg_id})
                    
                return {'statusCode': 200, 'body': 'OK'}
                
            # --- 📊 BLOQUE HOMOLOGADO: MI RESUMEN / PERFIL (CON CANDADOS) ---
            elif data == "ver_resumen":
                send_telegram_action(chat_id, "typing") 
                
                # 1. Cargar perfil fresco y evaluar Tier
                user = get_user_profile(user_id)
                tier, days_left = stripeairegpt.evaluate_user_tier(user)
                is_prem = tier in ['PREMIUM', 'TRIAL']

                texto_plan = f"TRIAL ({days_left} días restantes)" if tier == 'TRIAL' else tier

                print(f"🔘 [AUDIT_BUTTON] Generando para: {user_id}")
                print(f"🔘 [AUDIT_BUTTON] Tier: {tier}")
                print(f"🔘 [AUDIT_BUTTON] Alertas Raw: {user.get('alerts')}")

                # 2. Extraer sub-objetos (Usando tus llaves reales de DB)
                alerts = user.get('alerts', {})
                vehicle = user.get('vehicle', {})
                locations = user.get('locations', {})
                transport = user.get('profile_transport', {}) # Tu llave real
                health = user.get('health_profile', {})        # Tu llave real

                # 3. 🚩 INVOCAR LA TARJETA MAESTRA (AQUÍ ESTÁN LOS CANDADOS 🔒)
                card_resumen = cards.generate_summary_card(
                    user_name=first_name,
                    alerts=alerts,
                    vehicle=vehicle,
                    locations=locations,
                    plan_status=texto_plan,
                    transport_data=transport,
                    health_data=health
                )
                
                # 4. Obtener botones (Muestra "Go Premium" si es FREE)
                botones = cards.get_summary_buttons(locations, is_premium=is_prem)

                # 5. Envío limpio
                send_telegram(chat_id, card_resumen, markup=botones)
                return {'statusCode': 200, 'body': 'OK'}
                
            elif data == "CONFIG_ADVANCED":
                # Invocamos la tarjeta dinámica que ya tiene Stripe y Borrado
                texto_adv, markup_adv = cards.generate_advanced_settings_card(user_id)
                send_telegram(chat_id, texto_adv, markup=markup_adv)
                return {'statusCode': 200, 'body': 'OK'}

            elif data == "GO_PREMIUM":
                # Lógica de ventas limpia usando el motor de paywall
                user = get_user_profile(user_id)
                tier, days_left = stripeairegpt.evaluate_user_tier(user)
                texto_venta, botones_venta = stripeairegpt.get_paywall_response(tier, days_left, "premium", str(user_id))
                send_telegram(chat_id, texto_venta, markup=botones_venta)
                return {'statusCode': 200, 'body': 'OK'}

            elif data == "CONFIRM_HARD_DELETE":
                # Paso de seguridad para el borrado total
                resp = (
                    "🧨 *ZONA DE PELIGRO: BORRADO TOTAL* 🧨\n\n"
                    "¿Estás totalmente seguro? Esta acción eliminará permanentemente tu "
                    "historial de exposición, ubicaciones y perfil de salud de nuestra base de datos. "
                    "*No hay marcha atrás.*\n\n"
                    "⚠️ *NOTA IMPORTANTE:* Si tienes una suscripción activa, debes cancelarla "
                    "primero en el **Portal de Suscripciones** para detener los cargos, ya que "
                    "al borrar tu cuenta perderemos el vínculo con tu ID de Stripe."
                )
                markup = {"inline_keyboard": [[{"text": "🗑️ SÍ, BORRAR TODO", "callback_data": "EXECUTE_HARD_DELETE"}], [{"text": "❌ CANCELAR", "callback_data": "ver_resumen"}]]}
                send_telegram(chat_id, resp, markup)
                return {'statusCode': 200, 'body': 'OK'}

            elif data == "EXECUTE_HARD_DELETE":
                # Acción final
                table.delete_item(Key={'user_id': str(user_id)})
                send_telegram(chat_id, "💨 Tus datos han sido eliminados correctamente.")
                return {'statusCode': 200, 'body': 'OK'}

            elif data == "SAVE_OTHER":
                resp = "✍️ **¿Qué nombre le ponemos?**\n\nEscribe el nombre que quieras (Ej. Ponle *'Escuela'*, *'Gym'*, *'Casa Mamá'*)."

            # =========================================================
            # 🚬 FLUJO GAMIFICACIÓN: CIGARROS, EDAD URBANA Y ONBOARDING
            # =========================================================
            elif data == "CHECK_EXPOSURE":
                # 0. 🔒 GATEKEEPER STRIPE
                user = get_user_profile(user_id)
                can_proceed, msg, markup = check_quota_and_permissions(user, 'rutina', user_id)
                if not can_proceed:
                    send_telegram(chat_id, msg, markup)
                    return {'statusCode': 200, 'body': 'OK'}
                if msg: send_telegram(chat_id, msg)
                    
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
                    vector_c = resp_c.get("vectores", {}).get("ayer")
                    
                    vector_t = None
                    es_ho = (transp.get('medio') == 'home_office')
                    
                    if 'trabajo' in locs and not es_ho:
                        lat_t, lon_t = locs['trabajo']['lat'], locs['trabajo']['lon']
                        resp_t = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_t}&lon={lon_t}").json()
                        vector_t = resp_t.get("vectores", {}).get("ayer")

                    if vector_c:
                        calc = CalculadoraRiesgoSmability()
                        perfil = {"transporte_default": transp.get('medio', 'auto_ventana'), "tiempo_traslado_horas": transp.get('horas', 2)}
                        
                        # 🔥🔥🔥 AQUÍ ESTÁ LA LÍNEA MÁGICA QUE SE HABÍA BORRADO 🔥🔥🔥
                        res = calc.calcular_usuario(vector_c, perfil, vector_t, es_home_office=es_ho)
                        
                        if not res:
                            send_telegram(chat_id, "⚠️ Hubo un error interno calculando tu exposición.")
                            return {'statusCode': 200, 'body': 'OK'}

                        cigs, dias = res['cigarros'], res['dias_perdidos']

                        # 1. Calcular fecha de ayer en texto
                        ahora_cdmx = datetime.utcnow() - timedelta(hours=6)
                        ayer = ahora_cdmx - timedelta(days=1)
                        meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
                        fecha_ayer_str = f"{ayer.day} de {meses[ayer.month]}"

                        # 2. Generación de la Rutina Visual
                        nombres_medios = {
                            "auto_ac": "🚗 Auto (A/C)", "suburbano": "🚆 Tren Suburbano", "cablebus": "🚡 Cablebús",
                            "metro": "🚇 Metro/Tren", "metrobus": "🚌 Metrobús", "auto_ventana": "🚗 Auto (Ventanillas)",
                            "combi": "🚐 Combi/Micro", "caminar": "🚶 Caminar", "bicicleta": "🚲 Bici", "home_office": "🏠 Home Office"
                        }
                        
                        medio_raw = transp.get('medio', 'auto_ventana')
                        horas_val = transp.get('horas', 2)
                        medio_str = nombres_medios.get(medio_raw, medio_raw.capitalize())
                        
                        if es_ho: 
                            rutina_txt = "🏠 **Tu rutina:** Modalidad Home Office"
                            cigs_txt = f"Respiraste el equivalente a *{cigs} cigarros invisibles* filtrados por tu casa."
                        else:
                            emoji_rut = medio_str.split(' ')[0] if ' ' in medio_str else '📍'
                            rutina_txt = f"{emoji_rut} **Tu rutina:** Casa ↔ Trabajo\n⏱️ **Tiempo:** {horas_val} hrs en {medio_str.replace(emoji_rut, '').strip()}"
                            cigs_txt = f"Respiraste el equivalente a *{cigs} cigarros invisibles* en tu recorrido y estancia."
                        
                        grafico_humo = "🌫️" * int(cigs) if cigs >= 1 else "🌫️"

                        # 3. Armar la tarjeta con las nuevas variables
                        card = cards.CARD_EXPOSICION.format(
                            user_name=first_name, 
                            fecha_ayer=fecha_ayer_str, 
                            emoji_alerta="⚠️" if cigs >= 0.5 else "ℹ️", 
                            rutina_str=rutina_txt,
                            calidad_ias=res['calidad_ias'],    
                            promedio_ias=res['promedio_ias'],  
                            emoji_cigarro=grafico_humo, 
                            texto_cigarros=cigs_txt,
                            cigarros=cigs, 
                            emoji_edad="⏳🧓" if dias >= 1.0 else "🕰️", 
                            dias=dias,
                            promedio_riesgo=res['promedio_riesgo'],
                            footer=cards.BOT_FOOTER
                        )
                        
                        markup_viral = cards.get_share_exposure_button(cigs, dias)
                        # --- INYECCIÓN DEL BOTÓN DE GRÁFICA (REEMPLAZA ESTE BLOQUE) ---
                        if markup_viral and "inline_keyboard" in markup_viral:
                            markup_viral["inline_keyboard"].insert(0, [{"text": "🚇 Ver exposición de hoy", "callback_data": "GET_GRAPHIC"}])
                            markup_viral["inline_keyboard"].insert(1, [{"text": "🧱 Ver mi Tetris semanal", "callback_data": "GET_TETRIS"}])
                        else:
                            markup_viral = {"inline_keyboard": [
                                [{"text": "🚇 Ver exposición de hoy", "callback_data": "GET_GRAPHIC"}],
                                [{"text": "🧱 Ver mi Tetris semanal", "callback_data": "GET_TETRIS"}]
                            ]}
                        # --------------------------------------
                        
                        if 'trabajo' not in locs and not es_ho: 
                            card += "\n\n💡 *Tip: Guarda la ubicación de tu 'Trabajo' para un cálculo más exacto.*"
                        
                        send_telegram(chat_id, card, markup=markup_viral)
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

            # =========================================================
            # ⚡ FAST-PATH: Interceptor de Comandos (CONSOLIDADO)
            # FIX: KeyError 'user_name' + Navegabilidad de Resumen
            # =========================================================
            import re
            
            # 1. Normalización a prueba de balas (acentos, mayúsculas y caracteres)
            text_clean = user_content.strip().lower()
            text_clean = text_clean.replace('á','a').replace('é','e').replace('í','i').replace('ó','o').replace('ú','u')
            text_clean = re.sub(r'[^a-z0-9\s/]', '', text_clean) 

            # Diccionario base para todas las tarjetas (Evita el crash de user_name)
            card_args = {"user_name": first_name, "footer": cards.BOT_FOOTER}
            
            # Determinamos estatus para botones dinámicos
            tier_eval, _ = stripeairegpt.evaluate_user_tier(user_profile)
            is_prem_eval = tier_eval in ['PREMIUM', 'TRIAL']

            if text_clean in ["/start", "start", "hola", "empezar"]:
                print(f"🆕 [START] User: {user_id}")
                send_persistent_gps_button(chat_id)
                
                markup_onboarding = {
                    "inline_keyboard": [
                        [{"text": "📍 Configurar mi Casa", "callback_data": "SET_LOC_casa"}],
                        [{"text": "🚗 Registrar mi Auto", "callback_data": "SET_VEHICLE_start"}]
                    ]
                }
                msg_envio = cards.CARD_ONBOARDING.format(**card_args)
                send_telegram(chat_id, msg_envio, markup=markup_onboarding)
                return {'statusCode': 200, 'body': 'OK'}
                
            elif text_clean in ["ayuda", "menu", "que puedes hacer", "opciones", "/menu", "/ayuda"]:
                msg_envio = cards.CARD_MENU.format(**card_args)
                # UX: Inyectamos botones de consulta rápida de sus ubicaciones
                markup_menu = cards.get_summary_buttons(user_profile.get('locations', {}), is_prem_eval)
                send_telegram(chat_id, msg_envio, markup=markup_menu)
                return {'statusCode': 200, 'body': 'OK'}

            elif text_clean in ["reglas", "limitaciones", "como funciona", "alcance", "restricciones"]:
                msg_envio = cards.CARD_RULES.format(**card_args)
                send_telegram(chat_id, msg_envio, markup=cards.get_hnc_buttons()) # <-- Limpio
                return {'statusCode': 200, 'body': 'OK'}

            elif any(k in text_clean for k in ["que te pregunto", "que te puedo preguntar", "ejemplos", "prompts"]):
                msg_envio = cards.CARD_PROMPTS.format(**card_args)
                send_telegram(chat_id, msg_envio, markup=cards.get_hnc_buttons()) # <-- Limpio
                return {'statusCode': 200, 'body': 'OK'}
                
            elif text_clean in ["/borrar_mis_datos", "borrar mis datos", "borrar todo", "/reset_perfil"]:
                markup_borrado = {
                    "inline_keyboard": [
                        [{"text": "⚠️ Sí, borrar todos mis datos", "callback_data": "CONFIRM_RESET_ALL"}],
                        [{"text": "❌ Cancelar", "callback_data": "RESET"}]
                    ]
                }
                send_telegram(chat_id, "⚠️ **¿Estás seguro?**\n\nEsto eliminará permanentemente tus ubicaciones, vehículos, salud y rutinas.\n\n*(Suscripciones activas se mantienen)*.", markup=markup_borrado)
                return {'statusCode': 200, 'body': 'OK'}
                
            elif text_clean in ["/premium", "premium", "pagar", "comprar", "planes", "precio"]:
                tier, days_left = stripeairegpt.evaluate_user_tier(user_profile)
                if tier == 'PREMIUM':
                    send_telegram(chat_id, "💎 ¡Ya eres parte de la familia **Premium**!")
                else:
                    texto_venta, botones_venta = stripeairegpt.get_paywall_response("FREE", 0, "premium", str(user_id))
                    texto_venta = texto_venta.replace("🔒 *Función Bloqueada*", "💎 *AIreGPT Premium*")
                    send_telegram(chat_id, texto_venta, markup=botones_venta)
                return {'statusCode': 200, 'body': 'OK'}
                
            elif any(k in text_clean for k in ["que es el ias", "que es ias", "imeca"]):
                msg_envio = cards.CARD_IAS_INFO.format(**card_args)
                send_telegram(chat_id, msg_envio, markup=cards.get_hnc_buttons()) # <-- Limpio
                return {'statusCode': 200, 'body': 'OK'}
                
            # =========================================================
            # 📊 BLOQUE HOMOLOGADO: MI RESUMEN / PERFIL (FIX FINAL)
            # =========================================================
            elif text_clean in ["mi resumen", "resumen", "perfil", "/perfil", "mi perfil"]:
                send_telegram_action(chat_id, "typing") 
                
                # 1. Cargar perfil fresco y evaluar Tier
                user = get_user_profile(user_id)
                tier, days_left = stripeairegpt.evaluate_user_tier(user)
                is_prem = tier in ['PREMIUM', 'TRIAL']

                texto_plan = f"TRIAL ({days_left} días restantes)" if tier == 'TRIAL' else tier

                # 2. 🚩 LLAMADA ÚNICA AL MOTOR DE TARJETAS (Sin lógica duplicada)
                card_resumen = cards.generate_summary_card(
                    user_name=first_name,
                    alerts=user.get('alerts', {}),
                    vehicle=user.get('vehicle', {}),
                    locations=user.get('locations', {}),
                    plan_status=texto_plan,
                    transport_data=user.get('profile_transport', {}),
                    health_data=user.get('health_profile', {})
                )
                
                # 3. Obtener botones dinámicos usando la función de cards.py
                botones = cards.get_summary_buttons(user.get('locations', {}), is_premium=is_prem)

                # 4. Envío unificado
                send_telegram(chat_id, card_resumen, markup=botones)
                return {'statusCode': 200, 'body': 'OK'}

        print(f"📨 [MSG] User: {user_id} | Content: {user_content}") # LOG CRITICO

        save_interaction_and_draft(user_id, first_name, lat, lon)
        
        # --- 🛡️ FIX DE MEMORIA Y SANITIZACIÓN (Anti-Decimal Error & Fugas de Texto) ---
        locs = user_profile.get('locations', {})
        alerts = user_profile.get('alerts', {})
        veh = user_profile.get('vehicle', {})
        transp = user_profile.get('profile_transport', {})
        salud = user_profile.get('health_profile', {})
        
        # 1. 🛡️ EVALUACIÓN DE TIER PARA MEMORIA (Cerebro del Gatekeeper)
        tier_memoria, _ = stripeairegpt.evaluate_user_tier(user_profile)

        # 2. Asegurar diccionarios para evitar errores .get() o de iteración
        if not isinstance(alerts, dict): alerts = {}
        if not isinstance(transp, dict): transp = {}
        if not isinstance(salud, dict): salud = {}

        # 3. Construir contexto para forzar el "Bundle" de Rutina y Salud
        m_curr = transp.get('medio', 'No definido')
        
        # FIX QUIRÚRGICO 1: Convertir horas a float para evitar error Decimal
        try:
            h_curr = float(transp.get('horas', 0))
        except:
            h_curr = 0
        
        memoria_str = f"ESTATUS ACTUAL DEL USUARIO:\n"
        memoria_str += f"- Plan: {tier_memoria}\n"
        memoria_str += f"- Lugares Guardados: " + ", ".join([v.get('display_name', k) for k, v in locs.items()]) + "\n"
        
        # 🔒 CEGUERA SELECTIVA: Solo pasamos detalles sensibles si es Premium
        if tier_memoria in ['PREMIUM', 'TRIAL']:
            memoria_str += f"- Rutina de Transporte: Modo {m_curr}, Tiempo {h_curr} hrs\n"
            conds = [v.get('condition', k) for k, v in salud.items() if v.get('active')]
            memoria_str += f"- Perfil de Salud: " + (", ".join(conds) if conds else "Ninguno") + "\n"
        else:
            # Si es FREE, GPT sabe que existen campos de salud/rutina pero NO su contenido.
            # Esto evita que los "chismee" por texto natural.
            memoria_str += f"- Rutina de Transporte: 🔒 BLOQUEADO (Premium Required - No mencionar detalles)\n"
            memoria_str += f"- Perfil de Salud: 🔒 BLOQUEADO (Premium Required - No mencionar detalles)\n"
        
        veh_info = f"Placa terminación {veh.get('plate_last_digit')} (Holo {veh.get('hologram')})" if veh.get('active') else "No registrado"
        memoria_str += f"- Vehículo: {veh_info}\n"

        # 4. Limpiar diccionarios de tipos Decimal antes del json.dumps
        def clean_decimals(obj):
            if isinstance(obj, list): return [clean_decimals(i) for i in obj]
            elif isinstance(obj, dict): return {k: clean_decimals(v) for k, v in obj.items()}
            elif isinstance(obj, Decimal): return float(obj)
            return obj

        alerts_cleaned = clean_decimals(alerts)
        memoria_str += f"- Configuración de Alertas: {json.dumps(alerts_cleaned)}"
        # -------------------------------------------------------
        
        has_casa, has_trabajo = 'casa' in locs, 'trabajo' in locs
        forced_tag = None
        system_extra = "NORMAL"

        draft = user_profile.get('draft_location')
        if draft and isinstance(draft, dict) and 'lat' in draft:
            system_extra = (
                f"ESTADO: PENDING_NAME. El usuario envió coordenadas ({draft.get('lat')}, {draft.get('lon')}) "
                f"y ahora está proporcionando el nombre para este lugar. "
                f"Si el usuario escribe un nombre (ej. 'Polanco' o 'Circuito'), "
                f"DEBES usar la herramienta 'guardar_ubicacion_personalizada' con ese nombre."
            )
        elif not has_casa: 
            system_extra = "ONBOARDING 1: Pide CASA"
        elif not has_trabajo: 
            system_extra = "ONBOARDING 2: Pide TRABAJO"
        
        # 1. Prioridad: Si hay mapa en este mensaje (Override total)
        if lat:
            # --- FIX ÍTEM 7: REPORTE EFÍMERO INMEDIATO ---
            sys_state = table.get_item(Key={'user_id': 'SYSTEM_STATE'}).get('Item', {})
            current_phase = sys_state.get('last_contingency_phase', 'None')
            
            # 🚀 FIX ANTI-CRASHEO: Calculamos la variable premium localmente 
            tier_gps, _ = stripeairegpt.evaluate_user_tier(user_profile)
            es_premium_gps = tier_gps in ['PREMIUM', 'TRIAL']
            
            # 1. Generamos la tarjeta visual del lugar exacto
            report_text, calidad = generate_report_card(
                first_name, "Ubicación Actual", lat, lon, 
                vehicle=veh, contingency_phase=current_phase, 
                user_profile=user_profile, 
                is_premium=es_premium_gps # <--- ADIÓS CRASHEO, ADIÓS CANDADO
            )
            
            # 2. Interfaz limpia: Quitamos la pregunta de guardar y dejamos solo Mi Perfil
            markup_guardado = {"inline_keyboard": [[{"text": "👤 Mi Perfil", "callback_data": "ver_resumen"}]]}
            
            # 3. Seleccionamos el banner local basado en la calidad efímera
            mapa_archivos = {
                "Buena": "banner_buena.png", "Regular": "banner_regular.png", "Mala": "banner_mala.png",
                "Muy Mala": "banner_muy_mala.png", "Extremadamente Mala": "banner_extrema.png"
            }
            calidad_clean = calidad.replace("Extremadamente Alta", "Extremadamente Mala").replace("Muy Alta", "Muy Mala").replace("Alta", "Mala")
            nombre_png = mapa_archivos.get(calidad_clean, "banner_regular.png")
            
            import os
            directorio_actual = os.path.dirname(os.path.abspath(__file__))
            ruta_imagen = os.path.join(directorio_actual, "banners", nombre_png)
            
            # 4. Enviamos Foto + Reporte y CORTAMOS (no va a GPT)
            send_telegram_photo_local(chat_id, ruta_imagen, report_text, markup=markup_guardado)
            return {'statusCode': 200, 'body': 'OK'}
        
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
        # =========================================================
        # 🛠️ PROCESAMIENTO DE HERRAMIENTAS (FASE 1)
        # =========================================================
        if ai_msg.tool_calls:
            print(f"🛠️ [TOOL] GPT wants to call: {len(ai_msg.tool_calls)} tools")
            gpt_msgs.append(ai_msg)

            # =========================================================
            # 🛠️ ORQUESTADOR MODULAR UNIFICADO (CON GATEKEEPER)
            # =========================================================
            
            paywall_enviado = False
            
            for tc in ai_msg.tool_calls:
                fn = tc.function.name
                args = json.loads(tc.function.arguments)
                r = ""

                # --- 🛡️ PASO 1: EVALUACIÓN DE TIER Y PERMISOS ---
                user_fresco = get_user_profile(user_id)
                tier, days_left = stripeairegpt.evaluate_user_tier(user_fresco)
                is_prem_val = tier in ['PREMIUM', 'TRIAL']
                
                # Mapeo de herramientas a acciones de negocio (Cerebro Maestro)
                action_map = {
                    "guardar_ubicacion_personalizada": "add_location",
                    "configurar_recordatorio": "alertas",
                    "configurar_alerta_ias": "alertas",
                    "configurar_alerta_por_umbral": "alertas",
                    "configurar_alerta_contingencia": "alertas", # 🚩 Mapeado a alertas para el cadenero
                    "guardar_perfil_salud": "guardar_salud",
                    "guardar_salud": "guardar_salud",
                    "configurar_transporte": "configurar_transporte",
                    "obtener_calendario_mensual": "movilidad_mensual",
                    "consultar_verificacion": "movilidad_mensual"
                }
                
                action_to_check = action_map.get(fn, fn)
                print(f"DEBUG: GPT llamó a la función '{fn}'. Mapeada a la acción: '{action_to_check}'.")
                
                # Preguntamos a business_logic si se permite la acción
                allowed, reason = business_logic.is_action_allowed(user_fresco, action_to_check)

                if not allowed:
                    # 🛡️ SOLO ENVIAMOS UN PAYWALL POR TURNO (Evita el "metralleo")
                    if not paywall_enviado:
                        texto_pw, botones_pw = stripeairegpt.get_paywall_response(tier, days_left, action_to_check, str(user_id))
                        send_telegram(chat_id, texto_pw, markup=botones_pw)
                        paywall_enviado = True # Marcamos que ya cumplimos
                        print(f"💳 [PAYWALL] Tarjeta enviada por primera vez en este flujo.")

                    # 🚩 Para GPT siempre registramos el error, pero ya sin mandar más mensajes a Telegram
                    r = f"ERROR_SUSCRIPCION: Requiere plan Premium. Motivo: {reason}. Tarjeta ya enviada."
                    print(f"🚫 [GATEKEEPER] Bloqueado: {fn} para User: {user_id}")
                
                else:
                    # ✅ PASO 2: EJECUCIÓN PERMITIDA
                    print(f"🔧 [EXEC] Tool: {fn} | Tier: {tier}")

                    # --- 🎯 CONECTOR ÚNICO HOMOLOGADO (AJUSTE A) ---
                    if fn == "guardar_ubicacion_personalizada":
                        r = tools_logic.ejecutar_guardar_ubicacion(
                            user_id, 
                            args.get('nombre'), 
                            lat=args.get('lat'), 
                            lon=args.get('lon'), 
                            is_premium=is_prem_val # Sello de destino flexible activado
                        )

                    elif fn == "eliminar_ubicacion":
                        # Forzamos el borrado radical en DynamoDB
                        r = tools_logic.ejecutar_borrar_ubicacion(user_id, args.get('nombre_ubicacion'))

                    elif fn == "renombrar_ubicacion":
                        # Activamos el renombrado con lógica de destino flexible
                        r = tools_logic.ejecutar_renombrar_ubicacion(
                            user_id, 
                            args.get('nombre_actual'), 
                            args.get('nombre_nuevo')
                        )
                        
                    # --- 1. ESCRITURA Y CONFIGURACIÓN ---
                    elif fn in ["configurar_transporte", "guardar_perfil_salud", "guardar_salud", "configurar_auto", "configurar_alerta_ias", "configurar_alerta_contingencia", "configurar_recordatorio"]:
                        if fn == "configurar_transporte":
                            r = tools_logic.ejecutar_configurar_transporte(user_id, args.get('medio'), args.get('horas_al_dia', args.get('horas', 2)))
                        elif fn in ["guardar_perfil_salud", "guardar_salud"]:
                            r = tools_logic.ejecutar_guardar_salud(user_id, args.get('tipo_padecimiento', args.get('condicion')))
                        elif fn == "configurar_auto":
                            r = tools_logic.ejecutar_configurar_auto(user_id, args.get('ultimo_digito'), args.get('hologram', args.get('holograma', '0')))
                        elif fn == "configurar_alerta_contingencia":
                            r = tools_logic.ejecutar_configurar_alerta_contingencia(user_id, args.get('activar', True))
                        elif fn == "configurar_recordatorio": 
                            # FIX 1: Mapeo directo para reportes por horario (Casa 8am / Trabajo 10am)
                            r = tools_logic.configure_schedule_alert(user_id, args.get('nombre_ubicacion'), args.get('hora'), args.get('dias', 'diario'))
                        elif fn in ["configurar_alerta_ias", "configurar_alerta_por_umbral"]:
                            r = tools_logic.ejecutar_configurar_alerta_ias(user_id, args.get('nombre_ubicacion'), args.get('umbral_ias', args.get('umbral', 100)))

                    # --- 2. CONSULTAS VISUALES (Ubicaciones, Movilidad, Resumen) ---
                    elif fn in ["consultar_resumen_configuracion", "consultar_perfil"]: 
                        # 1. 🛡️ FORZAR LECTURA FRESCA (Ignoramos variables previas de la Lambda)
                        user_fresh = get_user_profile(user_id)
                        
                        # 🚀 FIX 1: Atrapamos days_left de la función
                        tier_real, days_left = stripeairegpt.evaluate_user_tier(user_fresh)
                        is_prem_real = tier_real in ['PREMIUM', 'TRIAL']

                        # 🚀 FIX 2: Creamos el texto dinámico para GPT
                        texto_plan = f"TRIAL ({days_left} días restantes)" if tier_real == 'TRIAL' else tier_real

                        print(f"🕵️ FUGA CHECK: User {user_id} pidiendo resumen. Tier Real: {tier_real}")
                        print(f"🕵️ DATA CHECK: Salud en DB: {user_fresh.get('health_profile')} | Transp en DB: {user_fresh.get('profile_transport')}")
                        print(f"🔍 [AUDIT_TEXT] Generando para: {user_id}")
                        print(f"🔍 [AUDIT_TEXT] Tier: {tier_real}")
                        print(f"🔍 [AUDIT_TEXT] Alertas Raw: {user_fresh.get('alerts')}") 
                        print(f"🔍 [AUDIT_TEXT] Locations: {user_fresh.get('locations')}")

                        # 2. Invocamos a cards.py pasándole el texto formateado
                        card_res = cards.generate_summary_card(
                            user_name=first_name, 
                            alerts=user_fresh.get('alerts', {}), 
                            vehicle=user_fresh.get('vehicle', {}), 
                            locations=user_fresh.get('locations', {}), 
                            plan_status=texto_plan, # <--- 🚀 FIX 3: Variable inyectada
                            transport_data=user_fresh.get('profile_transport', {}), 
                            health_data=user_fresh.get('health_profile', {})
                        )
                        
                        # 3. Mandamos la tarjeta con los botones (Go Premium si es FREE)
                        send_telegram(chat_id, card_res, markup=cards.get_summary_buttons(user_fresh.get('locations', {}), is_premium=is_prem_real))
                        
                        # 4. Señal para el silenciador
                        r = "Éxito: Interfaz visual de resumen enviada."

                    elif fn in ["consultar_ubicaciones", "consultar_ubicaciones_guardadas"]:
                        l_list = [f"📍 **{v.get('display_name', k).capitalize()}**" for k, v in locs.items() if v.get('active')]
                        card_locs = cards.CARD_MY_LOCATIONS.format(user_name=first_name, locations_list="\n".join(l_list) if l_list else "• Ninguna", footer=cards.BOT_FOOTER)
                        send_telegram(chat_id, card_locs, markup=cards.get_locations_buttons(locs))
                        r = "Éxito: Interfaz visual de ubicaciones enviada."

                    elif fn in ["consultar_verificacion", "consultar_hoy_no_circula", "obtener_calendario_mensual"]:
                        if not veh.get('active'): 
                            r = "⚠️ No tienes auto registrado."
                        else:
                            p_d, h_d = str(veh.get('plate_last_digit')), str(veh.get('hologram'))
                            u_ask = user_content.lower()
                            
                            # A. Calendario Mensual
                            if any(x in u_ask for x in ["mes", "calendario", "fechas", "lista"]) or fn == "obtener_calendario_mensual":
                                now = get_mexico_time()
                                meses_es = {1:"Enero", 2:"Febrero", 3:"Marzo", 4:"Abril", 5:"Mayo", 6:"Junio", 7:"Julio", 8:"Agosto", 9:"Septiembre", 10:"Octubre", 11:"Noviembre", 12:"Diciembre"}
                                lista_dias = get_monthly_prohibited_dates(p_d, h_d, now.year, now.month)
                                txt_sem, txt_sab = get_restriction_summary(p_d, h_d)
                                card_mes = cards.CARD_HNC_DETAILED.format(
                                    mes_nombre=meses_es[now.month], plate=p_d, color=veh.get('engomado','N/A'),
                                    holo=h_d.upper(), verificacion_txt=cards.get_verification_period(p_d, h_d),
                                    dias_semana_txt=txt_sem, sabados_txt=txt_sab,
                                    lista_fechas="\n".join(lista_dias) if lista_dias else "¡Circulas todo el mes! 🎉",
                                    multa_cdmx="$2,171", multa_edomex="$2,171", footer=cards.BOT_FOOTER
                                )
                                send_telegram(chat_id, card_mes, markup=cards.get_hnc_buttons())
                                # 🚩 AGREGADO "visual" para silenciador
                                r = "Éxito: Calendario mensual visual enviado."
                        
                            # B. Verificación
                            elif "verifi" in u_ask or fn == "consultar_verificacion":
                                card_v = cards.CARD_VERIFICATION.format(plate_info=p_d, engomado=veh.get('engomado','N/A'), period_txt=cards.get_verification_period(p_d, h_d), deadline=get_verification_deadline(cards.get_verification_period(p_d, h_d)), fine_amount="2,457", footer=cards.BOT_FOOTER)
                                send_telegram(chat_id, card_v, markup=cards.get_hnc_buttons())
                                # 🚩 AGREGADO "visual" para silenciador
                                r = "Éxito: Tarjeta visual de verificación enviada."
                        
                            # C. Veredicto Diario
                            else:
                                offset = 1 if "mañana" in u_ask else 2 if "pasado mañana" in u_ask else 0
                                label = "Mañana" if offset == 1 else "Pasado Mañana" if offset == 2 else "Hoy"
                                target_date = (get_mexico_time() + timedelta(days=offset)).strftime("%Y-%m-%d")
                                can_drive, r_short, _ = cards.check_driving_status(p_d, h_d, target_date)
                                card_day = cards.CARD_HNC_RESULT.format(fecha_str=target_date, dia_semana=label, plate_info=p_d, hologram=h_d, status_emoji="🟢" if can_drive else "🔴", status_title="SÍ CIRCULA" if can_drive else "NO CIRCULA", status_message="", reason=r_short, footer=cards.BOT_FOOTER)
                                send_telegram(chat_id, card_day, markup=cards.get_hnc_buttons())
                                # 🚩 AGREGADO "visual" para silenciador
                                r = f"Éxito: Veredicto visual para {label} enviado."

                    #-----
                    elif fn == "consultar_calidad_aire":
                        in_lat = args.get('lat', 0)
                        in_lon = args.get('lon', 0)
                        in_name = args.get('nombre_ubicacion', 'tu ubicación')
                        
                        # 1. RESOLUCIÓN DE COORDENADAS
                        if in_lat == 0 or in_lon == 0:
                            key = resolve_location_key(user_id, in_name)
                            if not key: key = resolve_location_key(user_id, user_content)
                            
                            if key and key in locs:
                                in_lat = float(locs[key].get('lat', 0))
                                in_lon = float(locs[key].get('lon', 0))
                                in_name = locs[key].get('display_name', key.capitalize())
                        
                        # 2. INTENTO DE ENVÍO DE TARJETA VISUAL
                        if in_lat != 0 and in_lon != 0:
                            # Obtenemos fase de contingencia actual del sistema
                            sys_state = table.get_item(Key={'user_id': 'SYSTEM_STATE'}).get('Item', {})
                            current_phase = sys_state.get('last_contingency_phase', 'None')
    
                            # Generamos el texto del reporte y la categoría de calidad
                            report_text, calidad = generate_report_card(
                                first_name, in_name, in_lat, in_lon, 
                                vehicle=veh, contingency_phase=current_phase,
                                user_profile=user_profile, is_premium=is_prem_val
                            )
                            
                            # --- SELECCIÓN DINÁMICA DE BANNER ---
                            mapa_archivos = {
                                "Buena": "banner_buena.png", 
                                "Regular": "banner_regular.png", 
                                "Mala": "banner_mala.png", 
                                "Muy Mala": "banner_muy_mala.png", 
                                "Extremadamente Mala": "banner_extrema.png"
                            }
                            # Limpieza por si la API devuelve "Alta" en vez de "Mala"
                            calidad_clean = calidad.replace("Extremadamente Alta", "Extremadamente Mala").replace("Muy Alta", "Muy Mala").replace("Alta", "Mala")
                            nombre_png = mapa_archivos.get(calidad_clean, "banner_regular.png")
                            
                            import os
                            directorio_actual = os.path.dirname(os.path.abspath(__file__))
                            ruta_imagen = os.path.join(directorio_actual, "banners", nombre_png)
                            
                            # ENVIAMOS FOTO LOCAL + REPORTE
                            send_telegram_photo_local(chat_id, ruta_imagen, report_text, markup=cards.get_exposure_button())
                            
                            # 🚩 LA SEÑAL PARA EL SILENCIADOR:
                            r = f"Éxito: Reporte visual enviado para {in_name}."
                        else:
                            # Sin coordenadas, r no tiene la palabra "visual", permitiendo que GPT pida la ubicación.
                            r = f"Error: No encontré coordenadas para '{in_name}'."

                gpt_msgs.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": str(r)})

            # --- CIERRE MAESTRO TRAS EL BUCLE FOR ---
            print(f"🔄 [GPT] Resolviendo respuesta final tras {len(ai_msg.tool_calls)} herramientas.")
            final_res = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_msgs, temperature=0.3)
            final_text = final_res.choices[0].message.content

            # 🤫 REGLA DE SILENCIO (FIXED: Evita el error 'ChatCompletionMessage' has no attribute 'get')
            silenciar = False
            for m in gpt_msgs:
                # Solo procesamos diccionarios (nuestras respuestas de tools)
                if isinstance(m, dict) and m.get('role') == 'tool':
                    contenido = str(m.get('content', '')).lower()
                    if "visual" in contenido or "suscripcion" in contenido:
                        silenciar = True
                        break
            
            if silenciar:
                print("🤫 SILENCIO: Interfaz enviada. Matando texto redundante.")
                final_text = ""

        else:
            # Si GPT no usó herramientas, usamos su respuesta directa
            final_text = ai_msg.content

        # =========================================================
        # 📤 SALIDA ÚNICA A TELEGRAM (SILENCIO BLINDADO + ANTI-ERROR 400)
        # =========================================================
        historial_ejecucion = str(gpt_msgs)
        
        # 1. Definimos qué eventos deben silenciar el texto de GPT
        # IMPORTANTE: El nombre de esta lista debe coincidir con el del bucle de abajo
        palabras_clave_interfaz = [
            "Reporte visual", 
            "Tarjeta visual", 
            "Interfaz visual", 
            "Veredicto visual", 
            "Calendario mensual"
        ]
        
        # 🚩 FIX AQUÍ: Se cambió 'palabras_clave_silencio' por 'palabras_clave_interfaz'
        se_envio_foto_o_grafica = any(f in historial_ejecucion for f in palabras_clave_interfaz)

        # 2. Lógica de Decisión de Silencio
        if se_envio_foto_o_grafica:
            print("🤫 SILENCIO: Tarjeta visual o gráfica enviada. Evitando texto redundante de GPT.")
            return {'statusCode': 200, 'body': 'OK'}

        # 3. Flujo de Mensajería de Texto (Feedback y Onboarding)
        markup_out = None
        if forced_tag:
            markup_out = get_inline_markup(forced_tag)
            final_text = "📍 **Ubicación recibida.**\n\n👇 Confirma para guardar:"

        if final_text and final_text.strip():
            # --- FIX ANTI-ERROR 400 (LIMPIEZA PROFUNDA) ---
            if "Ubicación recibida" not in final_text:
                # Quitamos asteriscos y corchetes, pero DEJAMOS el guion bajo (_) 
                # para que los links de Google Maps no se rompan.
                safe_final_text = final_text.replace("*", "").replace("[", "(").replace("]", ")")
            else:
                safe_final_text = final_text 
            
            if safe_final_text.strip():
                send_telegram(chat_id, safe_final_text, markup_out)
            
        return {'statusCode': 200, 'body': 'OK'}

    # === ¡AQUÍ ESTÁ EL FIX! ===
    # El error de la línea 2595 es porque faltaba este cierre para el try principal
    except Exception as e:
        print(f"🔥 [CRITICAL FAIL] Error en el flujo final: {e}")
        return {'statusCode': 500, 'body': str(e)}
