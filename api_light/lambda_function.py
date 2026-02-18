import json
import boto3
import pandas as pd
import numpy as np
import os
import gzip
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
    global CACHED_GRID
    data = get_s3_json(GRID_KEY)
    if data:
        CACHED_GRID = pd.DataFrame(data)
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
def fetch_forecast_hour(target_dt, lat, lon):
    """Baja un archivo de forecast específico y extrae el dato para esa lat/lon"""
    # Formato de archivo: forecast/2026-01-28_11-00.json
    fname = target_dt.strftime("%Y-%m-%d_%H-00") 
    key = f"forecast/{fname}.json"
    
    data = get_s3_json(key)
    if not data: return None
    
    # Encontrar el punto más cercano en ESE archivo de forecast
    # (Hacemos esto porque el forecast es un grid completo)
    df = pd.DataFrame(data)
    
    # Calculamos distancia rápida (asumimos que el grid es igual, pero por seguridad recalculamos)
    # Optimizacion: Si el grid es identico, podriamos usar el mismo indice, 
    # pero para asegurar precision buscamos de nuevo.
    distances = haversine_vectorized(lon, lat, df)
    idx = np.argmin(distances)
    
    row = df.iloc[idx]
    
    # Extraemos info
    return {
        "hora": target_dt.strftime("%H:%M"),
        "ias": safe_int(get_smart_val(row, ['ias', 'ias_mean'])),
        "riesgo": row.get('risk', 'N/A'),
        "dominante": row.get('dominant', 'N/A')
    }

def get_parallel_forecast(start_dt_str, lat, lon):
    """Lanza 4 hilos para buscar las siguientes 4 horas"""
    forecast_timeline = []
    
    try:
        # Parseamos fecha actual. Asumimos formato ISO o similar "YYYY-MM-DD HH:MM:SS"
        # Cortamos a 16 chars "2026-01-28 10:20"
        current_dt = datetime.strptime(start_dt_str[:16], "%Y-%m-%d %H:%M")
        
        # Generamos las 4 horas objetivo (Next Hour en punto)
        # Si son 10:20, la siguiente es 11:00, 12:00, 13:00, 14:00
        next_hour = (current_dt + timedelta(hours=1)).replace(minute=0, second=0)
        target_hours = [next_hour + timedelta(hours=i) for i in range(4)]
        
        with ThreadPoolExecutor(max_workers=4) as executor:
            # Lanzamos las tareas
            future_to_hour = {executor.submit(fetch_forecast_hour, h, lat, lon): h for h in target_hours}
            
            for future in as_completed(future_to_hour):
                res = future.result()
                if res:
                    forecast_timeline.append(res)
        
        # Ordenamos por hora porque los hilos pueden terminar en desorden
        forecast_timeline.sort(key=lambda x: x['hora'])
        
    except Exception as e:
        print(f"Error forecast paralelo: {e}")
        
    return forecast_timeline

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

        if CACHED_GRID is None and get_grid_data() is None:
            return {'statusCode': 503, 'body': 'Sistema iniciandose...'}

        distances = haversine_vectorized(u_lon, u_lat, CACHED_GRID)
        idx = np.argmin(distances)
        dist = distances[idx]
        p = CACHED_GRID.iloc[idx].replace({np.nan: None}).to_dict()

        # HOMOLOGACIÓN
        o3_val = get_smart_val(p, ['o3', 'o3 1h', 'o3_1h'])
        pm10_val = get_smart_val(p, ['pm10', 'pm10 12h', 'pm10_12h'])
        pm25_val = get_smart_val(p, ['pm25', 'pm25 12h', 'pm25_12h'])
        so2_val = get_smart_val(p, ['so2', 'so2 1h', 'so2_1h'])
        co_val = get_smart_val(p, ['co', 'co 8h', 'co_8h'])
        
        # --- PRONÓSTICO PARALELO (Tu solución) ---
        current_ts_str = p.get('timestamp', '')
        future_forecast = []
        if current_ts_str:
            future_forecast = get_parallel_forecast(current_ts_str, u_lat, u_lon)

        # Tendencia
        current_ias = safe_int(p.get('ias', 0))
        trend = "Estable ➡️"
        if future_forecast:
            if future_forecast[0]['ias'] > current_ias + 5: trend = "Subiendo ↗️"
            elif future_forecast[0]['ias'] < current_ias - 5: trend = "Bajando ↘️"

        calidad, color, mensaje_corto = get_contexto_aire(current_ias)

        # =====================================================================
        # 🌟 NUEVA SECCIÓN: EXTRAER VECTOR DE EXPOSICIÓN (AYER) 🌟
        # =====================================================================
        vector_exposicion = None
        try:
            # Determinamos la fecha de "Ayer"
            tz = ZoneInfo("America/Mexico_City")
            now_mx = datetime.now(tz)
            ayer_str = (now_mx - timedelta(days=1)).strftime("%Y-%m-%d")
            
            # Redondeamos la lat/lon del usuario a 3 decimales para cruzar con el diccionario
            geo_key = f"{round(u_lat, 3)},{round(u_lon, 3)}"
            
            # Buscamos el archivo en S3
            resumen_diario = get_s3_gzip_json(f"daily_summaries/summary_{ayer_str}.json.gz")
            
            if resumen_diario and "celdas" in resumen_diario:
                # Búsqueda directa ultrarrápida O(1)
                if geo_key in resumen_diario["celdas"]:
                    vector_exposicion = resumen_diario["celdas"][geo_key]
        except Exception as e:
            print(f"⚠️ Error extrayendo vector de exposición: {e}")
        # =====================================================================

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
            "pronostico_timeline": future_forecast,
            "vector_exposicion_ayer": vector_exposicion
        }
        
        return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(response)}

    except Exception as e:
        print(f"🔥 ERROR: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
