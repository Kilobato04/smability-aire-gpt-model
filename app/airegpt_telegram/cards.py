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

CARD_HNC_DETAILED = """🚗 **TU CALENDARIO HNC ({mes_nombre})**
🚘 **Placa:** ..{plate} ({color}) | **Holo:** {holo}

📅 **DÍAS SIN CIRCULAR:**
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

# --- HELPER VISUAL DE DÍAS ---
def format_days_text(days_list):
    if not days_list or len(days_list) == 7: return "Diario"
    if days_list == [0,1,2,3,4]: return "Lun-Vie"
    if days_list == [5,6]: return "Fin de Semana"
    names = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
    return ",".join([names[i] for i in days_list])

# --- HELPER DE BOTONES (Sin botón de Riesgo) ---
def get_summary_buttons(has_home, has_work):
    keyboard = []
    row = []
    if has_home: row.append({"text": "☁️ Ver Casa", "callback_data": "CHECK_HOME"})
    if has_work: row.append({"text": "🏢 Ver Oficina", "callback_data": "CHECK_WORK"})
    if row: keyboard.append(row)
    return {"inline_keyboard": keyboard}

# --- TARJETA PRINCIPAL ---
def generate_summary_card(user_name, alerts, vehicle=None, exposure=None):
    msg = f"⚙️ **TUS ALERTAS Y SERVICIOS**\n*Resumen para {user_name}:*\n\n"
    
    # 1. UMBRALES
    thresh = alerts.get('threshold', {})
    active = False
    msg += "📉 **Vigilancia IAS (24/7):**\n"
    for loc, cfg in thresh.items():
        if cfg.get('active'):
            active = True
            msg += f"• *{loc.capitalize()}:* > {cfg.get('umbral')} IAS\n"
    if not active: msg += "_(Sin vigilancia activa)_\n"
    msg += "\n"

    # 2. HORARIOS
    sched = alerts.get('schedule', {})
    msg += "⏰ **Reportes Programados:**\n"
    if not sched:
        msg += "_(Sin horarios)_\n"
    else:
        for loc, data in sched.items():
            hora = data.get('time', '00:00')
            dias = data.get('days', [0,1,2,3,4,5,6])
            msg += f"• *{loc.capitalize()}:* {hora} ({format_days_text(dias)})\n"
    msg += "\n"

    # 3. AUTO
    if vehicle and vehicle.get('active'):
        plate = vehicle.get('plate_last_digit')
        msg += f"🚗 **Tu Auto (..{plate}):**\n"
        hnc_on = "✅" if vehicle.get('alert_config', {}).get('enabled') else "🔕"
        msg += f"🔔 Aviso HNC: {hnc_on} (20:00)\n\n"

    # 4. EXPOSICIÓN (Solo DATOS, sin cálculo)
    if exposure:
        mode = exposure.get('mode', 'Transporte').capitalize()
        duration = exposure.get('duration', '?')
        msg += f"🫁 **Perfil de Exposición:**\n"
        msg += f"• Medio: {mode}\n"
        msg += f"• Tiempo: {duration}\n\n"

    # 5. FOOTER CONVERSACIONAL
    msg += "📝 *¿Quieres cambios?* Solo pídelo.\n"
    msg += "_Ej: \"Ajusta el umbral de Casa a 100\" o \"Avísame en Trabajo a las 9am\"._"
    
    return msg
