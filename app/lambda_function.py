import json
import boto3
import pandas as pd
import numpy as np
import xgboost as xgb
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os

# --- CONFIGURACIÓN DE RUTAS ---
BASE_PATH = os.environ.get('LAMBDA_TASK_ROOT', '/var/task')
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
S3_GRID_OUTPUT_KEY = os.environ.get('S3_GRID_OUTPUT_KEY', 'live_grid/latest_grid.json')

MODEL_PATH_O3 = f"{BASE_PATH}/app/artifacts/model_o3.json"
MODEL_PATH_PM10 = f"{BASE_PATH}/app/artifacts/model_pm10.json"
MODEL_PATH_PM25 = f"{BASE_PATH}/app/artifacts/model_pm25.json"
STATIC_ADMIN_PATH = f"{BASE_PATH}/app/geograficos/grid_admin_info.json"
SMABILITY_API_URL = "https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference,smaa"

s3_client = boto3.client('s3')

# --- TABLAS NOM-172-2024 ---
BPS_O3 = [(0,58,0,50), (59,92,51,100), (93,135,101,150), (136,175,151,200), (176,240,201,300)]
BPS_PM10 = [(0,45,0,50), (46,60,51,100), (61,132,101,150), (133,213,151,200), (214,354,201,300)]
BPS_PM25 = [(0,25,0,50), (26,45,51,100), (46,79,101,150), (80,147,151,200), (148,250,201,300)]

def get_ias_score(c, pollutant):
    if pollutant == 'o3': breakpoints = BPS_O3
    elif pollutant == 'pm10': breakpoints = BPS_PM10
    else: breakpoints = BPS_PM25
    try:
        c = float(c)
        for (c_lo, c_hi, i_lo, i_hi) in breakpoints:
            if c <= c_hi: return i_lo + ((c - c_lo) / (c_hi - c_lo)) * (i_hi - i_lo)
        last = breakpoints[-1]
        return last[2] + ((c - last[0]) / (last[1] - last[0])) * (last[3] - last[2])
    except: return 0

def get_risk_level(ias):
    if ias <= 50: return "Bajo"
    elif ias <= 100: return "Moderado"
    elif ias <= 150: return "Alto"
    elif ias <= 200: return "Muy Alto"
    else: return "Extremadamente Alto"

def overwrite_with_real_data(grid_df, stations_df):
    """Calibración de Bias e Inyección de nombres de estación"""
    if stations_df.empty: return grid_df
    
    # 1. BIAS CORRECTION (O3, PM10, PM25)
    for pol in ['o3', 'pm10', 'pm25']:
        real_col = f'{pol}_real'
        if real_col in stations_df.columns:
            real_avg = stations_df[real_col].mean()
            model_avg = grid_df[pol].mean()
            bias = real_avg - model_avg
            print(f"📊 Bias {pol.upper()}: {bias:.2f}")
            grid_df[pol] = (grid_df[pol] + bias).clip(lower=0)

    # 2. INYECCIÓN DE ESTACIONES (MARKERS)
    grid_df['station'] = None
    for _, st in stations_df.iterrows():
        dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
        idx = dist.idxmin()
        grid_df.at[idx, 'station'] = st['name']
        for pol in ['o3', 'pm10', 'pm25']:
            if not np.isnan(st[f'{pol}_real']):
                grid_df.at[idx, pol] = st[f'{pol}_real']
    return grid_df

def inverse_distance_weighting(x, y, z, xi, yi, power=2):
    dist = np.sqrt((xi[:, None] - x[None, :])**2 + (yi[:, None] - y[None, :])**2)
    dist = np.maximum(dist, 1e-12)
    weights = 1.0 / (dist ** power)
    return np.sum(weights * z[None, :], axis=1) / np.sum(weights, axis=1)

def load_models():
    models = {}
    for p in ['o3', 'pm10', 'pm25']:
        path = f"{BASE_PATH}/app/artifacts/model_{p}.json"
        if os.path.exists(path):
            m = xgb.XGBRegressor(); m.load_model(path); models[p] = m
    return models

