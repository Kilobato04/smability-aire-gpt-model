BOT_VERSION = cards.BOT_VERSION

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "consultar_calidad_aire",
            "description": "Obtiene TARJETA DE REPORTE oficial.",
            "parameters": {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}, "nombre_ubicacion": {"type": "string", "description": "Nombre lugar (ej. Casa)."}}, "required": ["lat", "lon"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_ubicacion",
            "description": "Guarda ubicación (Max 3).",
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
            "description": "☢️ BORRADO TOTAL. Confirmar con 'SI'.",
            "parameters": {"type": "object", "properties": {"confirmacion": {"type": "string", "enum": ["SI"]}}, "required": ["confirmacion"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_alerta_ias",
            "description": "Configura alerta IAS (Max 1 por ubicación).",
            "parameters": {"type": "object", "properties": {"nombre_ubicacion": {"type": "string"}, "umbral_ias": {"type": "integer"}}, "required": ["nombre_ubicacion", "umbral_ias"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "borrar_alerta_ias",
            "description": "Elimina alerta de umbral.",
            "parameters": {"type": "object", "properties": {"nombre_ubicacion": {"type": "string"}}, "required": ["nombre_ubicacion"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_recordatorio",
            "description": "Configura reporte diario (Max 1 por ubicación).",
            "parameters": {"type": "object", "properties": {"nombre_ubicacion": {"type": "string"}, "hora": {"type": "string", "description": "Format HH:MM"}}, "required": ["nombre_ubicacion", "hora"]}
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
            "description": "Agrega padecimiento (Max 2).",
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
    }
]
