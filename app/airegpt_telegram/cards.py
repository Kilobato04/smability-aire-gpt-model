# app/cards.py
BOT_VERSION = "v0.6.0 (Live API Connect)"
BOT_FOOTER = f"🤖 AIreGPT {BOT_VERSION}"

IAS_INFO = {
    "Buena": {"msg": "Aire limpio.", "rec": "¡Disfruta el exterior!", "emoji": "🟢"},
    "Regular": {"msg": "Calidad aceptable.", "rec": "Sensibles: moderar esfuerzo.", "emoji": "🟡"},
    "Mala": {"msg": "Podría causar molestias.", "rec": "Evita ejercicio intenso fuera.", "emoji": "🟠"},
    "Muy Mala": {"msg": "Riesgo alto.", "rec": "No salgas. Cierra ventanas.", "emoji": "🔴"},
    "Extremadamente Mala": {"msg": "¡Peligro!", "rec": "Urgencia médica si hay síntomas.", "emoji": "🟣"}
}

# --- NUEVO HELPER (Requerido por v0.6.0) ---
def get_emoji_for_quality(calidad):
    """Extrae el emoji de forma segura para el chatbot"""
    return IAS_INFO.get(calidad, {}).get("emoji", "⚪")

def get_health_advice(calidad, user_condition=None):
    advice = {
        "Buena": "Disfruta tus actividades al aire libre sin restricciones.",
        "Regular": "Reduce actividades intensas si eres muy sensible a la contaminación.",
        "Mala": "Evita el ejercicio vigoroso al aire libre. Grupos sensibles deben quedarse en interiores.",
        "Muy Mala": "Permanece en interiores con ventanas cerradas. No realices esfuerzo físico afuera.",
        "Extremadamente Mala": "¡Emergencia! Quédate en casa. Usa mascarilla N95/KN95 si necesitas salir."
    }
    cat = calidad.replace("Extremadamente Alta", "Extremadamente Mala").replace("Muy Alta", "Muy Mala").replace("Alta", "Mala")
    base_rec = advice.get(cat, "Toma precauciones al aire libre.")
    
    # Si el usuario no tiene perfil de salud, mandamos el texto limpio
    if not user_condition or user_condition.lower() == "ninguno": 
        return base_rec
        
    # Si el usuario TIENE perfil de salud (ej. Asma), personalizamos:
    if cat in ["Mala", "Muy Mala", "Extremadamente Mala"]:
        return f"⚠️ **Por tu {user_condition}:** {base_rec}"
    elif cat == "Regular":
        return f"ℹ️ **Por tu {user_condition}:** Considera reducir el esfuerzo físico."
    else:
        return f"✅ **Buena noticia:** El aire es seguro para tu {user_condition}."

# --- PLANTILLAS DE TARJETAS ---

CARD_ONBOARDING = """👋 **¡Bienvenido a AIreGPT!**
Para protegerte, necesito configurar tus dos bases principales. Así podré avisarte antes de que respires aire malo.

🏠 **1. Casa:** Para avisarte al despertar o fines de semana.
🏢 **2. Trabajo:** Para avisarte antes de salir a tu trayecto.

👇 **PASO 1:**
Por favor, **envíame la ubicación de tu CASA** (toca el clip 📎 y selecciona "Ubicación").
{footer}"""

CARD_ONBOARDING_WORK = """✅ **¡Casa guardada!**

🚀 **PASO 2:**
Ahora, envíame la ubicación de tu **TRABAJO** (o escuela) para activar las alertas de movilidad.
*(Toca el clip 📎 y selecciona "Ubicación")*
{footer}"""

# ACTUALIZADA: Se agregó {trend_arrow} para aprovechar el dato de la nueva API
CARD_REPORT = """🌤️ **{greeting}, {user_name}!**
Aquí tienes el reporte para **{location_name}**:
[🔗 Abrir en Google Maps]({maps_url})
📍 {region} • 🕒 {report_time}

{risk_circle} **Calidad {risk_category} ({ias_value} pts)**
☣️ **Contaminante dominante:** {pollutant}

🌡️ {temp}°C | 💧 {humidity}% | 🌬️ {wind_speed} km/h

📈 **Pronóstico (Próximas hrs):**
{forecast_block}

🛡️ **Salud:** {health_recommendation}
{footer}"""

