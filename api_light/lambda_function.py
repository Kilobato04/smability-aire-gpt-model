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
        # Cargamos la data pura, SIN crear columnas basura 'N/A'
        CACHED_GRID = pd.DataFrame(data)
        return CACHED_GRID
    return None

def haversine_vectorized(lon1, lat1, df):
    lon1, lat1 = np.radians(lon1), np.radians(lat1)
    lon2, lat2 = np.radians(df['lon'].values), np.radians(df['lat'].values)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    return 6367 * (2 * np.arcsin(np.sqrt(a)))

# --- HELPERS BLINDADOS 🛡️ ---
def safe_float(val, precision=1):
    """Intenta convertir a float. Si es texto, None o fallo, devuelve 0.0"""
    try:
        return round(float(val), precision)
    except (ValueError, TypeError):
        return 0.0

def safe_int(val):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0

def get_smart_val(row, keys_to_try):
    """
    Busca el valor en una lista de posibles llaves.
    Ej: para Ozono busca en ['o3', 'o3 1h', 'o3_1h']
    """
    for key in keys_to_try:
        if key in row and row[key] is not None:
            return row[key]
    return 0  # Si no encuentra nada

def get_contexto_aire(ias):
    try:
        val = safe_int(ias)
        if val <= 50: return "Buena", "Verde", "Disfruta el aire libre, condiciones ideales."
        if val <= 100: return "Regular", "Amarillo", "Aceptable, reduce esfuerzos fuertes si eres sensible."
        if val <= 150: return "Mala", "Naranja", "Evita actividades al aire libre, usa cubrebocas."
        if val <= 200: return "Muy Mala", "Rojo", "Peligro: No salgas, mantén ventanas cerradas."
        return "Extremadamente Mala", "Morado", "Alerta Sanitaria: Evita toda exposición."
    except:
        return "Desconocida", "Gris", "Datos no disponibles."

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
            return {'statusCode': 503, 'body': 'Error cargando Live Grid'}

        # 2. MODO FORECAST DATA
        elif mode == 'forecast_data':
            ts = params.get('timestamp')
            if not ts: return {'statusCode': 400, 'body': json.dumps({'error': 'Falta timestamp'})}
            data = get_s3_json(f"forecast/{ts}.json")
            if data: return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(data)}
            return {'statusCode': 404, 'body': json.dumps({'error': 'Forecast no encontrado'})}

        # 3. MODO HISTORY
        elif mode == 'history':
            ts = params.get('timestamp')
            if not ts: return {'statusCode': 400, 'body': json.dumps({'error': 'Falta timestamp'})}
            data = get_s3_json(f"live_grid/grid_{ts}.json")
            if data: return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(data)}
            return {'statusCode': 404, 'body': json.dumps({'error': 'Historial no encontrado'})}

        # ==========================================
        # 4. MODO BOT / GEOCERCA 🤖 (CORREGIDO)
        # ==========================================
        if 'lat' not in params or 'lon' not in params:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Faltan lat/lon'})}

        u_lat, u_lon = float(params['lat']), float(params['lon'])
        
        if not (LIMITS['LAT_MIN'] <= u_lat <= LIMITS['LAT_MAX'] and LIMITS['LON_MIN'] <= u_lon <= LIMITS['LON_MAX']):
            return {'statusCode': 200, 'body': json.dumps({"status": "out_of_bounds", "mensaje": "Fuera de zona"})}

        if CACHED_GRID is None and get_grid_data() is None:
            return {'statusCode': 503, 'body': 'Sistema iniciandose...'}

        # Encontrar punto
        distances = haversine_vectorized(u_lon, u_lat, CACHED_GRID)
        idx = np.argmin(distances)
        dist = distances[idx]
        p = CACHED_GRID.iloc[idx].replace({np.nan: None}).to_dict()

        # --- EXTRACCIÓN INTELIGENTE DE DATOS (HOMOLOGACIÓN) ---
        # Aquí solucionamos el problema de los nombres. Buscamos todas las variantes posibles.
        o3_val = get_smart_val(p, ['o3', 'o3 1h', 'o3_1h'])
        pm10_val = get_smart_val(p, ['pm10', 'pm10 12h', 'pm10_12h'])
        pm25_val = get_smart_val(p, ['pm25', 'pm25 12h', 'pm25_12h'])
        so2_val = get_smart_val(p, ['so2', 'so2 1h', 'so2_1h'])
        co_val = get_smart_val(p, ['co', 'co 8h', 'co_8h'])
        
        # Pronóstico Timeline
        current_ts_str = p.get('timestamp', '')
        timeline_raw = get_s3_json(FORECAST_KEY) or []
        future_forecast = []
        
        if timeline_raw and current_ts_str:
            try:
                # El formato de fecha puede variar (con :ss o sin :ss), cortamos a 16 chars para comparar minutos
                # "2026-01-28 09:20" vs "2026-01-28 09:00"
                ts_base = current_ts_str[:16] 
                
                for f in timeline_raw:
                    f_ts = f.get('timestamp', '')
                    # Solo tomamos horas futuras
                    if f_ts > ts_base:
                        # Buscamos 'ias' O 'ias_mean' (por si acaso el forecast summary usa nombres distintos)
                        ias_forecast = get_smart_val(f, ['ias', 'ias_mean'])
                        
                        future_forecast.append({
                            "hora": f_ts[11:16], # Hora HH:MM
                            "ias": safe_int(ias_forecast),
                            "riesgo": f.get('risk', 'N/A'),
                            "dominante": f.get('dominant', 'N/A')
                        })
                        
                        if len(future_forecast) >= 4: break # Solo queremos 4
            except Exception as e:
                print(f"Error parseando timeline: {e}")

        # Tendencia
        current_ias = safe_int(p.get('ias', 0))
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
            "pronostico_timeline": future_forecast
        }
        
        return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(response)}

    except Exception as e:
        print(f"🔥 ERROR: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
