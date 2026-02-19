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
    {
        "type": "function",
        "function": {
            "name": "consultar_hoy_no_circula",
            "description": "Verifica si el auto del usuario circula en una fecha específica. Requiere que el usuario tenga auto configurado.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "fecha_referencia": {"type": "string", "description": "La fecha a consultar en formato YYYY-MM-DD. Si el usuario dice 'mañana', calcula la fecha. Si es 'hoy', usa la fecha actual."}
                }, 
                "required": ["fecha_referencia"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_auto",
            "description": "Guarda o actualiza el vehículo del usuario. NO pedir color de engomado, se calcula solo.",
            "parameters": {
                "type": "object", 
                "properties": {
                    "ultimo_digito": {"type": "integer", "description": "Último número de la placa (0-9)."}, 
                    "holograma": {"type": "string", "description": "Ejemplos: '00', '0', '1', '2', 'exento', 'hibrido'"}
                }, 
                "required": ["ultimo_digito", "holograma"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "obtener_calendario_mensual",
            "description": "Genera el reporte visual con TODOS los días del mes que el auto no circula y la tabla de multas. Usar cuando el usuario pida: 'calendario', 'días que no circulo', 'mi programa mensual'.",
            "parameters": {
                "type": "object", 
                "properties": {}, 
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_hora_alerta_auto",
            "description": "Cambia la hora a la que se envía el recordatorio de Hoy No Circula (ej. cambiar de 20:00 a 07:00).",
            "parameters": {
                "type": "object", 
                "properties": {
                    "nueva_hora": {"type": "string", "description": "Hora en formato HH:MM (24hrs). Ej: '07:00', '19:30'."}
                }, 
                "required": ["nueva_hora"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_verificacion",
            "description": "Muestra la tarjeta oficial de verificación vehicular con fechas y multas. Usar si preguntan: '¿Cuándo verifico?', '¿Me toca verificar?', 'Multa por no verificar'.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_ubicaciones_guardadas",
            "description": "Muestra la lista de lugares guardados (Casa, Trabajo, etc.) con opciones para ver su aire o borrarlos.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "eliminar_ubicacion",
            "description": "Elimina permanentemente una ubicación guardada (Casa, Trabajo, etc.) del perfil del usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre_ubicacion": {
                        "type": "string",
                        "description": "El nombre del lugar a borrar (ej. 'casa', 'trabajo', 'escuela')."
                    }
                },
                "required": ["nombre_ubicacion"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_ubicacion_personalizada",
            "description": "Guarda la ubicación actual (draft) con un nombre específico proporcionado por el usuario. Usar cuando el usuario responde con un nombre como 'Gym', 'Escuela' o 'Casa Mamá' después de enviar una ubicación.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {
                        "type": "string",
                        "description": "El nombre que el usuario quiere dar al lugar (ej. 'Gym', 'Escuela')."
                    }
                },
                "required": ["nombre"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_resumen_configuracion",
            "description": "Muestra tarjeta con resumen de alertas, horarios, auto y status. Usar cuando el usuario pregunte 'qué tengo configurado' o 'mis alertas'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_alerta_contingencia",
            "description": "Activa o desactiva la suscripción a alertas globales de Contingencia Ambiental.",
            "parameters": {
                "type": "object",
                "properties": {
                    "activar": {
                        "type": "boolean",
                        "description": "True para activar, False para desactivar."
                    }
                },
                "required": ["activar"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_transporte",
            "description": "Guarda el medio de transporte y horas de traslado del usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medio": {"type": "string", "enum": ["auto_ac", "suburbano", "cablebus", "metro", "metrobus", "auto_ventana", "combi", "caminar", "bicicleta", "home_office"]},
                    "horas_al_dia": {"type": "number"}
                },
                "required": ["medio", "horas_al_dia"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calcular_exposicion_diaria",
            "description": "Calcula los cigarros respirados y la 'Edad Urbana'.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]
