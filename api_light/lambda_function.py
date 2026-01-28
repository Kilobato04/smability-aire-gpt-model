import json
import boto3
import pandas as pd
import numpy as np
import os
from datetime import datetime

# --- CONFIGURACIÓN ---
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
GRID_KEY = 'live_grid/latest_grid.json'
FORECAST_KEY = 'forecast_24h.json'
s3 = boto3.client('s3')

LIMITS = {'LAT_MIN': 19.13, 'LAT_MAX': 19.80, 'LON_MIN': -99.40, 'LON_MAX': -98.80}
MAX_DISTANCE_KM = 10.0
CACHED_GRID = None

def get_s3_json(key):
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read())
    except Exception as e:
        print(f"⚠️ Nota: No se pudo leer {key}: {e}")
        return None

def get_grid_data():
    global CACHED_GRID
    data = get_s3_json(GRID_KEY)
    if data:
        CACHED_GRID = pd.DataFrame(data)
        # Aseguramos columnas. Si faltan, ponemos 'N/A' (texto) o 0 (numeros)
        # Para evitar líos, usaremos N/A y el safe_float lo limpiará después
        cols_needed = ['mun', 'edo', 'station', 'dominant', 'so2', 'co']
        for col in cols_needed:
            if col not in CACHED_GRID.columns: CACHED_GRID[col] = "N/A"
        return CACHED_GRID
    return None

def haversine_vectorized(lon1, lat1, df):
    lon1, lat1 = np.radians(lon1), np.radians(lat1)
    lon2, lat2 = np.radians(df['lon'].values), np.radians(df['lat'].values)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    return 6367 * (2 * np.arcsin(np.sqrt(a)))

# --- HELPERS SEGUROS (NUEVOS) 🛡️ ---
def safe_float(val, precision=1):
    """Convierte a float de forma segura. Si falla (ej 'N/A'), devuelve 0.0"""
    try:
        return round(float(val), precision)
    except (ValueError, TypeError):
        return 0.0

def safe_int(val):
    """Convierte a int de forma segura. Si falla, devuelve 0"""
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0

def get_contexto_aire(ias):
    """Devuelve calidad, color y mensaje corto basado en el IAS"""
    try:
        val = safe_int(ias)
        if val <= 50: 
            return "Buena", "Verde", "Disfruta el aire libre, condiciones ideales."
        if val <= 100: 
            return "Regular", "Amarillo", "Aceptable, pero reduce esfuerzos fuertes si eres sensible."
        if val <= 150: 
            return "Mala", "Naranja", "Evita actividades al aire libre, usa cubrebocas si sales."
        if val <= 200: 
            return "Muy Mala", "Rojo", "Peligro: No salgas, mantén ventanas cerradas."
        return "Extremadamente Mala", "Morado", "Alerta Sanitaria: Evita toda exposición al exterior."
    except:
        return "Desconocida", "Gris", "Datos no disponibles temporalmente."

def lambda_handler(event, context):
    global CACHED_GRID
    try:
        params = event.get('queryStringParameters') or {}
        mode = params.get('mode')
        
        # 1. MODO MAPA WEB
        if mode == 'map':
            if CACHED_GRID is None: get_grid_data()
            if CACHED_GRID is not None:
                return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': CACHED_GRID.to_json(orient='records')}
            else:
                return {'statusCode': 503, 'body': 'Error cargando Live Grid'}

        # 2. MODO FORECAST DATA
        elif mode == 'forecast_data':
            ts = params.get('timestamp')
            if not ts: return {'statusCode': 400, 'body': json.dumps({'error': 'Falta timestamp'})}
            file_key = f"forecast/{ts}.json"
            data = get_s3_json(file_key)
            if data: return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(data)}
            else: return {'statusCode': 404, 'body': json.dumps({'error': 'Forecast no encontrado'})}

        # 3. MODO HISTORY
        elif mode == 'history':
            ts = params.get('timestamp')
            if not ts: return {'statusCode': 400, 'body': json.dumps({'error': 'Falta timestamp'})}
            file_key = f"live_grid/grid_{ts}.json"
            data = get_s3_json(file_key)
            if data: return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(data)}
            else: return {'statusCode': 404, 'body': json.dumps({'error': 'Historial no encontrado'})}

        # ==========================================
        # 4. MODO BOT / GEOCERCA 🤖
        # ==========================================
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

        # Pronostico
        current_ts_str = p.get('timestamp', '')
        timeline_raw = get_s3_json(FORECAST_KEY) or []
        future_forecast = []
        if timeline_raw and current_ts_str:
            try:
                future_forecast = [
                    {
                        "hora": f.get('timestamp', '')[11:16],
                        "ias": safe_int(f.get('ias_mean', 0)), # USAMOS SAFE_INT
                        "riesgo": f.get('risk', 'N/A'),
                        "dominante": f.get('dominant', 'N/A')
                    }
                    for f in timeline_raw 
                    if f.get('timestamp', '') > current_ts_str
                ][:4]
            except: pass

        # Tendencia
        current_ias = safe_int(p.get('ias', 0)) # USAMOS SAFE_INT
        trend = "Estable ➡️"
        if future_forecast:
            next_ias = future_forecast[0]['ias']
            if next_ias > current_ias + 5: trend = "Subiendo ↗️"
            elif next_ias < current_ias - 5: trend = "Bajando ↘️"

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
                    "o3": safe_float(p.get('o3')),    # BLINDADO 🛡️
                    "pm10": safe_float(p.get('pm10')), # BLINDADO 🛡️
                    "pm25": safe_float(p.get('pm25')), # BLINDADO 🛡️
                    "so2": safe_float(p.get('so2')),   # BLINDADO 🛡️
                    "co": safe_float(p.get('co'), 2)   # BLINDADO 🛡️
                }
            },
            "meteo": {
                "tmp": safe_float(p.get('tmp')), # BLINDADO 🛡️
                "rh": safe_float(p.get('rh')),   # BLINDADO 🛡️
                "wsp": safe_float(p.get('wsp'))  # BLINDADO 🛡️
            },
            "pronostico_timeline": future_forecast
        }
        
        return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(response)}

    except Exception as e:
        # Imprimir el error exacto en los logs de CloudWatch para debug futuro
        print(f"🔥 ERROR FATAL: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
