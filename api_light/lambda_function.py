import json
import boto3
import pandas as pd
import numpy as np
import os
import gzip
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

# --- CONFIGURACIÓN ---
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
GRID_KEY = 'live_grid/latest_grid.json'
s3 = boto3.client('s3')

LIMITS = {'LAT_MIN': 19.13, 'LAT_MAX': 19.80, 'LON_MIN': -99.40, 'LON_MAX': -98.80}
MAX_DISTANCE_KM = 10.0
CACHED_GRID = None

# --- FIX CACHE ---
CACHED_GRID = None
LAST_CACHE_TIME = 0
CACHE_TTL = 300 # 🔥 5 minutos (300 segundos) para estar siempre sincronizados

def get_s3_json(key):
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read())
    except:
        return None # Silencioso si no existe

def get_s3_gzip_json(key):
    """Descarga, descomprime y lee un JSON comprimido (.gz) de S3"""
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        compressed_data = obj['Body'].read()
        json_str = gzip.decompress(compressed_data).decode('utf-8')
        return json.loads(json_str)
    except Exception as e:
        print(f"⚠️ No se pudo cargar el resumen GZIP de S3: {e}")
        return None

def get_grid_data():
    global CACHED_GRID, LAST_CACHE_TIME
    
    # 1. ¿Tenemos caché? Vamos a ver qué tan "fresca" está
    if CACHED_GRID is not None:
        edad_cache = time.time() - LAST_CACHE_TIME
        if edad_cache < CACHE_TTL:
            print(f"⚡ [CACHE HIT] Usando grid en RAM. Edad del dato: {int(edad_cache)}s / {CACHE_TTL}s permitidos.")
            return CACHED_GRID
        else:
            print(f"♻️ [CACHE EXPIRED] El grid en RAM caducó (tenía {int(edad_cache)}s). Hay que renovar.")
            
    # 2. Si no hay caché o ya caducó, vamos a S3
    print("☁️ [S3 FETCH] Descargando grid fresco de S3...")
    data = get_s3_json(GRID_KEY)
    
    if data:
        CACHED_GRID = pd.DataFrame(data)
        LAST_CACHE_TIME = time.time() # Guardamos la hora exacta de la descarga
        print("✅ [CACHE UPDATED] Nuevo grid guardado exitosamente en memoria RAM.")
        return CACHED_GRID
        
    return None

def haversine_vectorized(lon1, lat1, df):
    lon1, lat1 = np.radians(lon1), np.radians(lat1)
    lon2, lat2 = np.radians(df['lon'].values), np.radians(df['lat'].values)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    return 6367 * (2 * np.arcsin(np.sqrt(a)))

# --- HELPERS ---
def safe_float(val, precision=1):
    try: return round(float(val), precision)
    except: return 0.0

def safe_int(val):
    try: return int(float(val))
    except: return 0

def get_smart_val(row, keys_to_try):
    for key in keys_to_try:
        if key in row and row[key] is not None:
            return row[key]
    return 0

def get_contexto_aire(ias):
    try:
        val = safe_int(ias)
        if val <= 50: return "Buena", "Verde", "Disfruta el aire libre."
        if val <= 100: return "Regular", "Amarillo", "Reduce esfuerzos fuertes."
        if val <= 150: return "Mala", "Naranja", "Usa cubrebocas."
        if val <= 200: return "Muy Mala", "Rojo", "Peligro: No salgas."
        return "Extremadamente Mala", "Morado", "Alerta Sanitaria."
    except: return "Desconocida", "Gris", "Datos no disponibles."

# --- NUEVO: Motor de Forecast en Paralelo (Tu Idea) ⚡ ---