CARD_ALERT_IAS = """🚨 **¡ALERTA DE CALIDAD DEL AIRE!** 🚨
Hola {user_name}, la contaminación en **{location_name}** ha superado tu límite de seguridad.

{risk_circle} **Calidad {risk_category} ({ias_value} pts)**
☣️ **Contaminante principal:** {pollutant}
*(Tu umbral configurado es: {threshold} pts)*

📈 **Tendencia:** {forecast_msg}

🛡️ **Acción inmediata:** {health_recommendation}
{footer}"""

CARD_REMINDER = """⏰ **{greeting}, {user_name}!**
Aquí tienes el reporte para **{location_name}**:
[🔗 Abrir en Google Maps]({maps_url})
📍 {region} • 🕒 {report_time}

{risk_circle} **Calidad {risk_category} ({ias_value} pts)**
☣️ **Contaminante dominante:** {pollutant}

🌡️ {temp}°C | 💧 {humidity}% | 🌬️ {wind_speed} km/h

📈 **Pronóstico (Próximas hrs):**
{forecast_block}

🛡️ **Salud:** {health_recommendation}
{footer}"""

CARD_CONTINGENCY = """🚨 **¡CONTINGENCIA AMBIENTAL!** 🚨
🌎 Zona Metropolitana del Valle de México
🕒 {report_time}

⚠️ **FASE ACTIVA:** {phase}
☣️ **Detalle:** {pollutant_info}
📍 **Estación Crítica:** {station_info}

🛑 **Restricciones Vehiculares:**
{restrictions_txt}
📄 [Leer Comunicado Oficial]({oficial_link})

🛡️ **Acción:**
• Cierra ventanas y evita salir.
• No realices ejercicio al aire libre.
📌 *La contingencia se mantiene vigente hasta que la CAMe emita el comunicado oficial de suspensión. No saques tu auto hasta confirmarlo.*

{footer}"""

CARD_CONTINGENCY_LIFTED = """🟢 **CONTINGENCIA SUSPENDIDA**
🌎 Zona Metropolitana del Valle de México
🕒 {report_time}

🎉 **¡Buenas noticias!**
La CAMe informa que las condiciones del aire han mejorado.

🚗 **Movilidad:**
Se levantan las restricciones del Doble Hoy No Circula. Tu auto vuelve a su calendario normal.
📄 [Leer Comunicado Oficial]({oficial_link})

_Fuente: CAMe / Smability_
{footer}"""

CARD_HNC_RESULT = """🚗 **HOY NO CIRCULA**
📅 **Fecha:** {fecha_str} ({dia_semana})
🚘 **Auto:** {plate_info} (Holo {hologram})

{status_emoji} **{status_title}**
{status_message}

⚠️ *Razón:* {reason}
{footer}"""

CARD_HNC_DETAILED = """🚗 **Reporte Mensual HNC: {mes_nombre}**
🚘 **Placa:** ...{plate} | **Engomado:** {color}
**Holograma:** {holo}

📅 **VERIFICACIÓN:** {verificacion_txt}

📅 **DÍAS QUE NO CIRCULAS:**
{dias_semana_txt}
{sabados_txt}
🕒 **Horario:** 05:00 - 22:00 hrs

📋 **Fechas específicas este mes:**
{lista_fechas}

👮 **RIESGO DE MULTA (Si omites):**
🏛️ **CDMX:** {multa_cdmx} + Corralón
🌲 **Edomex:** {multa_edomex} + Retención

📝 *Alertas automáticas activadas a las 20:00 hrs.*
{footer}"""

CARD_SUMMARY = """
📊 **RESUMEN DE CUENTA**
👤 {user_name} | Plan: {plan_status}

🚨 **Alerta Contingencia:** {contingency_status}

📍 **Tus Ubicaciones:**
{locations_list}

🚗 **Tu Auto:**
{vehicle_info}

🔔 **Alertas Aire (Por Nivel/Umbral):**
{alerts_threshold}

⏰ **Reportes Aire (Programados):**
{alerts_schedule}

🚫 **Aviso Hoy No Circula:**
{hnc_reminder}

💡 *{tip_footer}*
"""

