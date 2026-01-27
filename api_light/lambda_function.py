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

def get_grid_data():
    global CACHED_GRID
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=GRID_KEY)
        data = json.loads(obj['Body'].read())
        CACHED_GRID = pd.DataFrame(data)
        # Asegurar columnas mínimas para evitar errores de despliegue
        for col in ['mun', 'edo', 'building_vol', 'dominant', 'station']:
            if col not in CACHED_GRID.columns: CACHED_GRID[col] = "N/A"
        return CACHED_GRID
    except Exception as e:
        print(f"❌ Error S3: {e}")
        return None

def haversine_vectorized(lon1, lat1, df):
    lon1, lat1 = np.radians(lon1), np.radians(lat1)
    lon2, lat2 = np.radians(df['lon'].values), np.radians(df['lat'].values)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    return 6367 * (2 * np.arcsin(np.sqrt(a)))

def lambda_handler(event, context):
    global CACHED_GRID
    try:
        params = event.get('queryStringParameters') or {}
        
        # 1. MODO MAPA WEB (Descarga rápida de todo el grid)
        if params.get('mode') == 'map':
            if CACHED_GRID is None: get_grid_data()
            return {
                'statusCode': 200, 
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': CACHED_GRID.to_json(orient='records')
            }

        # 2. MODO PUNTUAL (Chatbot / Geocerca)
        if 'lat' not in params or 'lon' not in params:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Faltan lat/lon'})}

        u_lat, u_lon = float(params['lat']), float(params['lon'])
        
        # Validar si está en zona de cobertura
        if not (LIMITS['LAT_MIN'] <= u_lat <= LIMITS['LAT_MAX'] and LIMITS['LON_MIN'] <= u_lon <= LIMITS['LON_MAX']):
            return {'statusCode': 200, 'body': json.dumps({"status": "out_of_bounds", "mensaje": "Ubicación fuera de CDMX/ZMVM"})}

        if CACHED_GRID is None and get_grid_data() is None:
            return {'statusCode': 503, 'body': 'Grid no disponible'}

        # Encontrar punto más cercano
        distances = haversine_vectorized(u_lon, u_lat, CACHED_GRID)
        idx = np.argmin(distances)
        dist = distances[idx]
        p = CACHED_GRID.iloc[idx].replace({np.nan: None}).to_dict()

        # 3. CONSTRUCCIÓN DE LA RESPUESTA (Sincronizada con tu objeto de ejemplo)
        # Intentar cargar pronóstico
        timeline = []
        try:
            f_obj = s3.get_object(Bucket=S3_BUCKET, Key=FORECAST_KEY)
            timeline = json.loads(f_obj['Body'].read())[:4] # Solo las próximas 4h
        except: pass

        response = {
            "status": "success" if dist <= MAX_DISTANCE_KM else "warning",
            "origen": "live",
            "ubicacion": {
                "distancia": round(dist, 2),
                "zona": p.get('station', 'N/A'),
                "mun": p.get('mun', 'N/A'),
                "edo": p.get('edo', 'N/A')
            },
            "aire": {
                "ias": int(p.get('ias', 0)),
                "riesgo": p.get('risk', 'N/A'),
                "dominante": p.get('dominant', 'PM10'), # Tu campo clave
                "o3": p.get('o3', 0),
                "pm10": p.get('pm10', 0),
                "pm25": p.get('pm25', 0)
            },
            "meteo": {
                "tmp": p.get('tmp', 0), # Keys sincronizadas con Frontend
                "rh": p.get('rh', 0),
                "wsp": p.get('wsp', 0)
            },
            "ts": p.get('timestamp'),
            "pronostico_timeline": timeline
        }
        
        return {
            'statusCode': 200, 
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps(response)
        }

    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
