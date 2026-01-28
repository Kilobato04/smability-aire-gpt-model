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
    """Helper para bajar JSON de S3 de forma segura"""
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read())
    except Exception as e:
        print(f"❌ Error leyendo {key}: {e}")
        return None

def get_grid_data():
    global CACHED_GRID
    data = get_s3_json(GRID_KEY)
    if data:
        CACHED_GRID = pd.DataFrame(data)
        # Asegurar columnas mínimas
        for col in ['mun', 'edo', 'building_vol', 'dominant', 'station']:
            if col not in CACHED_GRID.columns: CACHED_GRID[col] = "N/A"
        return CACHED_GRID
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
        mode = params.get('mode')
        
        # ==========================================
        # 1. MODO MAPA WEB (Grid en Vivo)
        # ==========================================
        if mode == 'map':
            if CACHED_GRID is None: get_grid_data()
            if CACHED_GRID is not None:
                return {
                    'statusCode': 200, 
                    'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                    'body': CACHED_GRID.to_json(orient='records')
                }
            else:
                return {'statusCode': 503, 'body': 'Error cargando Live Grid'}

        # ==========================================
        # 2. MODO FORECAST DATA (Nuevo) 🆕
        # ==========================================
        # Busca descargar un grid completo de una hora específica
        # URL ej: ?mode=forecast_data&timestamp=2025-12-17_16-00
        elif mode == 'forecast_data':
            ts = params.get('timestamp')
            if not ts:
                return {'statusCode': 400, 'body': json.dumps({'error': 'Falta parametro timestamp'})}
            
            # Construimos la ruta del archivo. 
            # NOTA: Asumimos que viven en la carpeta 'forecasts/' con el nombre exacto del timestamp
            # Si tu estructura es diferente, ajusta esta línea:
            file_key = f"forecasts/{ts}.json" 
            
            data = get_s3_json(file_key)
            
            if data:
                return {
                    'statusCode': 200, 
                    'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                    'body': json.dumps(data)
                }
            else:
                return {
                    'statusCode': 404, 
                    'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                    'body': json.dumps({'error': 'Archivo de pronostico no encontrado', 'path': file_key})
                }

        # ==========================================
        # 3. MODO PUNTUAL (Chatbot / Geocerca)
        # ==========================================
        if 'lat' not in params or 'lon' not in params:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Faltan lat/lon'})}

        u_lat, u_lon = float(params['lat']), float(params['lon'])
        
        # Validar zona
        if not (LIMITS['LAT_MIN'] <= u_lat <= LIMITS['LAT_MAX'] and LIMITS['LON_MIN'] <= u_lon <= LIMITS['LON_MAX']):
            return {'statusCode': 200, 'body': json.dumps({"status": "out_of_bounds", "mensaje": "Ubicación fuera de CDMX"})}

        if CACHED_GRID is None and get_grid_data() is None:
            return {'statusCode': 503, 'body': 'Grid no disponible'}

        # Encontrar punto más cercano
        distances = haversine_vectorized(u_lon, u_lat, CACHED_GRID)
        idx = np.argmin(distances)
        dist = distances[idx]
        p = CACHED_GRID.iloc[idx].replace({np.nan: None}).to_dict()

        # Cargar Timeline (Pronóstico de 24h para ese punto)
        timeline = get_s3_json(FORECAST_KEY) or []
        if isinstance(timeline, list) and len(timeline) > 4:
            timeline = timeline[:4] # Solo las próximas 4h

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
                "dominante": p.get('dominant', 'PM10'),
                "o3": p.get('o3', 0),
                "pm10": p.get('pm10', 0),
                "pm25": p.get('pm25', 0)
            },
            "meteo": {
                "tmp": p.get('tmp', 0),
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