def prepare_grid_features(stations_df):
    with open(STATIC_ADMIN_PATH, 'r') as f:
        grid_df = pd.DataFrame(json.load(f))
    
    cdmx_tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(cdmx_tz)
    grid_df['hour_sin'] = np.sin(2 * np.pi * now.hour / 24)
    grid_df['hour_cos'] = np.cos(2 * np.pi * now.hour / 24)
    grid_df['month_sin'] = np.sin(2 * np.pi * now.month / 12)
    grid_df['month_cos'] = np.cos(2 * np.pi * now.month / 12)
    
    # Interpolación Meteo
    for feat, default in [('tmp', 20.0), ('rh', 40.0), ('wsp', 1.0), ('wdr', 90.0)]:
        valid = stations_df.dropna(subset=[feat])
        if not valid.empty:
            grid_df[feat] = inverse_distance_weighting(valid['lat'].values, valid['lon'].values, valid[feat].values, grid_df['lat'].values, grid_df['lon'].values)
        else: grid_df[feat] = default
    
    grid_df['station_numeric'] = -1
    return grid_df

def lambda_handler(event, context):
    print("🚀 Iniciando Predictor Maestro V56...")
    try:
        models = load_models()
        
        # Obtener y procesar datos reales
        resp = requests.get(SMABILITY_API_URL, timeout=15).json().get('stations', [])
        parsed = []
        for s in resp:
            met = s.get('meteorological', {}); pol = s.get('pollutants', {})
            parsed.append({
                'name': s.get('station_name'), 'lat': float(s['latitude']), 'lon': float(s['longitude']),
                'o3_real': pol.get('o3', {}).get('avg_1h', {}).get('value'),
                'pm10_real': pol.get('pm10', {}).get('avg_1h', {}).get('value'),
                'pm25_real': pol.get('pm25', {}).get('avg_1h', {}).get('value'),
                'tmp': met.get('temperature', {}).get('avg_1h', {}).get('value'),
                'rh': met.get('relative_humidity', {}).get('avg_1h', {}).get('value'),
                'wsp': met.get('wind_speed', {}).get('avg_1h', {}).get('value'),
                'wdr': met.get('wind_direction', {}).get('avg_1h', {}).get('value')
            })
        stations_df = pd.DataFrame(parsed)

        # Malla y Predicción
        grid_df = prepare_grid_features(stations_df)
        FEATURES = ['lat', 'lon', 'altitude', 'building_vol', 'station_numeric', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'tmp', 'rh', 'wsp']
        
        for p in ['o3', 'pm10', 'pm25']:
            if p in models:
                grid_df[p] = models[p].predict(grid_df[FEATURES]).clip(0)
            else: grid_df[p] = 0.0

        # Calibración y marcadores
        grid_df = overwrite_with_real_data(grid_df, stations_df)

        # Dominante e IAS
        def get_metrics(row):
            s_o3 = get_ias_score(row['o3'], 'o3')
            s_p10 = get_ias_score(row['pm10'], 'pm10')
            s_p25 = get_ias_score(row['pm25'], 'pm25')
            scores = {'O3': s_o3, 'PM10': s_p10, 'PM2.5': s_p25}
            dom = max(scores, key=scores.get)
            return pd.Series([max(scores.values()), dom])

        grid_df[['ias', 'dominant']] = grid_df.apply(get_metrics, axis=1)
        grid_df['risk'] = grid_df['ias'].apply(get_risk_level)
        
        # Timestamp y S3
        tz = ZoneInfo("America/Mexico_City")
        grid_df['timestamp'] = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        
        cols = ['timestamp', 'lat', 'lon', 'mun', 'edo', 'altitude', 'building_vol', 'tmp', 'rh', 'wsp', 'wdr', 'o3', 'pm10', 'pm25', 'ias', 'station', 'risk', 'dominant']
        final_json = grid_df[cols].replace({np.nan: None}).to_json(orient='records')
        
        s3_client.put_object(Bucket=S3_BUCKET, Key=S3_GRID_OUTPUT_KEY, Body=final_json, ContentType='application/json')
        
        return {'statusCode': 200, 'body': 'V56 Success'}
    except Exception as e:
        print(f"❌ Error: {str(e)}"); return {'statusCode': 500, 'body': str(e)}
