import json
import boto3
import pandas as pd
import numpy as np
import xgboost as xgb
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os

# --- CONFIGURACIÓN ---
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
S3_GRID_OUTPUT_KEY = os.environ.get('S3_GRID_OUTPUT_KEY', 'live_grid/latest_grid.json')
TARGET_POLLUTANT = 'o3' 
MODEL_PATH = os.path.join(os.environ.get('LAMBDA_TASK_ROOT', '/var/task'), 'model_o3.json')
# Usamos /tmp/ para evitar error de Read-only file system
GRID_BASE_PATH = '/tmp/grid_base_v6_wind.csv'
SMABILITY_API_URL = os.environ.get('SMABILITY_API_URL', 'https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference,smaa')

s3_client = boto3.client('s3')

def load_model_local():
    model = xgb.XGBRegressor()
    model.load_model(MODEL_PATH) 
    return model

def get_live_data():
    try:
        print(f"📡 Conectando a API...")
        response = requests.get(SMABILITY_API_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get('stations', [])
    except Exception as e:
        print(f"❌ Error Fatal API: {e}")
        return []

def process_stations_data(stations_list):
    """Procesa estaciones, valida calidad y extrae física (Viento, Altitud, Clima)."""
    parsed_data = []
    
    for s in stations_list:
        try:
            if s.get('latitude') is None or s.get('longitude') is None: continue
                
            lat = float(s.get('latitude'))
            lon = float(s.get('longitude'))
            # Altitud con fallback seguro
            alt = float(s.get('altitude')) if s.get('altitude') is not None else 2240.0
            
            meteo = s.get('meteorological', {})
            if not meteo: continue

            def get_val(key): return meteo.get(key, {}).get('avg_1h', {}).get('value')
            temp = get_val('temperature')
            rh = get_val('relative_humidity')
            wsp = get_val('wind_speed')
            wdr = get_val('wind_direction')
            
            # Filtro de Calidad: Requerimos Temp y HR como mínimo
            if temp is not None and rh is not None:
                if wsp is None: wsp = 0.0
                if wdr is None: wdr = 0.0
                parsed_data.append({
                    'lat': lat, 'lon': lon, 'alt': alt, 
                    'tmp': float(temp), 'rh': float(rh), 
                    'wsp': float(wsp), 'wdr': float(wdr)
                })
        except Exception:
            continue
            
    print(f"📊 Estaciones válidas para interpolar: {len(parsed_data)}")
    return pd.DataFrame(parsed_data)

def inverse_distance_weighting(x, y, z, xi, yi, power=2):
    dist = np.sqrt((xi[:, None] - x[None, :])**2 + (yi[:, None] - y[None, :])**2)
    dist = np.maximum(dist, 1e-12)
    weights = 1.0 / (dist ** power)
    return np.sum(weights * z[None, :], axis=1) / np.sum(weights, axis=1)

def prepare_grid_features(stations_df):
    # Límites Geográficos (Cobertura Norte-Sur ampliada)
    LAT_MIN, LAT_MAX = 19.15, 19.73
    LON_MIN, LON_MAX = -99.39, -98.91
    RESOLUTION = 0.01

    try:
        grid_df = pd.read_csv(GRID_BASE_PATH)
    except FileNotFoundError:
        lats = np.arange(LAT_MIN, LAT_MAX, RESOLUTION)
        lons = np.arange(LON_MIN, LON_MAX, RESOLUTION)
        grid_points = [{'lat': lat, 'lon': lon} for lat in lats for lon in lons]
        grid_df = pd.DataFrame(grid_points)
        grid_df.to_csv(GRID_BASE_PATH, index=False)

    # Variables Temporales (Zona Horaria CDMX)
    cdmx_tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(cdmx_tz)
    hour = now.hour; month = now.month
    
    grid_df['hour_sin'] = np.sin(2 * np.pi * hour / 24)
    grid_df['hour_cos'] = np.cos(2 * np.pi * hour / 24)
    grid_df['month_sin'] = np.sin(2 * np.pi * month / 12)
    grid_df['month_cos'] = np.cos(2 * np.pi * month / 12)
    
    if not stations_df.empty:
        st_lats = stations_df['lat'].values
        st_lons = stations_df['lon'].values
        grid_lats = grid_df['lat'].values
        grid_lons = grid_df['lon'].values
        
        # Interpolación Escalar (IDW)
        grid_df['tmp'] = inverse_distance_weighting(st_lats, st_lons, stations_df['tmp'].values, grid_lats, grid_lons).round(1)
        grid_df['rh'] = inverse_distance_weighting(st_lats, st_lons, stations_df['rh'].values, grid_lats, grid_lons).round(1)
        grid_df['altitude'] = inverse_distance_weighting(st_lats, st_lons, stations_df['alt'].values, grid_lats, grid_lons).round(0)
        
        # Interpolación Vectorial (Viento)
        wdr_rad = np.deg2rad(stations_df['wdr'].values)
        wsp_vals = stations_df['wsp'].values
        u_comp = wsp_vals * np.cos(wdr_rad)
        v_comp = wsp_vals * np.sin(wdr_rad)
        
        grid_u = inverse_distance_weighting(st_lats, st_lons, u_comp, grid_lats, grid_lons)
        grid_v = inverse_distance_weighting(st_lats, st_lons, v_comp, grid_lats, grid_lons)
        
        grid_df['wsp'] = np.sqrt(grid_u**2 + grid_v**2).round(1)
        grid_df['wdr'] = (np.degrees(np.arctan2(grid_v, grid_u)) % 360).round(0)
    else:
        # Fallback de emergencia
        print("🚨 Usando valores default (Sin estaciones válidas).")
        grid_df['tmp'] = 20; grid_df['rh'] = 40; grid_df['altitude'] = 2240
        grid_df['wsp'] = 0; grid_df['wdr'] = 0
    
    grid_df['station_numeric'] = -1 
    return grid_df

def interpolate_grid(model, grid_df):
    FEATURES = [
        'lat', 'lon', 'altitude', 'station_numeric', 
        'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 
        'tmp', 'rh', 'wsp', 'wdr'
    ]
    for col in FEATURES:
        if col not in grid_df.columns: grid_df[col] = 0
            
    predictions = model.predict(grid_df[FEATURES])
    grid_df[TARGET_POLLUTANT] = np.maximum(predictions, 0).round(1)
    
    # Exportamos TODAS las variables para visualización
    return grid_df[['lat', 'lon', 'altitude', 'tmp', 'rh', 'wsp', 'wdr', TARGET_POLLUTANT]]

def lambda_handler(event, context):
    print("🚀 Iniciando Lambda V9 (History + Timestamp)...")
    try:
        model = load_model_local()
        raw_stations = get_live_data()
        stations_df = process_stations_data(raw_stations)
        grid_df = prepare_grid_features(stations_df)
        result_grid = interpolate_grid(model, grid_df)
        
        json_output = result_grid.to_json(orient='records')
        
        # --- ESTRATEGIA DE GUARDADO ---
        cdmx_tz = ZoneInfo("America/Mexico_City")
        now = datetime.now(cdmx_tz)
        timestamp = now.strftime("%Y-%m-%d_%H-%M")
        
        # 1. Guardar HISTÓRICO (Para auditoría)
        history_key = f"live_grid/grid_{timestamp}.json"
        print(f"💾 Guardando Histórico: {history_key}")
        s3_client.put_object(
            Bucket=S3_BUCKET, Key=history_key, Body=json_output, ContentType='application/json'
        )
        
        # 2. Guardar LATEST (Para visualizador web)
        print(f"💾 Actualizando Latest: {S3_GRID_OUTPUT_KEY}")
        s3_client.put_object(
            Bucket=S3_BUCKET, Key=S3_GRID_OUTPUT_KEY, Body=json_output, ContentType='application/json'
        )

        return {'statusCode': 200, 'body': json.dumps(f'Guardado: {history_key}')}
    except Exception as e:
        print(f"❌ Error Fatal: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps(f"Error: {str(e)}")}
