import json
import os
import time
import requests
import boto3
from datetime import datetime, timedelta
# Asegúrate de que estos módulos existen en tu entorno o están en layers
import cards
import re

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
DYNAMODB_TABLE = 'SmabilityUsers'
MASTER_API_URL = "https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference"

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

# --- 🧠 REGLAS DE NEGOCIO (LOGICA COMPARTIDA) ---
def get_user_permissions(user_item):
    """
    Determina qué tiene permitido el usuario según su suscripción.
    Retorna: (can_alerts, can_contingency)
    """
    sub = user_item.get('subscription', {})
    status = sub.get('status', 'FREE').upper()
    
    # Lógica permisiva: Si dice PREMIUM (Manual, Mensual, Dev), tiene todo.
    if "PREMIUM" in status:
        return True, True
    
    # Lógica FREE (Default)
    return False, False

# --- HELPERS ---
def get_cdmx_time(): return datetime.utcnow() - timedelta(hours=6)
def get_maps_url(lat, lon): return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

def send_telegram_push(chat_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        time.sleep(0.05) # Rate limiting suave
    except Exception as e:
        print(f"❌ TG Error: {e}")

def interpret_timeline_short(current_ias, timeline):
    if not timeline or not isinstance(timeline, list): return "Estable"
    try:
        max_point = max(timeline, key=lambda x: x.get('ias', 0))
        diff = max_point['ias'] - current_ias
        if diff > 10: return f"Sigue alta hasta las {max_point['hora']}"
        elif diff < -10: return "Mejora pronto"
        return "Estable"
    except: return "Estable"

def format_forecast_block(timeline):
    if not timeline or not isinstance(timeline, list): return "➡️ Estable"
    block = ""
    emoji_map = {"Bajo": "🟢", "Moderado": "🟡", "Alto": "🟠", "Muy Alto": "🔴", "Extremadamente Alto": "🟣"}
    count = 0
    for t in timeline:
        if count >= 4: break
        riesgo = t.get('riesgo', 'Bajo')
        emoji = emoji_map.get(riesgo, "⚪")
        block += f"`{t.get('hora')}` | {emoji} {t.get('ias')} pts\n"
        count += 1
    return block.strip()

def check_master_api_contingency():
    try:
        r = requests.get(MASTER_API_URL, timeout=5)
        if r.status_code == 200:
            d = r.json().get('contingency')
            if d and isinstance(d, dict):
                p = "Ozono" if 'ozone' in str(d.get('alert_type')).lower() else "Partículas"
                try: p += f" ({int(float(d['value']['value']))} {d['value']['unit']})"
                except: pass
                return True, d.get('phase','Fase I'), p
    except: pass
    return False, "", ""

def get_location_air_data(lat, lon):
    # URL de tu API Light (Function URL)
    # Usamos la URL pública que ya comprobamos que funciona
    API_URL = "https://vuy3dprsp2udtuelnrb5leg6ay0ygsky.lambda-url.us-east-1.on.aws/"
    
    try:
        # 1. Cache Buster: Timestamp para evitar datos viejos
        ts = int(time.time())
        
        # 2. Configurar parámetros
        params = {
            'lat': lat,
            'lon': lon,
            'mode': 'live',
            'ts': ts  # Truco anti-caché
        }
        
        # 3. Llamada HTTP con Timeout largo (25s) para aguantar Cold Starts
        # print(f"   📡 [HTTP] Request a API Light...") 
        response = requests.get(API_URL, params=params, timeout=25)
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"   ❌ [HTTP ERROR] Status: {response.status_code} | Body: {response.text[:100]}")
            return None

    except requests.exceptions.Timeout:
        print(f"   🐢 [TIMEOUT] La API Light tardó más de 25s en responder.")
        return None
    except Exception as e: 
        print(f"   ⚠️ [REQ ERROR] {str(e)}")
        return None

