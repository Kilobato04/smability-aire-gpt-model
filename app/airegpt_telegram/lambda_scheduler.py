import json
import os
import time
import requests
import boto3
import stripeairegpt
import cards
import re
from datetime import datetime, timedelta


# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
DYNAMODB_TABLE = 'SmabilityUsers'
MASTER_API_URL = "https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference"
BOT_LAMBDA_NAME = 'Smability-Chatbot'
lambda_client = boto3.client('lambda')
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

def get_time_greeting():
    h = get_cdmx_time().hour
    return "Buenos días" if 5<=h<12 else "Buenas tardes" if 12<=h<20 else "Buenas noches"

def send_telegram_push(chat_id, text, markup=None):
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if markup: 
            payload["reply_markup"] = markup
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload)
        time.sleep(0.05) # Rate limiting suave
    except Exception as e:
        print(f"❌ TG Error: {e}")

def send_telegram_photo_local(chat_id, photo_path, caption, markup=None):
    """Sube una foto desde la carpeta local hacia Telegram y acepta botones"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
    if markup: 
        data["reply_markup"] = json.dumps(markup)
    
    try:
        with open(photo_path, 'rb') as photo_file:
            r = requests.post(url, data=data, files={"photo": photo_file}, timeout=15)
            if r.status_code != 200:
                print(f"❌ [TG PHOTO FAIL]: {r.text}")
                send_telegram_push(chat_id, caption, markup) # Paracaídas
    except Exception as e:
        print(f"❌ [TG UPLOAD ERROR]: {e}")
        send_telegram_push(chat_id, caption, markup) # Paracaídas

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

# --- NUEVA FUNCIÓN DE DETECCIÓN Y DISPARO (CON ESTACIÓN OFICIAL) ---
def check_and_broadcast_contingency():
    print("🕵️‍♂️ [DEBUG CONTINGENCIA] Iniciando revisión a la API Maestra...")
    try:
        r = requests.get(MASTER_API_URL, timeout=15)
        if r.status_code != 200: 
            print(f"❌ [DEBUG] Error HTTP al consultar API Maestra: {r.status_code}")
            return

        data = r.json()
        stations = data.get('stations', [])
        print(f"📊 [DEBUG] Total estaciones recibidas de la API: {len(stations)}")
        
        current_phase = "None"
        contingency_data = {}
        
        if stations:
            for i, st in enumerate(stations):
                cont = st.get('contingency')
                
                if cont and isinstance(cont, dict):
                    raw_phase = cont.get('phase', '')
                    clean_phase = str(raw_phase).strip().upper()
                    
                    if clean_phase in ['FASE I', 'FASE 1', 'FASE II', 'FASE 2']:
                        current_phase = "Fase I" if "1" in clean_phase or "I" in clean_phase else "Fase II"
                        contingency_data = cont
                        
                        # Inyectamos los datos de la estación oficial al payload
                        contingency_data['trigger_station_name'] = st.get('station_name', 'SIMAT')
                        contingency_data['trigger_station_id'] = st.get('station_id', 'N/A')
                        
                        # --- FIX: DIFERENCIADOR DE TARJETA (ALERTA DE SALUD TEMPRANA) ---
                        contingency_data['alert_type'] = "Alerta Temprana (Sensores)"
                        contingency_data['recommendations'] = {
                            "categories": [{
                                "name": "RESTRICCIONES VEHICULARES", 
                                "items": ["⏳ A la espera de confirmación legal de la CAMe (Aplicarán mañana si se mantiene)."]
                            }]
                        }
                        
                        print(f"✅ [DEBUG] Contingencia extraída en índice [{i}] - Estación: {contingency_data['trigger_station_name']}")
                        break # Encontramos la contingencia, rompemos el ciclo
        
        if current_phase == "None":
            print("🍃 [DEBUG] Ninguna estación reporta contingencia en este momento.")

        # Comparar con Estado en DB
        db_item = table.get_item(Key={'user_id': 'SYSTEM_STATE'}).get('Item', {})
        last_phase = db_item.get('last_contingency_phase', 'None')
        came_status = db_item.get('came_oficial', {}).get('estatus', 'SIN_CONTINGENCIA') # <--- LEEMOS A LA CAME
        
        print(f"🔍 [DEBUG] DB State: Anterior='{last_phase}' | Detectada='{current_phase}' | CAMe='{came_status}'")
        
        # Disparar solo si hay cambio de estado
        if current_phase != last_phase:
            print("🚨 [DEBUG] ¡CAMBIO DE ESTADO! Preparando Broadcast...")
            payload = {}
            
            if current_phase != "None":
                payload = {"action": "BROADCAST_CONTINGENCY", "data": contingency_data}
            
            elif last_phase != "None":
                # --- FIX: CANDADO JERÁRQUICO ---
                if came_status in ['ACTIVA', 'MANTIENE']:
                    print("🛑 [JERARQUÍA] Sensores limpios, pero CAMe MANTIENE. Bloqueando Tarjeta Verde.")
                    return # ABORTAMOS. Ni actualizamos la BD ni mandamos el Broadcast Verde.
                else:
                    payload = {"action": "BROADCAST_CONTINGENCY", "data": {"phase": "SUSPENDIDA"}}
            
            if payload:
                response = lambda_client.invoke(
                    FunctionName=BOT_LAMBDA_NAME,
                    InvocationType='Event', 
                    Payload=json.dumps(payload)
                )
                print(f"🚀 [DEBUG] Broadcast enviado al Bot. Status: {response.get('StatusCode')}")
            
            # Actualizar Estado
            table.update_item(
                Key={'user_id': 'SYSTEM_STATE'},
                UpdateExpression="SET last_contingency_phase = :p, updated_at = :t",
                ExpressionAttributeValues={':p': current_phase, ':t': datetime.now().isoformat()}
            )
        else:
            print("💤 [DEBUG] Sin cambios de fase. No se dispara alerta.")

    except Exception as e:
        print(f"🔥 [CRITICAL DEBUG] Fallo general en check_and_broadcast_contingency: {e}")

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
    # Detectar si el usuario es Premium/Trial para el HNC
    sub_status = user.get('subscription', {}).get('status', 'FREE').upper()
    is_vip = any(x in sub_status for x in ["PREMIUM", "TRIAL"])
    
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
                            meteo = data.get('meteo', {}) # <--- NUEVO (Clima)
                            ubic = data.get('ubicacion', {}) # <--- NUEVO (Ubicación)
                            f_block = cards.format_forecast_block(data.get('pronostico_timeline', []))
                            
                            # 🔥 FIX: Lógica Matemática Unificada de IAS a Color (CDMX)
                            cur_ias = qa.get('ias', 0)
                            if cur_ias <= 50:
                                cat = "Buena"
                            elif cur_ias <= 100:
                                cat = "Regular"
                            elif cur_ias <= 150:
                                cat = "Mala"
                            elif cur_ias <= 200:
                                cat = "Muy Mala"
                            else:
                                cat = "Extremadamente Mala"
                                
                            info = cards.IAS_INFO.get(cat, cards.IAS_INFO['Regular'])
                            
                            print(f"⏰ [NOTIFY] Enviando Reporte Diario a {first_name}")
                            
                            # Generar Píldora HNC (Veredicto real para VIPs, candado para FREE)
                            db_item = table.get_item(Key={'user_id': 'SYSTEM_STATE'}).get('Item', {})
                            sys_phase = db_item.get('last_contingency_phase', 'None')
                            
                            # IMPORTANTE: Pasamos 'is_vip' a la función
                            hnc_text = cards.build_hnc_pill(user.get('vehicle'), sys_phase, is_vip)
                            
                            # Armar footer combinado
                            combined_footer = f"{hnc_text}\n\n{cards.BOT_FOOTER}" if hnc_text else cards.BOT_FOOTER
                            
                            # --- NUEVO: Extraer tendencia de la API ---
                            tendencia_actual = qa.get('tendencia', 'Estable 📊')

                            card = cards.CARD_REMINDER.format(
                                greeting=get_time_greeting(),
                                user_name=first_name, location_name=loc_data.get('display_name', loc_name),
                                maps_url=get_maps_url(loc_data['lat'], loc_data['lon']),
                                region=f"{ubic.get('mun', 'ZMVM')}, {ubic.get('edo', 'CDMX')}",
                                report_time=f"{current_hour_str.split(':')[0]}:20", 
                                ias_value=qa.get('ias', 0), risk_category=cat, risk_circle=info['emoji'],
                                pollutant=qa.get('dominante', 'N/A'), 
                                trend=tendencia_actual, # <--- INYECTAMOS TENDENCIA AQUÍ
                                forecast_block=f_block,
                                health_recommendation=cards.get_health_advice(cat, h_str),
                                temp=meteo.get('tmp', 0), humidity=meteo.get('rh', 0), wind_speed=meteo.get('wsp', 0),
                                footer=combined_footer
                            )
                            
                            # --- FIX BANNERS: Seleccionar y enviar foto ---
                            import os
                            directorio_actual = os.path.dirname(os.path.abspath(__file__))
                            mapa_archivos = {
                                "Buena": "banner_buena.png", "Regular": "banner_regular.png", "Mala": "banner_mala.png",
                                "Muy Mala": "banner_muy_mala.png", "Extremadamente Mala": "banner_extrema.png"
                            }
                            calidad_clean = cat.replace("Extremadamente Alta", "Extremadamente Mala").replace("Muy Alta", "Muy Mala").replace("Alta", "Mala")
                            nombre_png = mapa_archivos.get(calidad_clean, "banner_regular.png")
                            ruta_imagen = os.path.join(directorio_actual, "banners", nombre_png)
                            
                            # Botón exclusivo para reportes automáticos
                            markup_reporte = {
                                "inline_keyboard": [[{"text": "📊 Mi Resumen", "callback_data": "ver_resumen"}]]
                            }
                            send_telegram_photo_local(user_id, ruta_imagen, card, markup=markup_reporte)
                            # ---------------------------------------------

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
                        #-----
                        if cur_ias >= umbral:
                            count = int(config.get('consecutive_sent', 0))
                            print(f"   🚨 [TRIGGER] CONDICIÓN CUMPLIDA. Consecutive sent: {count}")

                            # --- FIX NIVEL 4: CICLO DE ALERTAS FREE Y PREMIUM ---
                            tier = user.get('subscription', {}).get('status', 'FREE')
                            
                            should_send = False
                            is_paywall = False
                            
                            if "PREMIUM" in tier or "TRIAL" in tier:
                                if count < 3: should_send = True # Premium no recibe spam (max 3 seguidas)
                            else:
                                # LÓGICA FREE: 3 alertas de vida
                                free_alerts_sent = int(user.get('free_alerts_sent', 0))
                                if free_alerts_sent < 3:
                                    should_send = True
                                    try:
                                        # Le sumamos 1 a su contador histórico
                                        table.update_item(Key={'user_id': user_id}, UpdateExpression="SET free_alerts_sent = :val", ExpressionAttributeValues={':val': free_alerts_sent + 1})
                                    except: pass
                                else:
                                    # Se le acabaron las de prueba
                                    is_paywall = True
                                    should_send = True 
                            
                            if should_send:
                                if is_paywall:
                                    print(f"   💸 [PAYWALL] Lanzando muro a {first_name}")
                                    import stripeairegpt
                                    texto_venta, botones = stripeairegpt.get_paywall_response(tier, 0, "alertas", str(user_id))
                                    paywall_msg = "🚨 **El aire ha superado tu límite de peligro.**\n\nHas agotado tus 3 alertas automáticas de prueba.\n\nPara seguir recibiendo estos avisos en tiempo real, activa tu plan:\n\n" + texto_venta
                                    send_telegram_push(user_id, paywall_msg, markup=botones)
                                    try:
                                        # Apagamos su alerta para que no se le lance el paywall cada hora
                                        table.update_item(Key={'user_id': user_id}, UpdateExpression=f"SET alerts.threshold.#{loc_name}.active = :false", ExpressionAttributeNames={f"#{loc_name}": loc_name}, ExpressionAttributeValues={':false': False})
                                    except: pass
                                else:
                                    # ENVÍO NORMAL DE ALERTA
                                    f_short = interpret_timeline_short(cur_ias, data.get('pronostico_timeline', []))
                                    cur_ias_val = qa.get('ias', 0)
                                    
                                    if cur_ias_val <= 50: cat = "Buena"
                                    elif cur_ias_val <= 100: cat = "Regular"
                                    elif cur_ias_val <= 150: cat = "Mala"
                                    elif cur_ias_val <= 200: cat = "Muy Mala"
                                    else: cat = "Extremadamente Mala"
                                    
                                    info = cards.IAS_INFO.get(cat, cards.IAS_INFO['Regular'])
                                    db_item = table.get_item(Key={'user_id': 'SYSTEM_STATE'}).get('Item', {})
                                    sys_phase = db_item.get('last_contingency_phase', 'None')
                                    hnc_text = cards.build_hnc_pill(user.get('vehicle'), sys_phase)
                                    combined_footer = f"{hnc_text}\n\n{cards.BOT_FOOTER}" if hnc_text else cards.BOT_FOOTER

                                    tendencia_actual = qa.get('tendencia', 'Estable 📊')
                                    tendencia_final = f_short if f_short != "Estable" else tendencia_actual

                                    card = cards.CARD_ALERT_IAS.format(
                                        user_name=first_name, location_name=loc_data.get('display_name', loc_name), maps_url=get_maps_url(loc_data['lat'], loc_data['lon']),
                                        risk_category=cat, risk_circle=info['emoji'], ias_value=cur_ias_val,
                                        report_time=f"{current_hour_str.split(':')[0]}:20", forecast_msg=tendencia_final,
                                        threshold=umbral, pollutant=qa.get('dominante', 'N/A'), health_recommendation=cards.get_health_advice(cat, h_str),
                                        footer=combined_footer
                                    )
                                    
                                    import os
                                    directorio_actual = os.path.dirname(os.path.abspath(__file__))
                                    mapa_archivos = {"Buena": "banner_buena.png", "Regular": "banner_regular.png", "Mala": "banner_mala.png", "Muy Mala": "banner_muy_mala.png", "Extremadamente Mala": "banner_extrema.png"}
                                    ruta_imagen = os.path.join(directorio_actual, "banners", mapa_archivos.get(cat, "banner_regular.png"))
                                    
                                    markup_umbral = {"inline_keyboard": [[{"text": "📊 Mi Resumen", "callback_data": "ver_resumen"}]]}
                                    send_telegram_photo_local(user_id, ruta_imagen, card, markup=markup_umbral)
                                    
                                    try:
                                        # Anti-Spam de la hora actual
                                        table.update_item(Key={'user_id': user_id}, UpdateExpression=f"SET alerts.threshold.#{loc_name}.consecutive_sent = :inc", ExpressionAttributeNames={f"#{loc_name}": loc_name}, ExpressionAttributeValues={':inc': count + 1})
                                    except: pass
                            else:
                                print(f"   🛑 [MUTE] Alerta silenciada (Premium hizo spam o algo falló).")
                            # --- FIN DEL FIX ---

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
    
    # 1. Ventana Operativa
    if now.hour < 6 or now.hour > 23: 
        print("💤 [SLEEP] Fuera de horario.")
        return {'statusCode': 200, 'body': 'Sleep'}
    
    print(f"⏰ [SCHEDULER] Ejecutando: {now.strftime('%H:%M')}")
    
    # 2. NUEVO PROCESO DE CONTINGENCIA (Global)
    check_and_broadcast_contingency()

    # 3. PROCESO DE ALERTAS INDIVIDUALES (Usuarios)
    try:
        paginator = dynamodb.meta.client.get_paginator('scan')
        count = 0
        for page in paginator.paginate(TableName=DYNAMODB_TABLE):
            for item in page['Items']: 
                if item['user_id'] == 'SYSTEM_STATE': continue
                
                # Pasamos datos dummy (False, "", "") porque contingency ya se manejó arriba
                process_user(item, now.strftime("%H:%M"), (False, "", ""))
                count += 1
        print(f"✅ [DONE] Usuarios escaneados: {count}")
    except Exception as e: 
        print(f"❌ Error Loop Usuarios: {e}")
        
    return {'statusCode': 200, 'body': 'OK'}
