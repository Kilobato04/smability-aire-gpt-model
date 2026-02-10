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

def get_health_advice(category, user_condition=None):
    base_rec = IAS_INFO.get(category, IAS_INFO["Regular"])["rec"]
    if not user_condition or user_condition.lower() == "ninguno": return base_rec
    if category in ["Mala", "Muy Mala", "Extremadamente Mala"]:
        return f"⚠️ **Atención por tu {user_condition}:** {base_rec} El aire actual puede agravar tus síntomas."
    elif category == "Regular":
        return f"ℹ️ **Por tu {user_condition}:** Considera reducir el esfuerzo físico."
    else:
        return f"✅ **Buena noticia:** El aire es seguro para tu **{user_condition}**."

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
CARD_REPORT = """👋 **{greeting} {user_name}**
📍 **[{location_name}]({maps_url})** | {region}
🕒 {report_time}

{risk_circle} **{ias_value} puntos IAS** ({risk_category})
📈 Tendencia: {trend_arrow}

🔮 **Pronóstico Próximas 4h:**
{forecast_block}

📝 {natural_message}
🩺 **Recomendación:** {health_recommendation}

📊 **Clima:** 🌡️ {temp}°C | 💧 {humidity}% | 💨 {wind_speed} m/s
{footer}"""

CARD_ALERT_IAS = """🔔 **ALERTA: Límite Superado**
📍 **[{location_name}]({maps_url})**
🕒 {report_time} | {region}

🛑 **Nivel {risk_category} detectado**
{risk_circle} **{ias_value} puntos IAS** (Tu límite: {threshold})

🔮 **Tendencia:** {forecast_msg}
🩺 **Consejo:** {health_recommendation}

_Para silenciar: "Borrar alerta de {location_name}"_
{footer}"""

CARD_REMINDER = """⏰ **Tu Reporte Diario**
📍 **[{location_name}]({maps_url})**
🕒 {report_time} | {region}

{risk_circle} **{ias_value} puntos IAS** ({risk_category})

🔮 **Pronóstico:**
{forecast_block}

📝 {natural_message}
🩺 **Salud:** {health_recommendation}
_Para cancelar: "Borrar recordatorio de {location_name}"_
{footer}"""

CARD_CONTINGENCY = """🚨 **¡CONTINGENCIA AMBIENTAL!** 🚨
🌎 Zona Metropolitana del Valle de México
🕒 {report_time}

⚠️ **FASE ACTIVA:** {phase}
☣️ **Causa:** {pollutant}

🛑 **Restricciones:** Doble Hoy No Circula activo.
🛡️ **Acción:** Cierra ventanas y evita salir.

_Fuente: SIMAT /Smability_
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

    # a) Status Contingencia & Plan
    safe_plan = clean(plan_status)
    is_premium = "PREMIUM" in safe_plan.upper() or "TRIAL" in safe_plan.upper()
    contingency_status = "✅ **ACTIVA**" if is_premium else "🔒 **INACTIVA** (Solo Premium)"
    
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
        # --- FIX: VALIDAR QUE LA UBICACIÓN EXISTA ---
        # Solo mostramos la alerta si 'k' (ej. 'trabajo') sigue existiendo en tus ubicaciones
        if v.get('active') and k in locations: 
            safe_k = clean(k.capitalize())
            threshold_list.append(f"• {safe_k}: > {v.get('umbral')} pts")
    threshold_str = "\n".join(threshold_list) if threshold_list else "• *Sin alertas de umbral*"

    # e) Reportes de Aire PROGRAMADOS (FILTRADO)
    schedule_list = []
    schedules = alerts.get('schedule', {})
    for k, v in schedules.items():
        # --- FIX: VALIDAR QUE LA UBICACIÓN EXISTA ---
        if v.get('active') and k in locations: 
            safe_k = clean(k.capitalize())
            days = v.get('days', [])
            days_txt = "Diario" if len(days)==7 else "Días selec."
            schedule_list.append(f"• {safe_k}: {v.get('time')} hrs ({days_txt})")
    schedule_str = "\n".join(schedule_list) if schedule_list else "• *Sin reportes programados*"

    # f) Recordatorio HOY NO CIRCULA
    hnc_str = "• *Sin recordatorio activo*"
    if vehicle and vehicle.get('active'):
        config = vehicle.get('alert_config', {})
        if config.get('enabled'):
            hnc_str = f"• Te aviso a las **{config.get('time', '20:00')} hrs** si no circulas."
        else:
            hnc_str = "• 🔕 Recordatorio desactivado."
    elif not vehicle:
        hnc_str = "" 

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
