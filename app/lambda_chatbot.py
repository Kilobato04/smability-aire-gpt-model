import json
import os
import logging
import requests
import boto3
import unicodedata
from datetime import datetime
from openai import OpenAI
import lambda_api_light 

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
DYNAMODB_TABLE = 'SmabilityUsers'
HORA_INICIO = 6
HORA_FIN = 23

logger = logging.getLogger()
logger.setLevel(logging.INFO)

client = OpenAI(api_key=OPENAI_API_KEY)
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

# --- TOOLS ---
tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "consultar_calidad_aire",
            "description": "Consulta datos EXACTOS (IAS, O3, PM10, PM25) para coordenadas dadas.",
            "parameters": {
                "type": "object",
                "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
                "required": ["lat", "lon"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_ubicacion",
            "description": "Guarda ubicación en DB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}
                },
                "required": ["nombre", "lat", "lon"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "renombrar_ubicacion",
            "description": "Renombra ubicación existente.",
            "parameters": {
                "type": "object",
                "properties": {"nombre_actual": {"type": "string"}, "nombre_nuevo": {"type": "string"}},
                "required": ["nombre_actual", "nombre_nuevo"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "borrar_ubicacion",
            "description": "Elimina ubicación.",
            "parameters": {
                "type": "object",
                "properties": {"nombre": {"type": "string"}},
                "required": ["nombre"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_alerta_ias",
            "description": "Configura alerta IAS.",
            "parameters": {
                "type": "object",
                "properties": {"nombre_ubicacion": {"type": "string"}, "umbral_ias": {"type": "integer"}},
                "required": ["nombre_ubicacion", "umbral_ias"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_recordatorio",
            "description": "Configura recordatorio (HH:MM).",
            "parameters": {
                "type": "object",
                "properties": {"nombre_ubicacion": {"type": "string"}, "hora": {"type": "string"}},
                "required": ["nombre_ubicacion", "hora"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_mis_datos",
            "description": "Consulta perfil.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# --- FUNCIONES DB ---

def clean_key(text):
    try:
        nfd_form = unicodedata.normalize('NFD', text)
        text = "".join([c for c in nfd_form if not unicodedata.combining(c)])
    except: pass
    return "".join(e for e in text if e.isalnum() or e.isspace()).strip().lower().replace(" ", "_")

def get_user_profile(user_id):
    try:
        return table.get_item(Key={'user_id': str(user_id)}).get('Item', {})
    except: return {}

def save_user_interaction(user_id, first_name):
    try:
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="""
                SET first_name = :n, 
                    last_interaction = :t, 
                    locations = if_not_exists(locations, :empty_map), 
                    alerts = if_not_exists(alerts, :empty_alerts)
            """,
            ExpressionAttributeValues={
                ':n': first_name, ':t': datetime.now().isoformat(),
                ':empty_map': {}, ':empty_alerts': {'threshold': {}, 'schedule': {}}
            }
        )
    except: pass

def save_location(user_id, raw_name, lat, lon):
    try:
        safe_name = clean_key(raw_name)
        if not safe_name: return "❌ Nombre inválido."
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="set locations.#loc = :val",
            ExpressionAttributeNames={'#loc': safe_name},
            ExpressionAttributeValues={
                ':val': {'lat': str(lat), 'lon': str(lon), 'display_name': raw_name, 'active': True}
            }
        )
        return f"✅ Ubicación guardada: **{raw_name}**."
    except Exception as e: return f"❌ Error: {str(e)}"

def rename_location(user_id, old_name, new_name):
    try:
        safe_old = clean_key(old_name)
        safe_new = clean_key(new_name)
        user = get_user_profile(user_id)
        locs = user.get('locations', {})
        
        if safe_old not in locs:
            found = False
            for k in locs.keys():
                if clean_key(k) == safe_old: 
                    safe_old = k; found = True; break
            if not found: return f"⚠️ No encuentro '{old_name}'."
            
        old_data = locs[safe_old]
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="set locations.#new = :val",
            ExpressionAttributeNames={'#new': safe_new},
            ExpressionAttributeValues={
                ':val': {'lat': old_data['lat'], 'lon': old_data['lon'], 'display_name': new_name, 'active': True}
            }
        )
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="REMOVE locations.#old",
            ExpressionAttributeNames={'#old': safe_old}
        )
        return f"✅ Renombrado exitoso: **{old_name}** ahora es **{new_name}**."
    except Exception as e: return f"❌ Error: {str(e)}"

def delete_location(user_id, raw_name):
    try:
        safe_name = clean_key(raw_name)
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="REMOVE locations.#loc, alerts.threshold.#loc, alerts.schedule.#loc",
            ExpressionAttributeNames={'#loc': safe_name}
        )
        return f"🗑️ Eliminado: **{raw_name}**."
    except Exception as e: return f"❌ Error: {str(e)}"

def set_alert_ias(user_id, raw_name, umbral):
    try:
        safe_name = clean_key(raw_name)
        user = get_user_profile(user_id)
        if safe_name not in user.get('locations', {}): return f"⚠️ No encuentro '{raw_name}'."
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="set alerts.threshold.#loc = :val",
            ExpressionAttributeNames={'#loc': safe_name},
            ExpressionAttributeValues={':val': {'umbral': int(umbral), 'active': True}}
        )
        return f"🔔 Alerta activa: **{raw_name}** > **{umbral} IAS**."
    except Exception as e: return f"❌ Error: {str(e)}"

def set_alert_time(user_id, raw_name, hora):
    try:
        h = int(hora.split(':')[0])
        if h < HORA_INICIO or h >= HORA_FIN: return f"⚠️ Horario descanso ({HORA_INICIO}-{HORA_FIN}). Cambia la hora."
        safe_name = clean_key(raw_name)
        user = get_user_profile(user_id)
        if safe_name not in user.get('locations', {}): return f"⚠️ No encuentro '{raw_name}'."
        table.update_item(
            Key={'user_id': str(user_id)},
            UpdateExpression="set alerts.schedule.#loc = :val",
            ExpressionAttributeNames={'#loc': safe_name},
            ExpressionAttributeValues={':val': {'time': hora, 'active': True}}
        )
        return f"⏰ Recordatorio: **{raw_name}** a las **{hora}**."
    except Exception as e: return f"❌ Error: {str(e)}"

def get_my_config_str(user_id):
    try:
        data = get_user_profile(user_id)
        locs = data.get('locations', {})
        if not locs: return "📭 No tienes ubicaciones guardadas."
        msg = "📂 **Mis Ubicaciones:**\\n"
        for k,v in locs.items(): msg += f"- 📍 {v.get('display_name', k)}\\n"
        return msg
    except: return "Error."

# --- HANDLER ---

def send_telegram_message(chat_id, text):
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
        if 'message' not in body: return {'statusCode': 200, 'body': 'OK'}
        msg = body['message']
        chat_id = msg['chat']['id']
        user_id = msg['from']['id']
        first_name = msg['from'].get('first_name', 'Usuario')
        
        # 1. Init DB
        save_user_interaction(user_id, first_name)
        
        # 2. Obtener Contexto (Ubicaciones Guardadas)
        user_profile = get_user_profile(user_id)
        locations_map = user_profile.get('locations', {})
        
        # Generar string de contexto para GPT
        locations_context_str = "SIN UBICACIONES GUARDADAS."
        if locations_map:
            locations_context_str = "UBICACIONES DEL USUARIO (Usar para consultas):\\n"
            for k, v in locations_map.items():
                name = v.get('display_name', k)
                locations_context_str += f"- {name}: Lat {v['lat']}, Lon {v['lon']}\\n"

        has_home = 'casa' in locations_map
        
        user_content = ""
        system_instruction_extra = ""

        if 'location' in msg:
            lat, lon = msg['location']['latitude'], msg['location']['longitude']
            user_content = f"COORDENADAS ENVIADAS: {lat}, {lon}."
            
            # --- ONBOARDING EDUCATIVO ---
            if not has_home:
                system_instruction_extra = """
                📢 **MODO ONBOARDING (Usuario Nuevo):**
                1. Llama a `guardar_ubicacion` AUTOMÁTICAMENTE con nombre "Casa".
                2. CONFIRMA: "✅ He guardado esta ubicación como tu **Casa**."
                3. EDUCA sobre alertas con ejemplos claros: 
                   - "Ej: 'Recuérdame la calidad del aire a las 7:30am'"
                   - "Ej: 'Avísame si el IAS en Casa supera los 100'"
                4. Da el reporte de aire.
                """
            else:
                system_instruction_extra = "Usuario ya tiene Casa. Da reporte. Si pide guardar otra cosa, hazlo."

        elif 'text' in msg:
            user_content = msg['text']
            if user_content.strip() == "/start":
                welcome = (
                    f"👋 **¡Hola {first_name}! Soy AIreGPT.**\\n\\n"
                    "Tu asistente personal de calidad del aire en la CDMX.\\n\\n"
                    "1. 📎 **Envíame tu Ubicación** (Clip -> Ubicación).\\n"
                    "2. La guardaré como **Casa** 🏠.\\n"
                    "3. Luego podrás pedirme:\\n"
                    "   ⏰ _'Recuérdame el aire a las 7:30am'_\\n"
                    "   🔔 _'Avísame si Casa sube de 100 IAS'_"
                )
                send_telegram_message(chat_id, welcome)
                return {'statusCode': 200, 'body': 'OK'}
        else: return {'statusCode': 200, 'body': 'OK'}

        # --- PROMPT V14: GOLDEN MASTER ---
        gpt_messages = [
            {"role": "system", "content": f"""
                Eres AIreGPT (NOM-172). Asistente experto, amable y empático.
                
                CONTEXTO DE MEMORIA:
                {locations_context_str}
                
                {system_instruction_extra}

                ALCANCE GEOGRÁFICO:
                - Solo Valle de México. Si la tool dice "Fuera de cobertura", discúlpate y di que aún no tienes datos de esa zona. NO INVENTES DATOS.

                PRINCIPIOS:
                1. **VERDAD NUMÉRICA:** Copia y pega EXACTAMENTE los valores de la tool.
                2. **CONSISTENCIA:** Si el usuario renombra, los datos de aire deben ser idénticos.
                3. **FEEDBACK:** Si guardas/renombras, inicia confirmando: "✅ [Acción realizada]...".

                REGLA DE COLORES (CÍRCULOS):
                🟢(0-50), 🟡(51-75), 🟠(76-100), 🔴(101-150), 🟣(>150).

                FORMATO REPORTE:
                [Frase humana y empática sobre el clima/riesgo]
                
                [CÍRCULO] **Riesgo:** [Nivel] ([Valor] pts IAS)
                ⚠️ **Principal Amenaza:** [Contaminante]
                
                🩺 **Recomendación:** [Consejo humano]

                📊 **Datos:**
                🌡️ [T]°C | 💧 [H]% | 💨 [V]m/s
                🔴 O3: [V] ppb | 🟣 PM2.5: [V] µg | 🟤 PM10: [V] µg
                
                🕒 _Reporte [Hora]_
                📍 Fuente: AIreGPT.ai
                ℹ️ *Datos actualizados al min 20.*
            """},
            {"role": "user", "content": user_content}
        ]

        response = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_messages, tools=tools_schema, tool_choice="auto")
        ai_msg = response.choices[0].message
        final_text = ai_msg.content

        if ai_msg.tool_calls:
            gpt_messages.append(ai_msg)
            for tool_call in ai_msg.tool_calls:
                fn = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                res = ""
                
                if fn == "consultar_calidad_aire":
                    mock = {'queryStringParameters': {'lat': str(args['lat']), 'lon': str(args['lon'])}}
                    res = lambda_api_light.lambda_handler(mock, None)['body']
                elif fn == "guardar_ubicacion":
                    res = save_location(user_id, args['nombre'], args['lat'], args['lon'])
                elif fn == "renombrar_ubicacion":
                    res = rename_location(user_id, args['nombre_actual'], args['nombre_nuevo'])
                elif fn == "borrar_ubicacion":
                    res = delete_location(user_id, args['nombre'])
                elif fn == "configurar_alerta_ias":
                    res = set_alert_ias(user_id, args['nombre_ubicacion'], args['umbral_ias'])
                elif fn == "configurar_recordatorio":
                    res = set_alert_time(user_id, args['nombre_ubicacion'], args['hora'])
                elif fn == "consultar_mis_datos":
                    res = get_my_config_str(user_id)
                
                gpt_messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": fn, "content": str(res)})

            final_res = client.chat.completions.create(model="gpt-4o-mini", messages=gpt_messages)
            final_text = final_res.choices[0].message.content

        if final_text: send_telegram_message(chat_id, final_text)
        return {'statusCode': 200, 'body': 'OK'}

    except Exception as e:
        logger.error(f"Error: {e}")
        return {'statusCode': 500, 'body': str(e)}
