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
    if stations_df.empty: 
        print("⚠️ LOG: Sin estaciones para calibrar.")
        return grid_df
    
    for pol in ['o3', 'pm10', 'pm25']:
        real_col = f'{pol}_real'
        if real_col in stations_df.columns:
            valid_ref = stations_df[real_col].dropna()
            if not valid_ref.empty:
                real_avg = valid_ref.mean()
                model_avg = grid_df[pol].mean()
                bias = real_avg - model_avg
                print(f"📊 LOG BIAS: {pol.upper()} -> Real: {real_avg:.2f}, Modelo: {model_avg:.2f}, Bias: {bias:.2f}")
                grid_df[pol] = (grid_df[pol] + bias).clip(lower=0)

    grid_df['station'] = None
    for _, st in stations_df.iterrows():
        dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
        idx = dist.idxmin()
        grid_df.at[idx, 'station'] = st['name']
        for pol in ['o3', 'pm10', 'pm25']:
            val = st.get(f'{pol}_real')
            if val is not None and not np.isnan(val):
                grid_df.at[idx, pol] = val
    return grid_df

def inverse_distance_weighting(x, y, z, xi, yi, power=2):
    dist = np.sqrt((xi[:, None] - x[None, :])**2 + (yi[:, None] - y[None, :])**2)
    dist = np.maximum(dist, 1e-12)
    weights = 1.0 / (dist ** power)
    return np.sum(weights * z[None, :], axis=1) / np.sum(weights, axis=1)

def load_models():
    print("⬇️ LOG: Cargando modelos XGBoost...")
    models = {}
    for p in ['o3', 'pm10', 'pm25']:
        path = f"{BASE_PATH}/app/artifacts/model_{p}.json"
        if os.path.exists(path):
            try:
                m = xgb.XGBRegressor()
                m.load_model(path)
                models[p] = m
                print(f"✅ Modelo {p} cargado.")
            except Exception as e:
                print(f"❌ Error cargando {p}: {e}")
    return models

def prepare_grid_features(stations_df):
    # --- LOG ESTRATÉGICO 1 ---
    with open(STATIC_ADMIN_PATH, 'r') as f:
        grid_df = pd.DataFrame(json.load(f))
    
    alt_mean = grid_df['altitude'].mean() if 'altitude' in grid_df else 0
    print(f"📡 CHECK CAPAS: Malla cargada ({len(grid_df)} pts). Altitud Avg={alt_mean:.2f}m")
    
    tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(tz)
    grid_df['hour_sin'] = np.sin(2 * np.pi * now.hour / 24)
    grid_df['hour_cos'] = np.cos(2 * np.pi * now.hour / 24)
    grid_df['month_sin'] = np.sin(2 * np.pi * now.month / 12)
    grid_df['month_cos'] = np.cos(2 * np.pi * now.month / 12)
    
    for feat, default in [('tmp', 20.0), ('rh', 40.0), ('wsp', 1.0), ('wdr', 90.0)]:
        try:
            if feat in stations_df.columns and stations_df[feat].notna().any():
                valid = stations_df.dropna(subset=[feat, 'lat', 'lon'])
                if not valid.empty:
                    grid_df[feat] = inverse_distance_weighting(valid['lat'].values, valid['lon'].values, valid[feat].values, grid_df['lat'].values, grid_df['lon'].values)
                    continue
            grid_df[feat] = default
        except Exception as e:
            print(f"⚠️ Error IDW {feat}: {e}"); grid_df[feat] = default
            
    grid_df['station_numeric'] = -1
    return grid_df

def lambda_handler(event, context):
    print("🚀 INICIANDO PREDICTOR MAESTRO V56.3...")
    try:
        models = load_models()
        
        try:
            r = requests.get(SMABILITY_API_URL, timeout=15)
            resp_raw = r.json().get('stations', [])
            print(f"📡 CHECK API: {len(resp_raw)} estaciones recibidas.")
        except Exception as e:
            print(f"⚠️ LOG: Error API: {e}"); resp_raw = []

        parsed = []
        for s in resp_raw:
            met = s.get('meteorological') or {}
            pol = s.get('pollutants') or {}
            
            def get_deep(d, k1, k2):
                return d.get(k1, {}).get(k2, {}).get('value') if d.get(k1) else None

            parsed.append({
                'name': s.get('station_name', 'Unknown'),
                'lat': float(s['latitude']) if s.get('latitude') else None,
                'lon': float(s['longitude']) if s.get('longitude') else None,
                'o3_real': get_deep(pol, 'o3', 'avg_1h'),
                'pm10_real': get_deep(pol, 'pm10', 'avg_1h'),
                'pm25_real': get_deep(pol, 'pm25', 'avg_1h'),
                'tmp': get_deep(met, 'temperature', 'avg_1h'),
                'rh': get_deep(met, 'relative_humidity', 'avg_1h'),
                'wsp': get_deep(met, 'wind_speed', 'avg_1h'),
                'wdr': get_deep(met, 'wind_direction', 'avg_1h')
            })
        
        stations_df = pd.DataFrame(parsed).dropna(subset=['lat', 'lon'])
        grid_df = prepare_grid_features(stations_df)
        
        FEATURES = ['lat', 'lon', 'altitude', 'building_vol', 'station_numeric', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'tmp', 'rh', 'wsp']
        for p in ['o3', 'pm10', 'pm25']:
            if p in models:
                grid_df[p] = models[p].predict(grid_df[FEATURES]).clip(0)
            else: grid_df[p] = 0.0

        grid_df = overwrite_with_real_data(grid_df, stations_df)

        def get_metrics(row):
            scores = {'O3': get_ias_score(row['o3'], 'o3'), 'PM10': get_ias_score(row['pm10'], 'pm10'), 'PM2.5': get_ias_score(row['pm25'], 'pm25')}
            dom = max(scores, key=scores.get)
            return pd.Series([max(scores.values()), dom])

        grid_df[['ias', 'dominant']] = grid_df.apply(get_metrics, axis=1)
        grid_df['risk'] = grid_df['ias'].apply(get_risk_level)
        
        tz = ZoneInfo("America/Mexico_City")
        grid_df['timestamp'] = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        cols = ['timestamp', 'lat', 'lon', 'mun', 'edo', 'altitude', 'building_vol', 'tmp', 'rh', 'wsp', 'wdr', 'o3', 'pm10', 'pm25', 'ias', 'station', 'risk', 'dominant']
        
        final_df = grid_df[cols].replace({np.nan: None})
        final_json = final_df.to_json(orient='records')
        
        print(f"📦 CHECK OUTPUT: Tamaño={len(final_json)/1024:.2f} KB")
        s3_client.put_object(Bucket=S3_BUCKET, Key=S3_GRID_OUTPUT_KEY, Body=final_json, ContentType='application/json')
        
        return {'statusCode': 200, 'body': 'V56.3 Success'}
    except Exception as e:
        print(f"❌ ERROR FATAL: {str(e)}"); return {'statusCode': 500, 'body': str(e)}