CARD_VERIFICATION = """🚗 **ESTATUS DE VERIFICACIÓN**
🚘 **Auto:** {plate_info} | {engomado}

📅 **Tu Periodo:**
{period_txt}

⚠️ **Fecha Límite:** {deadline}

💰 **MULTA (Extemporánea):**
💸 **${fine_amount} MXN** (20 UMAS)
+ Corralón si eres detenido circulando.

💡 *Recuerda agendar tu cita una semana antes.*
{footer}"""

CARD_MY_LOCATIONS = """📍 **MIS UBICACIONES GUARDADAS**
👤 {user_name}

{locations_list}

👇 *Usa los botones para consultar o eliminar.*
{footer}"""

# --- NUEVA TARJETA: EXPOSICIÓN (GAMIFICACIÓN) ---
CARD_EXPOSICION = """{emoji_alerta} *Reporte de Desgaste Acelerado*
👤 {user_name}

Ayer te expusiste a una calidad del aire que le pasó factura a tu cuerpo. 👇

{emoji_cigarro} Respiraste el equivalente a *{cigarros} cigarros invisibles* en tu rutina.

{emoji_edad} Esto sumó *{dias} días extra* de desgaste a tu Edad Urbana.

💡 *Hoy en tu zona principal ({mun_casa}) la calidad es {calidad_hoy}.* {mensaje_hoy}

_*(Dato científico: Promedio de exposición integral {promedio_riesgo} µg/m³ eq.)*_
{footer}"""

# --- BOTONES DE EXPOSICIÓN Y ONBOARDING ---
def get_exposure_button():
    return {"inline_keyboard": [[{"text": "🚬 ¿Cuántos cigarros respiré ayer?", "callback_data": "CHECK_EXPOSURE"}]]}

def get_transport_buttons():
    return {"inline_keyboard": [
        [{"text": "🚇 Metro / Tren", "callback_data": "SET_TRANS_metro"}, 
         {"text": "🚐 Combi / Bus", "callback_data": "SET_TRANS_combi"}],
        [{"text": "🚗 Auto (Ventanillas)", "callback_data": "SET_TRANS_auto_ventana"},
         {"text": "❄️ Auto (Con A/C)", "callback_data": "SET_TRANS_auto_ac"}],
        [{"text": "🚲 Bici / Caminar", "callback_data": "SET_TRANS_bicicleta"},
         {"text": "🏠 Hago Home Office", "callback_data": "SET_TRANS_home_office"}]
    ]}

def get_time_buttons():
    return {"inline_keyboard": [
        [{"text": "⏱️ ~1 Hora", "callback_data": "SET_TIME_1"}, 
         {"text": "⏱️ ~2 Horas", "callback_data": "SET_TIME_2"}],
        [{"text": "⏱️ ~3 Horas", "callback_data": "SET_TIME_3"}, 
         {"text": "⏱️ 4+ Horas", "callback_data": "SET_TIME_4"}]
    ]}

# --- 1. HELPER VISUAL DE DÍAS ---
def format_days_text(days_list):
    if not days_list or len(days_list) == 7: return "Diario"
    if days_list == [0,1,2,3,4]: return "Lun-Vie"
    if days_list == [5,6]: return "Fin de Semana"
    names = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
    return ",".join([names[i] for i in days_list])


