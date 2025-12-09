# --- CONFIGURACIÓN V0.2.11 ---
BOT_VERSION = "v0.2.11 (Fix Logic)"

INFO_VEHICULAR = {
    "costo_verificacion": "677.00 MXN",
    "multa_extemporanea": "2,171.00 MXN",
    "multa_hoy_no_circula": "2,171.00 - 3,257.00 MXN + Corralón",
    "calendario": {
        "5": "Enero-Feb", "6": "Enero-Feb", "7": "Feb-Marzo", "8": "Feb-Marzo",
        "3": "Marzo-Abril", "4": "Marzo-Abril", "1": "Abril-Mayo", "2": "Abril-Mayo",
        "9": "Mayo-Junio", "0": "Mayo-Junio"
    }
}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "consultar_calidad_aire",
            "description": "Consulta datos EXACTOS.",
            "parameters": {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}}, "required": ["lat", "lon"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_ubicacion",
            "description": "Guarda ubicación nueva.",
            "parameters": {"type": "object", "properties": {"nombre": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}}, "required": ["nombre", "lat", "lon"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_vehiculo",
            "description": "Guarda datos auto.",
            "parameters": {"type": "object", "properties": {"terminacion_placa": {"type": "string"}, "holograma": {"type": "string"}}, "required": ["terminacion_placa", "holograma"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_perfil_salud",
            "description": "Guarda salud (permite múltiples).",
            "parameters": {"type": "object", "properties": {"tipo_padecimiento": {"type": "string"}, "es_vulnerable": {"type": "boolean"}}, "required": ["tipo_padecimiento", "es_vulnerable"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_transporte",
            "description": "Guarda tiempo de traslado total.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "tipo_transporte": {"type": "string", "description": "Si no especifica, pon 'Transporte Público'"}, 
                    "horas_diarias": {"type": "number", "description": "Total horas ida y vuelta (ej. 2.5)"}
                }, 
                "required": ["tipo_transporte", "horas_diarias"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_riesgo_inundacion",
            "description": "Guarda riesgo inundación (CM).",
            "parameters": {"type": "object", "properties": {"nombre_ubicacion": {"type": "string"}, "cm_aprox": {"type": "integer"}, "descripcion": {"type": "string"}}, "required": ["nombre_ubicacion", "cm_aprox", "descripcion"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "renombrar_ubicacion",
            "description": "Renombra ubicación.",
            "parameters": {"type": "object", "properties": {"nombre_actual": {"type": "string"}, "nombre_nuevo": {"type": "string"}}, "required": ["nombre_actual", "nombre_nuevo"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "borrar_ubicacion",
            "description": "Borra ubicación.",
            "parameters": {"type": "object", "properties": {"nombre": {"type": "string"}}, "required": ["nombre"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "borrar_perfil_completo",
            "description": "Resetea todo el perfil.",
            "parameters": {"type": "object", "properties": {"confirmacion": {"type": "string"}}, "required": ["confirmacion"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_alerta_ias",
            "description": "Configura alerta por umbral de contaminación.",
            "parameters": {"type": "object", "properties": {"nombre_ubicacion": {"type": "string"}, "umbral_ias": {"type": "integer"}}, "required": ["nombre_ubicacion", "umbral_ias"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_recordatorio",
            "description": "Configura recordatorio diario por hora.",
            "parameters": {"type": "object", "properties": {"nombre_ubicacion": {"type": "string"}, "hora": {"type": "string", "description": "Format HH:MM"}}, "required": ["nombre_ubicacion", "hora"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_mis_datos",
            "description": "Consulta perfil completo.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# 3. ONBOARDING RICO (10 PUNTOS)
def get_welcome_message(first_name):
    return (
        f"👋 **¡Hola {first_name}! Soy AIreGPT.**\\n\\n"
        "Tu asistente inteligente de entorno y movilidad. Esto es lo que puedo hacer por ti:\\n\\n"
        "📍 **1. Ubicaciones:** Guardo tu Casa, Trabajo o Gym.\\n"
        "☁️ **2. Calidad del Aire:** Reportes precisos por zona.\\n"
        "🔔 **3. Alertas IAS:** 'Avísame si Casa sube de 100 puntos'.\\n"
        "⏰ **4. Recordatorios:** 'Reporte diario a las 7:30am'.\\n"
        "🚗 **5. Auto:** Te recuerdo tu Verificación y Hoy No Circula.\\n"
        "🩺 **6. Salud:** Consejos personalizados si eres vulnerable.\\n"
        "🚌 **7. Transporte:** Calculo tu exposición al aire en trayectos.\\n"
        "🌧️ **8. Lluvia:** Registro zonas de encharcamiento.\\n"
        "✏️ **9. Edición:** 'Renombra Casa a Depa'.\\n"
        "🗑️ **10. Privacidad:** 'Borra mis datos' cuando quieras.\\n\\n"
        "🚀 **¡Empecemos! Envíame tu ubicación actual (📎 Clip) para guardarla como Casa.**"
    )

# 4. SYSTEM PROMPT (Con reglas Fuertes de Transporte y Alertas)
def get_system_prompt(memoria_str, info_estatica, system_instruction_extra):
    return f"""
    Eres AIreGPT (NOM-172). Asistente experto.
    
    MEMORIA:
    {memoria_str}
    
    ESTADO:
    {system_instruction_extra}

    REGLAS DE INTERPRETACIÓN:
    1. **TRANSPORTE:** Si el usuario dice "Hago 2 horas de camino" y no especifica cómo, ASUME `tipo_transporte="Transporte Público"` automáticamente y llama a `guardar_transporte`.
    2. **ALERTAS:** - Si dice "Avísame cuando el aire esté mal" o "Arriba de 100", USA `configurar_alerta_ias`.
       - Si dice "Recuérdame a las 7am", USA `configurar_recordatorio`.
    3. **INUNDACIÓN:** Guarda en CM (Ej. 10, 30).
    4. **SALUD:** Une múltiples condiciones en un texto.
    5. **COLORES:** 🟢(0-50), 🟡(51-75), 🟠(76-100), 🔴(101-150), 🟣(>150).

    REPORTE:
    [Frase humana]
    [CÍRCULO] **Riesgo:** [Nivel] ([Valor] pts IAS)
    ⚠️ **Amenaza:** [Contaminante]
    🩺 **Consejo:** [Texto]
    📊 🌡️[T]°C | 💧[H]% | 💨[V]m/s | 🔴O3:[V] | 🟣PM2.5:[V] | 🟤PM10:[V]
    🕒 _Reporte [Hora]_
    ℹ️ *Datos al min 20.*
    🤖 *{BOT_VERSION}*
    """
