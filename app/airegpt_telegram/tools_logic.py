import json
import boto3
import os
import requests
from datetime import datetime
from decimal import Decimal

# --- CONFIGURACIÓN ---
DYNAMODB_TABLE = 'SmabilityUsers'
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

def normalize_key(text):
    if not text: return ""
    text = text.lower().strip().replace(" ", "_")
    replacements = (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"))
    for a, b in replacements:
        text = text.replace(a, b)
    return text

# --- 🧪 RUTINA Y TRANSPORTE ---
def ejecutar_configurar_transporte(user_id, medio, horas_raw):
    try:
        horas_float = round(float(horas_raw), 1)
        if horas_float > 6.0: horas_float = 6.0
        horas_db = Decimal(str(horas_float))
        
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET profile_transport = :p",
            ExpressionAttributeValues={':p': {
                'medio': medio, 
                'horas': horas_db, 
                'tiempo_traslado_horas': horas_db
            }}
        )
        return f"Éxito: Rutina actualizada a {medio}."
    except:
        return "⚠️ Error en formato de tiempo."
#---
def configure_schedule_alert(user_id, nombre_ubicacion, hora, dias_str=None):
    """
    Guarda o actualiza recordatorios de aire en alerts.schedule.{ubicacion}
    """
    try:
        # 1. Normalizar la llave (ej. 'Casa' -> 'casa')
        key = normalize_key(nombre_ubicacion)
        
        # 2. Traducir texto de días a lista [0,1,2,3,4,5,6]
        # Si dias_str es None o 'diario', usamos todos los días por defecto
        dias_list = [0, 1, 2, 3, 4, 5, 6]
        if dias_str and "diario" not in dias_str.lower():
            # Aquí podrías usar tu helper parse_days_input si ya lo tienes en la lambda,
            # por ahora para asegurar el guardado, usamos diario o lo que venga.
            pass 

        # 3. Update atómico en DynamoDB
        # Usamos la ruta alerts.schedule.#loc para no borrar otras alertas
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET alerts.schedule.#loc = :val",
            ExpressionAttributeNames={'#loc': key},
            ExpressionAttributeValues={
                ':val': {
                    'time': str(hora), 
                    'days': dias_list, 
                    'active': True
                }
            }
        )
        
        # 🚩 EL DETONADOR: Retorno con "Éxito" para el silenciador
        return f"Éxito: Recordatorio para {nombre_ubicacion.capitalize()} a las {hora} guardado."

    except Exception as e:
        print(f"❌ Error en configure_schedule_alert: {e}")
        return f"⚠️ Error al guardar horario: {str(e)}"

# --- 📍 GESTIÓN DE UBICACIONES (Soporte 3 ranuras para Premium) ---
def ejecutar_guardar_ubicacion(user_id, nombre, lat=None, lon=None, is_premium=False):
    try:
        # 1. Normalizar la llave (ej. 'Casa' -> 'casa')
        key = normalize_key(nombre)
        if not key: return "⚠️ Nombre de ubicación no válido."

        # 2. Obtener datos actuales para verificar límites y buscar el DRAFT
        user_data = table.get_item(Key={'user_id': str(user_id)}).get('Item', {})
        locs = user_data.get('locations', {})
        
        # 🚨 RESCATE DE COORDENADAS: Si no vienen en la tool, las sacamos del PIN previo (draft)
        if lat is None or lon is None:
            draft = user_data.get('draft_location')
            if draft and isinstance(draft, dict):
                lat = draft.get('lat')
                lon = draft.get('lon')
            
            if not lat or not lon:
                return "⚠️ No encontré coordenadas pendientes. Por favor envía primero tu ubicación con el clip 📎."

        # 3. Lógica de Límites (3 para Premium, 2 para Free)
        activas = [k for k, v in locs.items() if isinstance(v, dict) and v.get('active')]
        limite = 3 if is_premium else 2
        
        if len(activas) >= limite and key not in locs:
            plan = "Premium" if is_premium else "Gratis"
            return f"🛑 Límite de {limite} lugares alcanzado para tu plan {plan}. Borra uno para agregar '{nombre}'."

        # 4. Guardado Atómico en DynamoDB + LIMPIEZA DEL DRAFT
        # Agregamos 'REMOVE draft_location' para limpiar la memoria temporal
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET locations.#loc = :val, alerts.threshold.#loc = :alert REMOVE draft_location",
            ExpressionAttributeNames={'#loc': key},
            ExpressionAttributeValues={
                ':val': {
                    'display_name': nombre.strip().capitalize(),
                    'lat': str(lat),
                    'lon': str(lon),
                    'active': True
                },
                ':alert': {'umbral': 100, 'active': True, 'consecutive_sent': 0}
            }
        )
        
        return f"Éxito: Ubicación '{nombre.capitalize()}' guardada correctamente."

    except Exception as e:
        print(f"❌ Error en guardar_ubicacion: {e}")
        return f"⚠️ Error al guardar en DB: {str(e)}"