# --- 2. ACTUALIZAR FUNCIÓN GENERADORA DE RESUMEN ---
def generate_summary_card(user_name, alerts, vehicle, locations, plan_status):
    # Función auxiliar de limpieza local
    def clean(text):
        return str(text).replace("_", " ").replace("*", "").replace("[", "").replace("]", "")

    # a) Status Contingencia & Plan (LÓGICA CORREGIDA)
    safe_plan = clean(plan_status)
    is_premium = "PREMIUM" in safe_plan.upper() or "TRIAL" in safe_plan.upper()
    
    if is_premium:
        # Leemos el estado real de la BD. Si no existe la llave, asumimos False (Inactiva)
        # OJO: Si prefieres que por defecto esté activa para nuevos, cambia False a True
        is_active_db = alerts.get('contingency', False)
        contingency_status = "✅ **ACTIVA**" if is_active_db else "🔕 **DESACTIVADA**"
    else:
        contingency_status = "🔒 **BLOQUEADA** (Solo Premium)"
    
    # b) Ubicaciones
    locs = []
    if isinstance(locations, dict):
        for k, v in locations.items():
            safe_k = clean(k.capitalize())
            safe_name = clean(v.get('display_name','Ubicación'))
            locs.append(f"• **{safe_k}:** {safe_name}")
    loc_str = "\n".join(locs) if locs else "• *Sin ubicaciones guardadas*"

    # c) Vehículo
    veh_str = "• *Sin auto registrado*"
    if vehicle and vehicle.get('active'):
        digit = vehicle.get('plate_last_digit')
        holo = clean(vehicle.get('hologram'))
        veh_str = f"• Placa **{digit}** (Holo {holo})"

    # d) Alertas de Aire por UMBRAL (FILTRADO)
    threshold_list = []
    thresholds = alerts.get('threshold', {})
    for k, v in thresholds.items():
        # Validar que la ubicación exista
        if v.get('active') and k in locations: 
            safe_k = clean(k.capitalize())
            threshold_list.append(f"• {safe_k}: > {v.get('umbral')} pts")
    threshold_str = "\n".join(threshold_list) if threshold_list else "• *Sin alertas de umbral*"

    # e) Reportes de Aire PROGRAMADOS (FILTRADO)
    schedule_list = []
    schedules = alerts.get('schedule', {})
    for k, v in schedules.items():
        # Validar que la ubicación exista
        if v.get('active') and k in locations: 
            safe_k = clean(k.capitalize())
            days = v.get('days', [])
            days_txt = "Diario" if len(days)==7 else "Días selec."
            schedule_list.append(f"• {safe_k}: {v.get('time')} hrs ({days_txt})")
    schedule_str = "\n".join(schedule_list) if schedule_list else "• *Sin reportes programados*"

    # f) Recordatorio HOY NO CIRCULA
    if vehicle and vehicle.get('active'):
        hnc_str = "• 🚗 Encuentra las restricciones de HNC directamente en tus alertas y reportes de Aire."
    else:
        hnc_str = "• 🔕 Registra tu auto para ver restricciones." 

    # Footer
    tip = "💡 Tip: Escribe 'Cambiar hora alertas' para ajustar." if is_premium else "💎 Tip: Hazte Premium para activar Contingencias."

    return CARD_SUMMARY.format(
        user_name=clean(user_name),
        plan_status=safe_plan,
        contingency_status=contingency_status,
        locations_list=loc_str,
        vehicle_info=veh_str,
        alerts_threshold=threshold_str,
        alerts_schedule=schedule_str,
        hnc_reminder=hnc_str,
        tip_footer=tip
    )

# --- 3. ACTUALIZAR BOTONES DE RESUMEN (UPSELLING) ---
def get_summary_buttons(locations_dict, is_premium=False):
    """
    Genera botones de consulta para TODAS las ubicaciones guardadas.
    Argumentos:
      - locations_dict: El diccionario 'locations' directo de DynamoDB.
      - is_premium: Booleano para mostrar/ocultar botón de pago.
    """
    keyboard = []
    
    # 1. Fila de Consultas (Dinámica)
    # Creamos botones para CADA ubicación en el diccionario
    row_locs = []
    for key, val in locations_dict.items():
        # Nombre bonito para el botón
        label = val.get('display_name', key.capitalize())
        # Llave segura para el callback (ej. "CHECK_AIR_casa")
        safe_key = str(key).replace(" ", "_")
        
        row_locs.append({"text": f"💨 {label}", "callback_data": f"CHECK_AIR_{safe_key}"})
    
    # Si son muchas, las dividimos en filas de 2 para que no se vea feo
    # (Chunking list into size 2)
    for i in range(0, len(row_locs), 2):
        keyboard.append(row_locs[i:i+2])
    
    # 2. Fila de Upselling (Solo si es FREE)
    if not is_premium:
        keyboard.append([{"text": "💎 Activar Premium ($49)", "callback_data": "GO_PREMIUM"}])
    
    return {"inline_keyboard": keyboard}

