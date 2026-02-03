import cards
BOT_VERSION = cards.BOT_VERSION
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "consultar_calidad_aire",
            "description": "Obtiene TARJETA DE REPORTE oficial.",
            "parameters": {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}, "nombre_ubicacion": {"type": "string"}}, "required": ["lat", "lon"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_ubicacion",
            "description": "Guarda ubicación.",
            "parameters": {"type": "object", "properties": {"nombre": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}}, "required": ["nombre", "lat", "lon"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "borrar_ubicacion",
            "description": "Elimina ubicación.",
            "parameters": {"type": "object", "properties": {"nombre": {"type": "string"}}, "required": ["nombre"]}
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
            "name": "borrar_perfil_completo",
            "description": "Borrado total.",
            "parameters": {"type": "object", "properties": {"confirmacion": {"type": "string", "enum": ["SI"]}}, "required": ["confirmacion"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_alerta_ias",
            "description": "Configura alerta IAS.",
            "parameters": {"type": "object", "properties": {"nombre_ubicacion": {"type": "string"}, "umbral_ias": {"type": "integer"}}, "required": ["nombre_ubicacion", "umbral_ias"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "borrar_alerta_ias",
            "description": "Elimina alerta.",
            "parameters": {"type": "object", "properties": {"nombre_ubicacion": {"type": "string"}}, "required": ["nombre_ubicacion"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_recordatorio",
            "description": "Configura reporte diario. Identifica hora y días (ej. lunes y viernes).",
            "parameters": {
                "type": "object", 
                "properties": {
                    "nombre_ubicacion": {"type": "string"}, 
                    "hora": {"type": "string", "description": "HH:MM"},
                    "dias": {"type": "string", "description": "Ej: 'lunes a viernes', 'fines de semana', 'diario', 'martes'"}
                }, 
                "required": ["nombre_ubicacion", "hora"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "borrar_recordatorio",
            "description": "Elimina recordatorio.",
            "parameters": {"type": "object", "properties": {"nombre_ubicacion": {"type": "string"}}, "required": ["nombre_ubicacion"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_perfil_salud",
            "description": "Agrega padecimiento.",
            "parameters": {"type": "object", "properties": {"tipo_padecimiento": {"type": "string"}, "es_vulnerable": {"type": "boolean"}}, "required": ["tipo_padecimiento", "es_vulnerable"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "borrar_padecimiento",
            "description": "Elimina padecimiento.",
            "parameters": {"type": "object", "properties": {"tipo_padecimiento": {"type": "string"}}, "required": ["tipo_padecimiento"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_mis_datos",
            "description": "Muestra datos.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    # 2. AGREGAR ESTA NUEVA (Para la tarjeta resumen)
    {
        "type": "function",
        "function": {
            "name": "consultar_resumen_configuracion",
            "description": "Muestra tarjeta con resumen de alertas, horarios, auto y status. Usar cuando el usuario pregunte 'qué tengo configurado' o 'mis alertas'.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]