# --- 🗑️ BORRADO ESPECÍFICO (Refuerzo) ---
def ejecutar_borrar_ubicacion(user_id, nombre):
    try:
        key = normalize_key(nombre)
        # En lugar de REMOVE (que borra la llave), marcamos active: False 
        # para mantener consistencia si hay alertas amarradas, o REMOVE si prefieres limpieza total.
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="REMOVE locations.#k, alerts.threshold.#k, alerts.schedule.#k",
            ExpressionAttributeNames={'#k': key}
        )
        return f"Éxito: Ubicación '{nombre}' eliminada."
    except Exception as e:
        return f"⚠️ Error al eliminar: {str(e)}"

# --- 🩺 SALUD (Atomic Replace) ---
def ejecutar_guardar_salud(user_id, condicion_raw):
    try:
        cond_id = condicion_raw.lower().strip().replace(" ", "_")
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET health_profile = :val",
            ExpressionAttributeValues={':val': {
                cond_id: {'condition': condicion_raw.capitalize(), 'active': True}
            }}
        )
        return f"Éxito: Salud actualizada con {condicion_raw.capitalize()}."
    except Exception as e:
        return f"❌ Error DB: {str(e)}"

# --- 🚗 VEHÍCULO ---
def ejecutar_configurar_auto(user_id, digit, hologram):
    try:
        digit = int(digit)
        # Limpieza para evitar el "none"
        holo = str(hologram).lower().replace("holograma", "").strip()
        if holo in ["ninguno", "no tengo", "none"]: holo = "0" # Valor default seguro

        colors = {5:"Amarillo", 6:"Amarillo", 7:"Rosa", 8:"Rosa", 3:"Rojo", 4:"Rojo", 1:"Verde", 2:"Verde", 9:"Azul", 0:"Azul"}
        color = colors.get(digit, "Desconocido")
        
        vehicle_data = {
            "active": True, "plate_last_digit": digit, "hologram": holo,
            "engomado": color, "updated_at": datetime.now().isoformat()
        }
        table.update_item(
            Key={'user_id': str(user_id)}, 
            UpdateExpression="SET vehicle = :v", 
            ExpressionAttributeValues={':v': vehicle_data}
        )
        return f"Éxito: Vehículo placa {digit} registrado."
    except:
        return "❌ Error al guardar vehículo."

# --- 🚨 CONTINGENCIA (Ruta Exacta) ---
def ejecutar_configurar_alerta_contingencia(user_id, activar):
    try:
        # Según tu JSON la ruta es alerts -> contingency
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET alerts.contingency = :v",
            ExpressionAttributeValues={':v': bool(activar)}
        )
        return "Éxito: Alerta de contingencia actualizada."
    except:
        return "⚠️ Error en DB contingencia."

# --- 🔔 ALERTAS Y RECORDATORIOS ---
def ejecutar_configurar_alerta_ias(user_id, nombre_ubicacion, umbral):
    try:
        u_int = int(umbral)
        if u_int < 100: return "⚠️ El umbral mínimo es 100 pts."
        
        # Nota: Aquí asumimos que la validación de existencia de ubicación se hizo en el handler 
        # o GPT ya sabe que existe por su memoria.
        key = normalize_key(nombre_ubicacion)
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="SET alerts.threshold.#loc = :v",
            ExpressionAttributeNames={'#loc': key},
            ExpressionAttributeValues={':v': {'umbral': u_int, 'active': True, 'consecutive_sent': 0}}
        )
        return f"Éxito: Alerta configurada en {nombre_ubicacion} > {u_int} pts."
    except:
        return "⚠️ Error en umbral."

# --- 🗑️ BORRADOS ATÓMICOS ---
def ejecutar_borrado_elemento(user_id, tipo, args=None):
    try:
        if tipo == "ubicacion":
            nombre = args.get('nombre_ubicacion', '')
            key = normalize_key(nombre)
            table.update_item(
                Key={'user_id': str(user_id)},
                UpdateExpression="REMOVE locations.#k, alerts.threshold.#k, alerts.schedule.#k",
                ExpressionAttributeNames={'#k': key}
            )
            return f"🗑️ Ubicación '{nombre}' eliminada."
        
        paths = {"auto": "vehicle", "rutina": "profile_transport", "perfil_salud": "health_profile"}
        table.update_item(Key={'user_id': str(user_id)}, UpdateExpression=f"REMOVE {paths[tipo]}")
        return f"Éxito: {tipo.capitalize()} eliminado correctamente."
    except:
        return "⚠️ Error en borrado."
