import json
import boto3
import pandas as pd
import numpy as np
import os
from math import radians

# --- CONFIGURACIÓN ---
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
GRID_KEY = 'live_grid/latest_grid.json'
s3 = boto3.client('s3')

CACHED_GRID = None

def get_grid_data():
    global CACHED_GRID
    print("📥 Descargando Grid desde S3 (Cold Start)...")
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=GRID_KEY)
        data = json.loads(obj['Body'].read())
        CACHED_GRID = pd.DataFrame(data)
        print(f"✅ Grid cargado: {len(CACHED_GRID)} puntos.")
        return True
    except Exception as e:
        print(f"❌ Error S3: {e}")
        return False

def haversine_vectorized(lon1, lat1, df):
    lon1, lat1 = np.radians(lon1), np.radians(lat1)
    lon2, lat2 = np.radians(df['lon'].values), np.radians(df['lat'].values)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    c = 2 * np.arcsin(np.sqrt(a))
    km = 6367 * c
    return km

def lambda_handler(event, context):
    global CACHED_GRID
    try:
        params = event.get('queryStringParameters', {})
        if not params or 'lat' not in params or 'lon' not in params:
            return {'statusCode': 400, 'headers': {'Content-Type': 'application/json'}, 'body': json.dumps({'error': 'Faltan parametros lat y lon'})}

        user_lat = float(params['lat'])
        user_lon = float(params['lon'])
        
        if CACHED_GRID is None:
            success = get_grid_data()
            if not success: return {'statusCode': 503, 'body': json.dumps({'error': 'Grid no disponible'})}
            
        distances = haversine_vectorized(user_lon, user_lat, CACHED_GRID)
        min_idx = np.argmin(distances)
        nearest_dist = distances[min_idx]
        point = CACHED_GRID.iloc[min_idx].to_dict()
        
        status = "success"
        msg = "Cobertura OK"
        if nearest_dist > 5.0:
            status = "warning"
            msg = "Fuera de cobertura oficial (>5km)."

        response = {
            "status": status,
            "mensaje_sistema": msg,
            "ubicacion": {
                "distancia_nodo_km": round(nearest_dist, 2),
                "zona_detectada": point.get('station') or "Interpolación Modelo"
            },
            "calidad_aire": {
                "ias_puntos": int(point.get('ias', 0)),
                "riesgo_salud": point.get('risk', 'N/A'),
                "contaminante_dominante": point.get('dominant', 'N/A'),
                "detalle": {
                    "ozono_ppb": round(point.get('o3', 0), 1),
                    "pm10_ug": round(point.get('pm10', 0), 1),
                    "pm25_ug": round(point.get('pm25', 0), 1)
                }
            },
            "meteorologia": {
                "temp_c": round(point.get('tmp', 0), 1),
                "humedad_rel": int(point.get('rh', 0)),
                "viento_ms": round(point.get('wsp', 0), 1),
                "altitud_m": int(point.get('altitude', 0))
            },
            "timestamp_lectura": point.get('timestamp', 'N/A')
        }
        return {'statusCode': 200, 'headers': {'Content-Type': 'application/json'}, 'body': json.dumps(response)}
    except Exception as e:
        print(f"Error: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