# --- MODIFICADO: ELIMINAMOS BOTÓN DE VOLVER ---
def get_locations_buttons(locations_dict):
    keyboard = []
    # Fila de "Consultar Aire"
    row_check = []
    # Fila de "Eliminar"
    row_delete = []
    
    for key, val in locations_dict.items():
        label = key.capitalize()
        # Claves cortas para callback (evitar límite de bytes de Telegram)
        safe_key = key.upper().replace(" ", "_")[:15] 
        
        row_check.append({"text": f"💨 {label}", "callback_data": f"CHECK_AIR_{safe_key}"})
        row_delete.append({"text": f"🗑️ {label}", "callback_data": f"DELETE_LOC_{safe_key}"})
    
    if row_check: keyboard.append(row_check)
    if row_delete: keyboard.append(row_delete)
    
    return {"inline_keyboard": keyboard}

#Helper para confirmación de borrado
def get_delete_confirmation_buttons(location_key):
    return {"inline_keyboard": [
        [
            {"text": "✅ Sí, borrar todo", "callback_data": f"CONFIRM_DEL_{location_key.upper()}"},
            {"text": "❌ Cancelar", "callback_data": "CANCEL_DELETE"}
        ]
    ]}

# =====================================================================
# 🚗 MOTOR HNC V2, SALUD Y PRONÓSTICO (COMPARTIDO BOT Y SCHEDULER)
# =====================================================================
from datetime import datetime, timedelta

MATRIZ_SEMANAL = {5:0, 6:0, 7:1, 8:1, 3:2, 4:2, 1:3, 2:3, 9:4, 0:4}
ENGOMADOS = {5:"Amarillo", 6:"Amarillo", 7:"Rosa", 8:"Rosa", 3:"Rojo", 4:"Rojo", 1:"Verde", 2:"Verde", 9:"Azul", 0:"Azul"}


def format_forecast_block(timeline):
    if not timeline or not isinstance(timeline, list): return "➡️ Estable"
    block = ""
    cat_map = {"Bajo": "Buena", "Moderado": "Regular", "Alto": "Mala", "Muy Alto": "Muy Mala", "Extremadamente Alto": "Extrema"}
    emoji_map = {"Bajo": "🟢", "Moderado": "🟡", "Alto": "🟠", "Muy Alto": "🔴", "Extremadamente Alto": "🟣"}
    count = 0
    for t in timeline:
        if count >= 4: break
        riesgo = t.get('riesgo', 'Bajo')
        cat = cat_map.get(riesgo, "Regular")
        pol = t.get('dominante', '') # MATCH EXACTO CON TU JSON
        pol_str = f" • {pol}" if pol else ""
        block += f"`{t.get('hora')}` | {emoji_map.get(riesgo, '⚪')} {cat} ({t.get('ias')} pts){pol_str}\n"
        count += 1
    return block.strip()

def get_verification_period(plate_digit, hologram):
    if str(hologram).lower() in ['00', 'exento', 'hibrido']: return "🟢 EXENTO (No verifica)"
    try: d = int(plate_digit)
    except: return "⚠️ Revisar Placa"

    if d in [5, 6]: return "🟡 Ene-Feb / Jul-Ago"
    if d in [7, 8]: return "🌸 Feb-Mar / Ago-Sep"
    if d in [3, 4]: return "🔴 Mar-Abr / Sep-Oct"
    if d in [1, 2]: return "🟢 Abr-May / Oct-Nov"
    if d in [9, 0]: return "🔵 May-Jun / Nov-Dic"
    return "📅 Revisar Calendario"