def lambda_handler(event, context):
    global CACHED_GRID
    try:
        params = event.get('queryStringParameters') or {}
        mode = params.get('mode')
        
        # 1. MAPA WEB
        if mode == 'map':
            if CACHED_GRID is None: get_grid_data()
            if CACHED_GRID is not None:
                return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': CACHED_GRID.to_json(orient='records')}
            return {'statusCode': 503, 'body': 'Error cargando Live Grid'}

        # 2. FORECAST RAW (Data completa de una hora)
        elif mode == 'forecast_data':
            ts = params.get('timestamp')
            if not ts: return {'statusCode': 400, 'body': json.dumps({'error': 'Falta timestamp'})}
            data = get_s3_json(f"forecast/{ts}.json")
            if data: return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(data)}
            return {'statusCode': 404, 'body': json.dumps({'error': 'Forecast no encontrado'})}

        # 3. HISTORY
        elif mode == 'history':
            ts = params.get('timestamp')
            if not ts: return {'statusCode': 400, 'body': json.dumps({'error': 'Falta timestamp'})}
            # OJO: Aquí es donde tu frontend puede estar fallando. 
            # Verifica si el frontend manda "2026-01-28_10-00" o "2026-01-28 10:00"
            data = get_s3_json(f"live_grid/grid_{ts}.json")
            if data: return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(data)}
            return {'statusCode': 404, 'body': json.dumps({'error': 'Historial no encontrado'})}

        # 4. BOT / GEOCERCA
        if 'lat' not in params or 'lon' not in params:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Faltan lat/lon'})}

        u_lat, u_lon = float(params['lat']), float(params['lon'])
        
        if not (LIMITS['LAT_MIN'] <= u_lat <= LIMITS['LAT_MAX'] and LIMITS['LON_MIN'] <= u_lon <= LIMITS['LON_MAX']):
            return {'statusCode': 200, 'body': json.dumps({"status": "out_of_bounds", "mensaje": "Fuera de zona"})}

        # Obtenemos el grid (la función decidirá si usa caché o baja de S3)
        grid_actual = get_grid_data()
        
        if grid_actual is None:
            return {'statusCode': 503, 'body': 'Error cargando datos de aire'}

        distances = haversine_vectorized(u_lon, u_lat, grid_actual) # <-- Ojo, usamos grid_actual
        idx = np.argmin(distances)
        dist = distances[idx]
        p = CACHED_GRID.iloc[idx].replace({np.nan: None}).to_dict()

        # HOMOLOGACIÓN
        o3_val = get_smart_val(p, ['o3', 'o3 1h', 'o3_1h'])
        pm10_val = get_smart_val(p, ['pm10', 'pm10 12h', 'pm10_12h'])
        pm25_val = get_smart_val(p, ['pm25', 'pm25 12h', 'pm25_12h'])
        so2_val = get_smart_val(p, ['so2', 'so2 1h', 'so2_1h'])
        co_val = get_smart_val(p, ['co', 'co 8h', 'co_8h'])
        
        current_ts_str = p.get('timestamp', '')

        # =====================================================================
        # 🌟 EXTRACCIÓN PARALELA DE LA TRINIDAD DE VECTORES 🌟
        # =====================================================================
        vector_ayer = None
        vector_hoy = None
        vector_futuro = None
        meta_hoy_hora = None
        meta_futuro_start = None
        
        try:
            tz = ZoneInfo("America/Mexico_City")
            now_mx = datetime.now(tz)
            ayer_str = (now_mx - timedelta(days=1)).strftime("%Y-%m-%d")
            
            grid_lat, grid_lon = p.get('lat', 0.0), p.get('lon', 0.0)
            geo_key = f"{round(grid_lat, 3)},{round(grid_lon, 3)}"
            
            # Descarga concurrente
            with ThreadPoolExecutor(max_workers=3) as executor:
                f_ayer = executor.submit(get_s3_gzip_json, f"daily_summaries/summary_{ayer_str}.json.gz")
                f_hoy = executor.submit(get_s3_gzip_json, "daily_summaries/summary_today.json.gz")
                f_futuro = executor.submit(get_s3_gzip_json, "forecast_summary/latest_forecast.json.gz")
                res_ayer, res_hoy, res_futuro = f_ayer.result(), f_hoy.result(), f_futuro.result()
            
            # Asignaciones
            if res_ayer and "celdas" in res_ayer and geo_key in res_ayer["celdas"]:
                vector_ayer = res_ayer["celdas"][geo_key]
                
            if res_hoy and "celdas" in res_hoy and geo_key in res_hoy["celdas"]:
                vector_hoy = res_hoy["celdas"][geo_key]
                meta_hoy_hora = res_hoy.get("ultima_hora_procesada")
                
            if res_futuro and "celdas" in res_futuro and geo_key in res_futuro["celdas"]:
                vector_futuro = res_futuro["celdas"][geo_key]
                meta_futuro_start = res_futuro.get("timestamp_start")
                
        except Exception as e:
            print(f"⚠️ Error extrayendo trinidad de vectores: {e}")

        # =====================================================================
        # ⏱️ RECONSTRUIR TIMELINE DE 4 HORAS (Compatibilidad Bot)
        # =====================================================================
        pronostico_timeline = []
        if vector_futuro and 'ias' in vector_futuro and meta_futuro_start:
            try:
                start_dt = datetime.strptime(meta_futuro_start[:16], "%Y-%m-%dT%H:%M")
                lista_dominantes = vector_futuro.get('dominante', ["N/A"] * 24)
                
                # 1. Obtenemos la hora actual truncada para tener una línea base
                tz = ZoneInfo("America/Mexico_City")
                hora_actual = datetime.now(tz).replace(minute=0, second=0, microsecond=0, tzinfo=None)
                
                agregados = 0
                for i in range(len(vector_futuro['ias'])):
                    hora_dt = start_dt + timedelta(hours=i)
                    
                    # 2. EL FIX: Solo agregamos horas que sean MAYORES a la hora actual
                    if hora_dt > hora_actual:
                        ias_val = vector_futuro['ias'][i]
                        
                        if ias_val <= 50: riesgo = "Bajo"
                        elif ias_val <= 100: riesgo = "Moderado"
                        elif ias_val <= 150: riesgo = "Alto"
                        elif ias_val <= 200: riesgo = "Muy Alto"
                        else: riesgo = "Extremadamente Alto"
                        
                        pronostico_timeline.append({
                            "hora": hora_dt.strftime("%H:%M"),
                            "ias": ias_val,
                            "riesgo": riesgo,
                            "dominante": lista_dominantes[i] if i < len(lista_dominantes) else "N/A"
                        })
                        agregados += 1
                        
                    # 3. Cortamos exactamente al tener 4 horas futuras
                    if agregados == 4:
                        break
                        
            except Exception as e:
                print(f"⚠️ Error armando timeline de compatibilidad: {e}")

        # Tendencia
        current_ias = safe_int(p.get('ias', 0))
        trend = "Estable ➡️"
        if pronostico_timeline:
            ias_next = pronostico_timeline[0]['ias']
            if ias_next > current_ias + 5: trend = "Subiendo ↗️"
            elif ias_next < current_ias - 5: trend = "Bajando ↘️"

        calidad, color, mensaje_corto = get_contexto_aire(current_ias)

        response = {
            "status": "success" if dist <= MAX_DISTANCE_KM else "warning",
            "origen": "live",
            "ts": current_ts_str,
            "ubicacion": {
                "distancia": round(dist, 2),
                "zona": p.get('station', 'N/A'),
                "mun": p.get('mun', 'N/A'),
                "edo": "CDMX" if p.get('edo') == 'Ciudad de México' else p.get('edo')
            },
            "aire": {
                "ias": current_ias,
                "calidad": calidad,
                "color": color,
                "tendencia": trend,
                "mensaje_corto": mensaje_corto,
                "dominante": p.get('dominant', 'PM10'),
                "contaminantes": {
                    "o3": safe_float(o3_val),
                    "pm10": safe_float(pm10_val),
                    "pm25": safe_float(pm25_val),
                    "so2": safe_float(so2_val),
                    "co": safe_float(co_val, 2)
                }
            },
            "meteo": {
                "tmp": safe_float(p.get('tmp')),
                "rh": safe_float(p.get('rh')),
                "wsp": safe_float(p.get('wsp'))
            },
            "pronostico_timeline": pronostico_timeline,
            "vectores": {
                "ayer": vector_ayer,
                "hoy": vector_hoy,
                "futuro": vector_futuro
            },
            "metadata_tiempo": {
                "hoy_ultima_hora": meta_hoy_hora,
                "forecast_start": meta_futuro_start
            }
        }
        
        return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(response)}

    except Exception as e:
        print(f"🔥 ERROR: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