# --- CORE LOGIC (CORREGIDO) ---
def process_user(user, current_hour_str, contingency_data):
    user_id = user['user_id']
    first_name = user.get('first_name', 'Usuario')
    
    # ⚓ ANCLA A: FIX DE ROBUSTEZ (CRÍTICO)
    # Obtenemos alerts. Si DynamoDB devolvió un string en lugar de un dict, cortamos.
    alerts = user.get('alerts', {})
    if isinstance(alerts, str):
        print(f"⚠️ [DATA ERROR] User {user_id} tiene 'alerts' corrupto (String). Saltando.")
        return
    # ------------------------------------------

    locations = user.get('locations', {})
    health = user.get('health_profile', {})
    h_str = ", ".join([v.get('condition','') for v in health.values()]) if health else None

    # 🛑 GATEKEEPER: Revisar Permisos antes de procesar
    can_alerts, can_contingency = get_user_permissions(user)
    
    if not can_alerts and not can_contingency:
        return 

    # 1. CONTINGENCIA
    is_c, ph, pol = contingency_data
    if is_c and can_contingency: # 🔒 Solo si paga
        user_wants_cont = alerts.get('contingency', {}).get('enabled', False)
        
        if user_wants_cont:
            last = user.get('last_contingency_date', '')
            today = get_cdmx_time().strftime("%Y-%m-%d")
            
            if last != today:
                print(f"🚨 [NOTIFY] Enviando Contingencia a {first_name}")
                card = cards.CARD_CONTINGENCY.format(user_name=first_name, report_time=f"{current_hour_str.split(':')[0]}:20", phase=ph, pollutant=pol, forecast_msg="Oficial", footer=cards.BOT_FOOTER)
                send_telegram_push(user_id, card)
                table.update_item(Key={'user_id': user_id}, UpdateExpression="SET last_contingency_date = :d", ExpressionAttributeValues={':d': today})
                return # Si enviamos contingencia, evitamos saturar con otras alertas

    # 2. PROCESAMIENTO DE ALERTAS (Solo si tiene permiso PREMIUM)
    if can_alerts:
        
        # ⚓ ANCLA B: FIX VENTANA DE 20 MINUTOS
        # Calculamos el minuto actual para evitar spam (ej. 7:00, 7:20, 7:40)
        now = get_cdmx_time()
        current_minute = now.minute
        
        # Solo procesamos alertas de horario entre el minuto 18 y 38
        is_schedule_window = (18 <= current_minute < 38)

        # A. RECORDATORIOS POR HORARIO
        schedule_data = alerts.get('schedule', {})
        
        # Validamos que sea diccionario antes de iterar
        if isinstance(schedule_data, dict) and is_schedule_window:
            
            for loc_name, config in schedule_data.items():
                if not isinstance(config, dict): continue

                # Validamos hora (Ej: "07:30" coincide con "07:00")
                if config.get('active') and config.get('time', '').split(':')[0] == current_hour_str.split(':')[0]:
                    
                    loc_data = locations.get(loc_name)
                    if loc_data:
                        data = get_location_air_data(loc_data['lat'], loc_data['lon'])
                        if data:
                            qa = data.get('aire', {})
                            f_block = format_forecast_block(data.get('pronostico_timeline', []))
                            
                            cat_map = {"Bajo": "Buena", "Moderado": "Regular", "Alto": "Mala", "Muy Alto": "Muy Mala", "Extremadamente Alto": "Extrema"}
                            cat = cat_map.get(qa.get('riesgo'), "Regular")
                            info = cards.IAS_INFO.get(cat, cards.IAS_INFO['Regular'])
                            
                            print(f"⏰ [NOTIFY] Enviando Reporte Diario a {first_name}")
                            card = cards.CARD_REMINDER.format(
                                user_name=first_name, location_name=loc_data.get('display_name', loc_name),
                                maps_url=get_maps_url(loc_data['lat'], loc_data['lon']),
                                report_time=f"{current_hour_str.split(':')[0]}:20", region="ZMVM",
                                ias_value=qa.get('ias', 0), risk_category=cat, risk_circle=info['emoji'],
                                natural_message=info['msg'], forecast_block=f_block,
                                health_recommendation=cards.get_health_advice(cat, h_str),
                                footer=cards.BOT_FOOTER
                            )
                            send_telegram_push(user_id, card)

        # ---------------------------------------------------------
        # B. ALERTAS POR UMBRAL (Emergencia) - CON LOGS DE DEBUG 🕵️‍♂️
        # ---------------------------------------------------------
        threshold_data = alerts.get('threshold', {})
        
        # [LOG 1] Ver qué config tiene el usuario
        print(f"🔍 [DEBUG] User: {first_name} | Threshold Data: {json.dumps(threshold_data, default=str)}")

        if isinstance(threshold_data, dict):
            
            for loc_name, config in threshold_data.items():
                if not isinstance(config, dict): 
                    print(f"   ⚠️ [SKIP] Config de {loc_name} no es diccionario.")
                    continue
                
                # [LOG 2] Estado de activación
                is_active = config.get('active', False)
                if not is_active: 
                    print(f"   ⏭️ [SKIP] {loc_name}: Alerta desactivada (active=False)")
                    continue
                
                # --- FIX: PARSEO INTELIGENTE (Texto a Número) ---
                raw_umbral = config.get('umbral', 100)
                umbral = 100 # Default seguro
                
                try:
                    # Intento 1: Es número directo
                    if isinstance(raw_umbral, (int, float)):
                        umbral = int(raw_umbral)
                    # Intento 2: Es texto (ej: "> 40 IMA") -> Usamos Regex
                    else:
                        match = re.search(r'(\d+)', str(raw_umbral))
                        if match:
                            umbral = int(match.group(1))
                        else:
                            print(f"   ⚠️ [REGEX FAIL] No se pudo leer número en: '{raw_umbral}'")
                            continue 
                except Exception as e:
                    print(f"   ❌ [ERROR] Falló el parseo de umbral: {e}")
                    continue
                
                # Regla de seguridad: Mínimo 40 - ajuste vs SPAM
                umbral = max(umbral, 100)

                # [LOG 3] Confirmación de matemáticas
                print(f"   🔢 [MATH] {loc_name}: Umbral Final = {umbral} (Raw: {raw_umbral})")

                loc_data = locations.get(loc_name)
                
                if loc_data:
                    # [LOG 4] Llamada a API
                    print(f"   📡 [API] Consultando API Light para {loc_name}...")
                    data = get_location_air_data(loc_data['lat'], loc_data['lon'])
                    
                    if data:
                        qa = data.get('aire', {})
                        cur_ias = qa.get('ias', 0)
                        
                        # [LOG 5] EL MOMENTO DE LA VERDAD
                        print(f"   ⚖️ [COMPARE] {loc_name}: ¿Actual {cur_ias} > Umbral {umbral}?")
                        
                        if cur_ias >= umbral:
                            count = int(config.get('consecutive_sent', 0))
                            print(f"   🚨 [TRIGGER] CONDICIÓN CUMPLIDA. Consecutive sent: {count}")

                            if count < 3:
                                f_short = interpret_timeline_short(cur_ias, data.get('pronostico_timeline', []))
                                cat_map = {"Bajo": "Buena", "Moderado": "Regular", "Alto": "Mala", "Muy Alto": "Muy Mala", "Extremadamente Alto": "Extremadamente Mala"}
                                cat = cat_map.get(qa.get('riesgo'), "Regular")
                                info = cards.IAS_INFO.get(cat, cards.IAS_INFO['Mala'])
                                
                                print(f"   📤 [SENDING] Enviando mensaje a Telegram...")
                                
                                card = cards.CARD_ALERT_IAS.format(
                                    user_name=first_name, location_name=loc_data.get('display_name', loc_name),
                                    maps_url=get_maps_url(loc_data['lat'], loc_data['lon']),
                                    report_time=f"{current_hour_str.split(':')[0]}:20", region="ZMVM",
                                    risk_category=cat, risk_circle=info['emoji'], ias_value=cur_ias,
                                    forecast_msg=f_short, natural_message=info['msg'],
                                    threshold=umbral, pollutant="N/A", health_recommendation=cards.get_health_advice(cat, h_str),
                                    footer=cards.BOT_FOOTER
                                )
                                send_telegram_push(user_id, card)
                                
                                # Actualizar contador
                                try:
                                    table.update_item(
                                        Key={'user_id': user_id},
                                        UpdateExpression=f"SET alerts.threshold.#{loc_name}.consecutive_sent = :inc",
                                        ExpressionAttributeNames={f"#{loc_name}": loc_name},
                                        ExpressionAttributeValues={':inc': count + 1}
                                    )
                                    print("   ✅ [DB] Contador actualizado.")
                                except Exception as e:
                                    print(f"   ❌ [DB ERROR] No se pudo actualizar contador: {e}")
                            else:
                                print(f"   🛑 [MUTE] Alerta silenciada por spam (consecutive >= 3)")

                        elif config.get('consecutive_sent', 0) > 0:
                            # Resetear contador si bajó el nivel
                            print(f"   ⬇️ [RESET] El nivel bajó ({cur_ias} < {umbral}). Reseteando contador.")
                            try:
                                table.update_item(
                                    Key={'user_id': user_id},
                                    UpdateExpression=f"SET alerts.threshold.#{loc_name}.consecutive_sent = :zero",
                                    ExpressionAttributeNames={f"#{loc_name}": loc_name},
                                    ExpressionAttributeValues={':zero': 0}
                                )
                            except Exception as e: print(f"Error reseteando: {e}")
                    else:
                        print(f"   ❌ [API FAIL] API devolvió None para {loc_name}")
                else:
                    print(f"   ⚠️ [DATA] No se encontró config de location para {loc_name}")

def lambda_handler(event, context):
    now = get_cdmx_time()
    # Ventana operativa: 6 AM a 11 PM para ahorrar ejecuciones nocturnas
    if now.hour < 6 or now.hour > 23: 
        print("💤 [SLEEP] Fuera de horario operativo.")
        return {'statusCode': 200, 'body': 'Sleep'}
    
    print(f"⏰ [SCHEDULER] Iniciando ciclo: {now.strftime('%H:%M')}")
    
    cont_data = check_master_api_contingency()
    if cont_data[0]: print(f"⚠️ [CONTINGENCIA DETECTADA]: {cont_data[1]}")

    try:
        paginator = dynamodb.meta.client.get_paginator('scan')
        count = 0
        for page in paginator.paginate(TableName=DYNAMODB_TABLE):
            for item in page['Items']: 
                process_user(item, now.strftime("%H:%M"), cont_data)
                count += 1
        print(f"✅ [DONE] Usuarios procesados: {count}")
    except Exception as e: 
        print(f"❌ [CRITICAL ERROR]: {e}")
        
    return {'statusCode': 200, 'body': 'OK'}
