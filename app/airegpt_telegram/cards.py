# app/airegpt_telegram/cards.py
BOT_VERSION = "v0.5.7 (Timeline)"
BOT_FOOTER = f"🤖 AIreGPT {BOT_VERSION}"
IAS_SCALE_TEXT = "🟢(0-50) 🟡(51-75) 🟠(76-100) 🔴(101-150) 🟣(>150)"

IAS_INFO = {
    "Buena": {"msg": "Aire limpio.", "rec": "¡Disfruta el exterior!", "emoji": "🟢"},
    "Regular": {"msg": "Calidad aceptable.", "rec": "Sensibles: moderar esfuerzo.", "emoji": "🟡"},
    "Mala": {"msg": "Podría causar molestias.", "rec": "Evita ejercicio intenso fuera.", "emoji": "🟠"},
    "Muy Mala": {"msg": "Riesgo alto.", "rec": "No salgas. Cierra ventanas.", "emoji": "🔴"},
    "Extremadamente Mala": {"msg": "¡Peligro!", "rec": "Urgencia médica si hay síntomas.", "emoji": "🟣"}
}

def get_health_advice(category, user_condition=None):
    base_rec = IAS_INFO.get(category, IAS_INFO["Regular"])["rec"]
    if not user_condition or user_condition.lower() == "ninguno": return base_rec
    if category in ["Mala", "Muy Mala", "Extremadamente Mala"]:
        return f"⚠️ **Atención por tu {user_condition}:** {base_rec} El aire actual puede agravar tus síntomas."
    elif category == "Regular":
        return f"ℹ️ **Por tu {user_condition}:** Considera reducir el esfuerzo físico."
    else:
        return f"✅ **Buena noticia:** El aire es seguro para tu **{user_condition}**."

CARD_ONBOARDING = """👋 **¡Hola {user_name}!**
Soy **AIreGPT**, tu asistente personal de calidad del aire.
📍 **1. Ubicaciones:** Guardo tus lugares frecuentes.
☁️ **2. Precisión:** Datos locales exactos.
🔮 **3. Pronóstico:** Tendencia de las próximas 4 horas.
🔔 **4. Alertas:** Te aviso si la contaminación sube.
🚨 **5. Contingencia:** Alerta oficial automática.
👇 **CONFIGURACIÓN INICIAL (Obligatoria)**
Para funcionar, necesito saber dónde está tu **CASA**.
🚀 **PASO 1:** Toca el 📎 (Clip), elige **'Ubicación'** y envíame tu punto actual.
{footer}"""

CARD_REPORT = """👋 **{greeting} {user_name}**
📍 **[{location_name}]({maps_url})** | {region}
🕒 {report_time}
{risk_circle} **{ias_value} puntos IAS** ({risk_category})
🔮 **Pronóstico:** {forecast_msg}
📝 {natural_message}
⚠️ **Principal:** {pollutant}
🩺 **Recomendación:** {health_recommendation}
📊 **Clima:** 🌡️ {temp}°C | 💧 {humidity}% | 💨 {wind_speed} m/s
{footer}"""

CARD_ALERT_IAS = """🔔 **ALERTA DE AIRE**
📍 **[{location_name}]({maps_url})**
🕒 {report_time} | {region}
🛑 **Nivel {risk_category} detectado**
{risk_circle} **{ias_value} puntos IAS**
🔮 **Tendencia:** {forecast_msg}
📝 {natural_message}
(Tu límite es {threshold}).
☣️ **Causante:** {pollutant}
🩺 **Consejo:** {health_recommendation}
_Para desactivar: "Borrar alerta de {location_name}"_
{footer}"""

CARD_REMINDER = """⏰ **Tu Recordatorio Diario**
📍 **[{location_name}]({maps_url})**
🕒 {report_time} | {region}
{risk_circle} **{ias_value} puntos IAS** ({risk_category})
🔮 **Pronóstico:** {forecast_msg}
📝 {natural_message}
⚠️ **Principal:** {pollutant}
🩺 **Salud:** {health_recommendation}
_Para cancelar: "Borrar recordatorio de {location_name}"_
{footer}"""

CARD_CONTINGENCY = """🚨 **¡CONTINGENCIA AMBIENTAL!** 🚨
🌎 Zona Metropolitana del Valle de México
🕒 {report_time}
⚠️ **FASE ACTIVA:** {phase}
☣️ **Contaminante:** {pollutant}
🔮 **Evolución:** {forecast_msg}
🛑 **Restricciones:** Doble Hoy No Circula activo.
🛡️ **Acción:** Cierra ventanas y evita salir.
_Para desactivar: "Desactivar contingencia"_
{footer}"""
