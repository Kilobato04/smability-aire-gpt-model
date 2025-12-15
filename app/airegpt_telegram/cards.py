BOT_VERSION = "v0.5.1 (Health+)"
BOT_FOOTER = f"🤖 AIreGPT {BOT_VERSION}"
IAS_SCALE_TEXT = "🟢(0-50) 🟡(51-75) 🟠(76-100) 🔴(101-150) 🟣(>150)"

# Base de Conocimiento NOM-172
IAS_INFO = {
    "Buena": {
        "msg": "El aire está limpio.",
        "rec": "Disfruta tus actividades al aire libre.",
        "emoji": "🟢"
    },
    "Regular": {
        "msg": "Calidad aceptable.",
        "rec": "Si eres muy sensible, reduce esfuerzos fuertes.",
        "emoji": "🟡"
    },
    "Mala": {
        "msg": "Podría causar molestias.",
        "rec": "Evita actividades físicas vigorosas al aire libre.",
        "emoji": "🟠"
    },
    "Muy Mala": {
        "msg": "Riesgo alto para la salud.",
        "rec": "No realices actividades al aire libre. Cierra ventanas.",
        "emoji": "🔴"
    },
    "Extremadamente Mala": {
        "msg": "¡Peligro! Contaminación extrema.",
        "rec": "Permanece en interiores. Acude al médico si tienes síntomas.",
        "emoji": "🟣"
    }
}

# --- LÓGICA DE PERSONALIZACIÓN ---
def get_health_advice(category, user_condition=None):
    """Genera un consejo híbrido entre la norma y el usuario."""
    base_rec = IAS_INFO.get(category, IAS_INFO["Regular"])["rec"]
    
    # Si no tiene padecimientos, devolvemos la recomendación estándar
    if not user_condition or user_condition.lower() == "ninguno":
        return base_rec
        
    # Lógica de Personalización
    if category in ["Mala", "Muy Mala", "Extremadamente Mala"]:
        return f"⚠️ **Atención por tu {user_condition}:** {base_rec} El aire actual puede agravar tus síntomas."
    elif category == "Regular":
        return f"ℹ️ **Por tu {user_condition}:** Considera reducir el esfuerzo físico, aunque el aire es aceptable."
    else:
        return f"✅ **Buena noticia:** El aire es seguro para tu **{user_condition}**."

# --- TARJETAS (TEMPLATES) ---

CARD_ONBOARDING = """👋 **¡Hola {user_name}!**

Soy **AIreGPT**, tu asistente personal de calidad del aire.

📍 **1. Ubicaciones:** Guardo tus lugares frecuentes.
☁️ **2. Precisión:** Datos locales exactos.
🔮 **3. Pronóstico:** Tendencia a 24 horas.
🔔 **4. Alertas:** Te aviso si la contaminación sube.
🚨 **5. Contingencia:** Alerta oficial automática.
⏰ **6. Rutinas:** Reportes diarios programados.

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
