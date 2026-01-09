import json
import boto3
import pandas as pd
import numpy as np
import os
from math import radians

S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
GRID_KEY = 'live_grid/latest_grid.json'
s3 = boto3.client('s3')

LIMITS = {'LAT_MIN': 19.13, 'LAT_MAX': 19.80, 'LON_MIN': -99.40, 'LON_MAX': -98.80}
MAX_DISTANCE_KM = 10.0

CACHED_GRID = None

def get_grid_data():
    global CACHED_GRID
    try:
        print("📥 API Light: Cargando Grid S3...", flush=True)
        obj = s3.get_object(Bucket=S3_BUCKET, Key=GRID_KEY)
        data = json.loads(obj['Body'].read())
        CACHED_GRID = pd.DataFrame(data)
        
        # Validar columnas por seguridad
        for col in ['mun', 'edo', 'building_vol', 'pm25']:
            if col not in CACHED_GRID.columns: CACHED_GRID[col] = 0 if col == 'building_vol' else 'N/A'
            
        print(f"✅ Grid cargado: {len(CACHED_GRID)} puntos.", flush=True)
        return CACHED_GRID
    except Exception as e:
        print(f"❌ Error S3: {e}", flush=True)
        return None

def haversine_vectorized(lon1, lat1, df):
    lon1, lat1 = np.radians(lon1), np.radians(lat1)
    lon2, lat2 = np.radians(df['lon'].values), np.radians(df['lat'].values)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    c = 2 * np.arcsin(np.sqrt(a))
    return 6367 * c

def lambda_handler(event, context):
    global CACHED_GRID
    try:
        params = event.get('queryStringParameters', {})
        if not params: params = {}
        
        # MODO MAPA WEB (Descarga completa)
        if params.get('mode') == 'map':
            if CACHED_GRID is None: get_grid_data()
            return {'statusCode': 200, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}, 'body': CACHED_GRID.to_json(orient='records')}

        # MODO PUNTUAL (Chatbot)
        if 'lat' not in params or 'lon' not in params:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Faltan lat/lon'})}

        user_lat, user_lon = float(params['lat']), float(params['lon'])
        
        if not (LIMITS['LAT_MIN'] <= user_lat <= LIMITS['LAT_MAX'] and LIMITS['LON_MIN'] <= user_lon <= LIMITS['LON_MAX']):
            return {'statusCode': 200, 'body': json.dumps({"status": "out_of_bounds", "mensaje": "Fuera de ZMVM"})}
        
        if CACHED_GRID is None:
            if get_grid_data() is None: return {'statusCode': 503, 'body': 'Grid error'}
            
        distances = haversine_vectorized(user_lon, user_lat, CACHED_GRID)
        min_idx = np.argmin(distances)
        nearest_dist = distances[min_idx]
        point = CACHED_GRID.iloc[min_idx].replace({np.nan: None}).to_dict()
        
        status = "success" if nearest_dist <= MAX_DISTANCE_KM else "warning"
        msg = "Cobertura OK" if status == "success" else "Ubicación lejana"

        response = {
            "status": status,
            "mensaje_sistema": msg,
            "ubicacion": {
                "distancia_nodo_km": round(nearest_dist, 2),
                "zona_detectada": point.get('station'),
                "alcaldia": point.get('mun', 'Desconocido'),
                "estado": point.get('edo', 'Desconocido'),
                "tipo_zona": "Alta Densidad" if point.get('building_vol', 0) > 200000 else "Baja Densidad"
            },
            "calidad_aire": {
                "ias_puntos": int(point.get('ias', 0)),
                "riesgo_salud": point.get('risk', 'N/A'),
                "dominante": point.get('dominant', 'N/A'),
                "detalle": {
                    "ozono_ppb": point.get('o3', 0),
                    "pm10_ug": point.get('pm10', 0),
                    "pm25_ug": point.get('pm25', 0)
                }
            },
            "meteorologia": {
                "temp_c": point.get('tmp', 0),
                "humedad": int(point.get('rh', 0)),
                "viento_ms": point.get('wsp', 0),
                "altitud": int(point.get('altitude', 0))
            },
            "timestamp": point.get('timestamp')
        }
        
        return {'statusCode': 200, 'headers': {'Content-Type': 'application/json'}, 'body': json.dumps(response)}

    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
