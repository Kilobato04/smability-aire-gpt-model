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
    if stations_df.empty: return grid_df
    metrics = ['o3', 'pm10', 'pm25']
    for pol in metrics:
        real_col = f'{pol}_real'
        if real_col in stations_df.columns:
            valid_vals = pd.to_numeric(stations_df[real_col], errors='coerce').dropna()
            if not valid_vals.empty:
                real_avg = valid_vals.mean()
                model_avg = grid_df[pol].mean()
                bias = real_avg - model_avg
                print(f"📊 BIAS {pol.upper()}: Real={real_avg:.1f}, Modelo={model_avg:.1f}, Bias={bias:.1f}")
                grid_df[pol] = (grid_df[pol] + bias).clip(lower=0)
    
    # Inyección de nombres
    for _, st in stations_df.iterrows():
        dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
        idx = dist.idxmin()
        grid_df.at[idx, 'station'] = st['name']
    return grid_df

def inverse_distance_weighting(x, y, z, xi, yi):
    dist = np.sqrt((xi[:, None] - x[None, :])**2 + (yi[:, None] - y[None, :])**2)
    dist = np.maximum(dist, 1e-12)
    weights = 1.0 / (dist ** 2)
    return np.sum(weights * z[None, :], axis=1) / np.sum(weights, axis=1)

def load_models():
    models = {}
    for p in ['o3', 'pm10', 'pm25']:
        path = f"{BASE_PATH}/app/artifacts/model_{p}.json"
        if os.path.exists(path):
            m = xgb.XGBRegressor(); m.load_model(path); models[p] = m
            print(f"✅ Modelo {p} cargado.")
    return models

def lambda_handler(event, context):
    print("🚀 INICIANDO PREDICTOR MAESTRO V56.4...")
    try:
        models = load_models()
        
        # 1. API con Blindaje Extremo
        try:
            r = requests.get(SMABILITY_API_URL, timeout=15)
            stations_raw = r.json().get('stations') or []
            print(f"📡 CHECK API: {len(stations_raw)} estaciones recibidas.")
        except Exception as e:
            print(f"⚠️ Error API: {e}"); stations_raw = []

        parsed = []
        for s in stations_raw:
            if not s: continue
            # Extraer de forma segura incluso si faltan diccionarios completos
            p = s.get('pollutants') or {}
            m = s.get('meteorological') or {}
            
            def safe_get(d, k1, k2):
                tmp = d.get(k1)
                if isinstance(tmp, dict):
                    return tmp.get(k2, {}).get('value')
                return None

            parsed.append({
                'name': s.get('station_name', 'Unknown'),
                'lat': float(s['latitude']) if s.get('latitude') else None,
                'lon': float(s['longitude']) if s.get('longitude') else None,
                'o3_real': safe_get(p, 'o3', 'avg_1h'),
                'pm10_real': safe_get(p, 'pm10', 'avg_1h'),
                'pm25_real': safe_get(p, 'pm25', 'avg_1h'),
                'tmp': safe_get(m, 'temperature', 'avg_1h'),
                'rh': safe_get(m, 'relative_humidity', 'avg_1h'),
                'wsp': safe_get(m, 'wind_speed', 'avg_1h')
            })
        
        stations_df = pd.DataFrame(parsed).dropna(subset=['lat', 'lon'])
        
        # 2. Cargar Malla
        if not os.path.exists(STATIC_ADMIN_PATH):
            raise FileNotFoundError(f"No existe la malla en {STATIC_ADMIN_PATH}")
            
        with open(STATIC_ADMIN_PATH, 'r') as f:
            grid_df = pd.DataFrame(json.load(f))
        
        print(f"✅ Malla cargada: {len(grid_df)} puntos. Altitud Avg: {grid_df['altitude'].mean():.1f}")

        # 3. Features Temporales y Meteo
        tz = ZoneInfo("America/Mexico_City")
        now = datetime.now(tz)
        grid_df['hour_sin'] = np.sin(2 * np.pi * now.hour / 24)
        grid_df['hour_cos'] = np.cos(2 * np.pi * now.hour / 24)
        grid_df['month_sin'] = np.sin(2 * np.pi * now.month / 12)
        grid_df['month_cos'] = np.cos(2 * np.pi * now.month / 12)

        for feat, default in [('tmp', 20.0), ('rh', 40.0), ('wsp', 1.0)]:
            valid = stations_df.dropna(subset=[feat])
            if not valid.empty:
                grid_df[feat] = inverse_distance_weighting(valid['lat'].values, valid['lon'].values, valid[feat].values, grid_df['lat'].values, grid_df['lon'].values)
            else: grid_df[feat] = default

        # 4. Predicción y Calibración
        grid_df['station_numeric'] = -1
        feats = ['lat', 'lon', 'altitude', 'building_vol', 'station_numeric', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'tmp', 'rh', 'wsp']
        
        for p in ['o3', 'pm10', 'pm25']:
            if p in models:
                grid_df[p] = models[p].predict(grid_df[feats]).clip(0)
            else: grid_df[p] = 0.0

        grid_df = overwrite_with_real_data(grid_df, stations_df)

        # 5. IAS y Riesgo
        def calc_row(row):
            s = {'O3': get_ias_score(row['o3'], 'o3'), 'PM10': get_ias_score(row['pm10'], 'pm10'), 'PM2.5': get_ias_score(row['pm25'], 'pm25')}
            dom = max(s, key=s.get)
            return pd.Series([max(s.values()), dom])

        grid_df[['ias', 'dominant']] = grid_df.apply(calc_row, axis=1)
        grid_df['risk'] = grid_df['ias'].apply(get_risk_level)
        grid_df['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

        # 6. Guardado S3
        cols = ['timestamp', 'lat', 'lon', 'mun', 'edo', 'altitude', 'building_vol', 'tmp', 'rh', 'wsp', 'o3', 'pm10', 'pm25', 'ias', 'station', 'risk', 'dominant']
        output = grid_df[cols].replace({np.nan: None}).to_json(orient='records')
        
        print(f"📦 OUTPUT: {len(output)/1024:.1f} KB")
        s3_client.put_object(Bucket=S3_BUCKET, Key=S3_GRID_OUTPUT_KEY, Body=output, ContentType='application/json')
        
        return {'statusCode': 200, 'body': 'V56.4 OK'}
    except Exception as e:
        print(f"❌ ERROR: {e}"); return {'statusCode': 500, 'body': str(e)}