def check_driving_status(plate_last_digit, hologram, date_str=None, contingency_phase="None"):
    """Retorna: (Puede_Circular: Bool, Razon_Corta: Str, Detalle_Visual: Str)"""
    try:
        if not date_str or date_str.lower() == "hoy":
            date_str = (datetime.utcnow() - timedelta(hours=6)).strftime("%Y-%m-%d")
            
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_week, day_month = dt.weekday(), dt.day
        
        holo = str(hologram).lower().replace("holograma", "").strip()
        plate = int(plate_last_digit)
        color = ENGOMADOS.get(plate, "Desconocido")

        if day_week == 6: return True, "Domingo libre", "🟢 CIRCULA (Es domingo)."
        
        if contingency_phase in ['Fase I', 'Fase 1', 'Fase II', 'Fase 2']:
            is_fase2 = 'II' in contingency_phase.upper() or '2' in contingency_phase
            if holo in ['2', 'foraneo']: return False, "Restricción Fase I/II", f"🔴 NO CIRCULA."
            if holo == '1':
                if is_fase2: return False, "Fase II Activa", "🔴 NO CIRCULA."
                if MATRIZ_SEMANAL.get(plate) == day_week: return False, "Día Habitual", f"🔴 NO CIRCULA."
                if (plate % 2 != 0): return False, "Fase I (Placas Impares)", "🔴 NO CIRCULA."
            if holo in ['0', '00', 'exento'] and not is_fase2:
                if MATRIZ_SEMANAL.get(plate) == day_week: return False, f"Fase I (Eng. {color})", f"🔴 NO CIRCULA."
            if holo in ['0', '00'] and is_fase2:
                if MATRIZ_SEMANAL.get(plate) == day_week: return False, f"Fase II (Eng. {color})", f"🔴 NO CIRCULA."

        if holo in ['0', '00', 'exento', 'hibrido', 'eléctrico']: return True, "Holograma Exento", "🟢 CIRCULA."
        
        if day_week < 5:
            if MATRIZ_SEMANAL.get(plate) == day_week: return False, f"Día Habitual", f"🔴 NO CIRCULA."
            return True, "Día Permitido", "🟢 CIRCULA."

        if day_week == 5:
            if holo in ['2', 'foraneo']: return False, "Sábado Holo 2", "🔴 NO CIRCULA."
            if holo == '1':
                sat_idx, is_impar = (day_month - 1) // 7 + 1, (plate % 2 != 0)
                if sat_idx == 5: return False, "5º Sábado", "🔴 NO CIRCULA."
                if is_impar and sat_idx in [1, 3]: return False, f"{sat_idx}º Sábado (Impar)", f"🔴 NO CIRCULA."
                if not is_impar and sat_idx in [2, 4]: return False, f"{sat_idx}º Sábado (Par)", f"🔴 NO CIRCULA."
                return True, "Sábado Permitido", "🟢 CIRCULA."
        return True, "Sin Restricción", "🟢 CIRCULA."
    except Exception: return True, "Error", "⚠️ Error al calcular."

def build_hnc_pill(vehicle, contingency_phase="None"):
    if not vehicle or not vehicle.get('active'): return ""
    
    plate = vehicle.get('plate_last_digit')
    holo = vehicle.get('hologram')
    color_auto = ENGOMADOS.get(int(plate), "Desconocido")
    
    can_drive, r_short, _ = check_driving_status(plate, holo, "hoy", contingency_phase)
    hnc_status = "🟢 CIRCULA" if can_drive else f"⛔ NO CIRCULA ({r_short})"
    
    pill = f"\n🚗 **Tu Auto Hoy:** {hnc_status} \n*(Placa term. {plate} | Holo {holo} | Eng. {color_auto})*"

    periodo_verif = get_verification_period(plate, holo)
    mes_actual_txt = {1:"Ene", 2:"Feb", 3:"Mar", 4:"Abr", 5:"May", 6:"Jun", 7:"Jul", 8:"Ago", 9:"Sep", 10:"Oct", 11:"Nov", 12:"Dic"}[(datetime.utcnow() - timedelta(hours=6)).month]
    if mes_actual_txt in periodo_verif and "EXENTO" not in periodo_verif.upper():
        pill += f"\n⚠️ **RECORDATORIO:** Estás en periodo de Verificación ({periodo_verif})."
        
    return pill
