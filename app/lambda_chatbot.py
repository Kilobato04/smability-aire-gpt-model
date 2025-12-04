import json
import os
import logging
import requests
from openai import OpenAI
import lambda_api_light 

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

logger = logging.getLogger()
logger.setLevel(logging.INFO)

client = OpenAI(api_key=OPENAI_API_KEY)

tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "consultar_calidad_aire",
            "description": "Obtiene datos de aire, riesgo y clima para una coordenada.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "Latitud"},
                    "lon": {"type": "number", "description": "Longitud"}
                },
                "required": ["lat", "lon"]
            }
        }
    }
]

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Error Telegram: {e}")

def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
        if 'message' not in body: return {'statusCode': 200, 'body': 'OK'}
            
        msg = body['message']
        chat_id = msg['chat']['id']
        
        user_content = ""
        if 'location' in msg:
            lat = msg['location']['latitude']
            lon = msg['location']['longitude']
            user_content = f"Coordenadas: {lat}, {lon}."
        elif 'text' in msg:
            user_content = msg['text']
            if user_content.strip() == "/start":
                send_telegram_message(chat_id, "👋 ¡Hola! Soy AIreGPT.\\n\\nCompárteme tu **Ubicación** 📍 para analizar el aire en tu zona.")
                return {'statusCode': 200, 'body': 'OK'}
        else: return {'statusCode': 200, 'body': 'OK'}

        # --- PROMPT OFICIAL AIreGPT ---
        gpt_messages = [
            {"role": "system", "content": """
                Eres AIreGPT, experto en salud ambiental de Smability.
                
                ALCANCE: Solo proporcionas datos de calidad del aire y meteorología para el Valle de México.
                Usa la tool 'consultar_calidad_aire' si hay coordenadas.
                
                TU FORMATO DE RESPUESTA (Estricto, usa Markdown, NO uses líneas separadoras '---'):
                
                🚦 **Nivel de Riesgo:** [Nivel] ([Puntos IAS] pts)
                ⚠️ **Causa Principal:** [Contaminante Dominante]
                
                🩺 [Consejo breve y directo sobre salud/actividad física]
                
                🌡️ [Temp]°C  |  💧 [Humedad]%  |  💨 [Viento] m/s
                
                🔴 O3: [Valor] ppb
                🟤 PM10: [Valor] µg
                🟣 PM2.5: [Valor] µg
                
                🕒 Reporte: [Hora del timestamp HH:MM]
                📍 Fuente: AIreGPT.ai
                
                NOTA: No inventes datos. Saca todo estrictamente del JSON.
            """},
            {"role": "user", "content": user_content}
        ]

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=gpt_messages,
            tools=tools_schema,
            tool_choice="auto"
        )
        
        ai_msg = response.choices[0].message
        final_text = ai_msg.content

        if ai_msg.tool_calls:
            tool_call = ai_msg.tool_calls[0]
            if tool_call.function.name == "consultar_calidad_aire":
                args = json.loads(tool_call.function.arguments)
                
                # Llamada Interna API Light
                mock_event = {'queryStringParameters': {'lat': str(args['lat']), 'lon': str(args['lon'])}}
                api_response = lambda_api_light.lambda_handler(mock_event, None)
                data_str = api_response['body']
                
                gpt_messages.append(ai_msg)
                gpt_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.function.name,
                    "content": data_str
                })
                
                final_res = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=gpt_messages
                )
                final_text = final_res.choices[0].message.content

        if final_text:
            send_telegram_message(chat_id, final_text)

        return {'statusCode': 200, 'body': 'OK'}

    except Exception as e:
        logger.error(f"Error: {e}")
        return {'statusCode': 500, 'body': str(e)}
