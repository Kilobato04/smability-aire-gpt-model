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
    """Calibración de Bias e Inyección de nombres de estación para el mapa"""
    if stations_df.empty: 
        print("⚠️ LOG: Sin estaciones para calibrar. Saltando Bias Correction.")
        return grid_df
    
    # 1. BIAS CORRECTION (O3, PM10, PM25)
    for pol in ['o3', 'pm10', 'pm25']:
        real_col = f'{pol}_real'
        if real_col in stations_df.columns:
            valid_ref = stations_df[real_col].dropna()
            if not valid_ref.empty:
                real_avg = valid_ref.mean()
                model_avg = grid_df[pol].mean()
                bias = real_avg - model_avg
                print(f"📊 LOG BIAS: {pol.upper()} -> Real Avg: {real_avg:.2f}, Model Avg: {model_avg:.2f}, Bias: {bias:.2f}")
                grid_df[pol] = (grid_df[pol] + bias).clip(lower=0)

    # 2. INYECCIÓN DE ESTACIONES (Para iconos en Front-end)
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
    print("⬇️ LOG: Cargando modelos XGBoost desde artifacts...")
    models = {}
    for p in ['o3', 'pm10', 'pm25']:
        path = f"{BASE_PATH}/app/artifacts/model_{p}.json"
        if os.path.exists(path):
            m = xgb.XGBRegressor(); m.load_model(path); models[p] = m
        else:
            print(f"⚠️ LOG: Modelo {p} no encontrado en {path}")
    return models

def prepare_grid_features(stations_df):
    with open(STATIC_ADMIN_PATH, 'r') as f:
        grid_df = pd.DataFrame(json.load(f))
    
    # --- LOG ESTRATÉGICO 1: Verificación de Capas ---
    alt_mean = grid_df['altitude'].mean() if 'altitude' in grid_df else 0
    bld_mean = grid_df['building_vol'].mean() if 'building_vol' in grid_df else 0
    print(f"📡 CHECK CAPAS: Malla de {len(grid_df)} pts. Altitud Promedio={alt_mean:.2f}m, Edificios Avg={bld_mean:.2f}")
    
    cdmx_tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(cdmx_tz)
    grid_df['hour_sin'] = np.sin(2 * np.pi * now.hour / 24)
    grid_df['hour_cos'] = np.cos(2 * np.pi * now.hour / 24)
    grid_df['month_sin'] = np.sin(2 * np.pi * now.month / 12)
    grid_df['month_cos'] = np.cos(2 * np.pi * now.month / 12)
    
    # Interpolación Meteo para la Malla
    for feat, default in [('tmp', 20.0), ('rh', 40.0), ('wsp', 1.0), ('wdr', 90.0)]:
        valid = stations_df.dropna(subset=[feat])
        if not valid.empty:
            grid_df[feat] = inverse_distance_weighting(valid['lat'].values, valid['lon'].values, valid[feat].values, grid_df['lat'].values, grid_df['lon'].values)
        else: 
            grid_df[feat] = default
    
    grid_df['station_numeric'] = -1
    return grid_df

def lambda_handler(event, context):
    print("🚀 INICIANDO PREDICTOR MAESTRO V56...")
    try:
        # Cargar Modelos
        models = load_models()
        
        # Obtener y procesar datos reales del API
        try:
            resp_raw = requests.get(SMABILITY_API_URL, timeout=15).json().get('stations', [])
            # --- LOG ESTRATÉGICO 2: Verificación de API ---
            print(f"📡 CHECK API: Se recibieron {len(resp_raw)} estaciones de referencia.")
        except Exception as api_err:
            print(f"⚠️ LOG: Fallo en API de referencia: {api_err}")
            resp_raw = []

        parsed = []
        for s in resp_raw:
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

        # Preparar Malla
        grid_df = prepare_grid_features(stations_df)
        
        # Predicción Base (Modelos XGBoost)
        FEATURES = ['lat', 'lon', 'altitude', 'building_vol', 'station_numeric', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'tmp', 'rh', 'wsp']
        for p in ['o3', 'pm10', 'pm25']:
            if p in models:
                grid_df[p] = models[p].predict(grid_df[FEATURES]).clip(0)
            else: 
                grid_df[p] = 0.0

        # Calibración de Bias e Inyección de Realidad
        grid_df = overwrite_with_real_data(grid_df, stations_df)

        # Cálculo de IAS y Dominante (NOM-172)
        def get_metrics(row):
            s_o3 = get_ias_score(row['o3'], 'o3')
            s_p10 = get_ias_score(row['pm10'], 'pm10')
            s_p25 = get_ias_score(row['pm25'], 'pm25')
            scores = {'O3': s_o3, 'PM10': s_p10, 'PM2.5': s_p25}
            dom = max(scores, key=scores.get)
            return pd.Series([max(scores.values()), dom])

        grid_df[['ias', 'dominant']] = grid_df.apply(get_metrics, axis=1)
        grid_df['risk'] = grid_df['ias'].apply(get_risk_level)
        
        # Timestamp final
        tz = ZoneInfo("America/Mexico_City")
        grid_df['timestamp'] = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        
        # Selección de columnas finales para el Front-end
        cols = ['timestamp', 'lat', 'lon', 'mun', 'edo', 'altitude', 'building_vol', 
                'tmp', 'rh', 'wsp', 'wdr', 'o3', 'pm10', 'pm25', 'ias', 'station', 'risk', 'dominant']
        
        final_df = grid_df[cols].replace({np.nan: None})
        final_json = final_df.to_json(orient='records')
        
        # --- LOG ESTRATÉGICO 3: Salud del Output ---
        json_kb = len(final_json.encode('utf-8')) / 1024
        print(f"📦 CHECK OUTPUT: JSON generado con {len(final_df)} puntos. Tamaño estimado: {json_kb:.2f} KB")
        
        # Guardar en S3
        s3_client.put_object(
            Bucket=S3_BUCKET, 
            Key=S3_GRID_OUTPUT_KEY, 
            Body=final_json, 
            ContentType='application/json'
        )
        
        print("✅ PROCESO COMPLETADO EXITOSAMENTE.")
        return {'statusCode': 200, 'body': 'Predictor Maestro V56 OK'}

    except Exception as e:
        print(f"❌ ERROR FATAL: {str(e)}")
        return {'statusCode': 500, 'body': str(e)}
