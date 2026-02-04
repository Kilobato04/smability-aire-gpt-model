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

🔔 **Alertas Calidad del Aire (IAS):**
{alerts_ias}

⏰ **Recordatorios HNC:**
{alerts_hnc}

💡 *{tip_footer}*
"""

# --- 1. HELPER VISUAL DE DÍAS ---
def format_days_text(days_list):
    if not days_list or len(days_list) == 7: return "Diario"
    if days_list == [0,1,2,3,4]: return "Lun-Vie"
    if days_list == [5,6]: return "Fin de Semana"
    names = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
    return ",".join([names[i] for i in days_list])

# --- 2. HELPER DE BOTONES (Sin botón de Riesgo) ---
def get_summary_buttons(has_home, has_work):
    keyboard = []
    row = []
    if has_home: row.append({"text": "☁️ Ver Casa", "callback_data": "CHECK_HOME"})
    if has_work: row.append({"text": "🏢 Ver Oficina", "callback_data": "CHECK_WORK"})
    if row: keyboard.append(row)
    return {"inline_keyboard": keyboard}


# --- 3. ACTUALIZAR FUNCIÓN GENERADORA DE RESUMEN ---
def generate_summary_card(user_name, alerts, vehicle, exposure, plan_status):
    # a) Status Contingencia
    is_premium = "PREMIUM" in plan_status.upper()
    contingency_status = "✅ **ACTIVA**" if is_premium else "🔒 **INACTIVA** (Solo Premium)"
    
    # b) Ubicaciones
    locs = []
    # Aquí asumimos que pasas el dict de locations, no el exposure profile directo
    # Si pasas exposure profile, adáptalo. Asumiré que pasas el dict 'locations' de la DB.
    if isinstance(exposure, dict): # Parche si pasas locations directo
        for k, v in exposure.items():
            locs.append(f"• **{k.capitalize()}:** {v.get('display_name','Ubicación')}")
    loc_str = "\n".join(locs) if locs else "• *Sin ubicaciones guardadas*"

    # c) Vehículo
    veh_str = "• *Sin auto registrado*"
    if vehicle and vehicle.get('active'):
        veh_str = f"• Placa **{vehicle.get('plate_last_digit')}** (Holo {vehicle.get('hologram')})"

    # d) Alertas IAS
    ias_list = []
    thresholds = alerts.get('threshold', {})
    for k, v in thresholds.items():
        if v.get('active'): ias_list.append(f"• {k.capitalize()}: > {v.get('umbral')} pts")
    ias_str = "\n".join(ias_list) if ias_list else "• *Sin alertas configuradas*"

    # e) Alertas HNC
    hnc_list = []
    schedules = alerts.get('schedule', {})
    for k, v in schedules.items():
        if v.get('active'): 
            # Parsear días
            days = v.get('days', [])
            days_txt = "Diario" if len(days)==7 else "Personalizado"
            hnc_list.append(f"• {k.capitalize()}: {v.get('time')} hrs ({days_txt})")
    hnc_str = "\n".join(hnc_list) if hnc_list else "• *Sin recordatorios*"

    tip = "💡 Tip: Escribe 'Cambiar hora alertas' para ajustar." if is_premium else "💎 Tip: Hazte Premium para activar Contingencias."

    return CARD_SUMMARY.format(
        user_name=user_name,
        plan_status=plan_status,
        contingency_status=contingency_status,
        locations_list=loc_str,
        vehicle_info=veh_str,
        alerts_ias=ias_str,
        alerts_hnc=hnc_str,
        tip_footer=tip
    )

# --- 4. ACTUALIZAR BOTONES DE RESUMEN (UPSELLING) ---
def get_summary_buttons(has_home, has_work, is_premium=False):
    # Fila 1: Accesos directos a Aire
    row1 = []
    if has_home: row1.append({"text": "🏠 Aire Casa", "callback_data": "CHECK_HOME"})
    if has_work: row1.append({"text": "🏢 Aire Trabajo", "callback_data": "CHECK_WORK"})
    
    keyboard = []
    if row1: keyboard.append(row1)
    
    # Fila 2: Lógica Premium vs Free
    if not is_premium:
        keyboard.append([{"text": "💎 Activar Premium ($49)", "callback_data": "GO_PREMIUM"}])
        keyboard.append([{"text": "❓ Ver Beneficios", "callback_data": "SHOW_BENEFITS"}])
    else:
        keyboard.append([{"text": "⚙️ Configuración Avanzada", "callback_data": "CONFIG_ADVANCED"}])

    return {"inline_keyboard": keyboard}
