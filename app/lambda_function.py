import json
import boto3
import pandas as pd
import numpy as np
import xgboost as xgb
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os

# --- 1. CONFIGURACIÓN Y RUTAS ---
BASE_PATH = os.environ.get('LAMBDA_TASK_ROOT', '/var/task')
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
S3_GRID_OUTPUT_KEY = os.environ.get('S3_GRID_OUTPUT_KEY', 'live_grid/latest_grid.json')
# --- CONFIGURACIÓN S3 ---
S3_BUCKET = "smability-data-lake"
# Los modelos ahora viven en: models/model_xxx.json
MODEL_S3_PREFIX = "models/"

MODEL_PATH_O3 = f"{BASE_PATH}/app/artifacts/model_o3.json"
MODEL_PATH_PM10 = f"{BASE_PATH}/app/artifacts/model_pm10.json"
MODEL_PATH_PM25 = f"{BASE_PATH}/app/artifacts/model_pm25.json"
STATIC_ADMIN_PATH = f"{BASE_PATH}/app/geograficos/grid_admin_info.json"
SMABILITY_API_URL = "https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference,smaa"

s3_client = boto3.client('s3')

# --- 2. LÓGICA NORMATIVA NOM-172-2024 ---
BPS_O3 = [(0,58,0,50), (59,92,51,100), (93,135,101,150), (136,175,151,200), (176,240,201,300)]
BPS_PM10 = [(0,45,0,50), (46,60,51,100), (61,132,101,150), (133,213,151,200), (214,354,201,300)]
BPS_PM25 = [(0,25,0,50), (26,45,51,100), (46,79,101,150), (80,147,151,200), (148,250,201,300)]

def get_ias_score(c, pollutant):
    try:
        c = float(c)
        bps = BPS_O3 if pollutant == 'o3' else (BPS_PM10 if pollutant == 'pm10' else BPS_PM25)
        for (c_lo, c_hi, i_lo, i_hi) in bps:
            if c <= c_hi: return i_lo + ((c - c_lo) / (c_hi - c_lo)) * (i_hi - i_lo)
        return bps[-1][3]
    except: return 0

def get_risk_level(ias):
    if ias <= 50: return "Bajo"
    if ias <= 100: return "Moderado"
    if ias <= 150: return "Alto"
    if ias <= 200: return "Muy Alto"
    return "Extremadamente Alto"

# --- 3. FUNCIONES DE CARGA Y PROCESAMIENTO ---
def load_models():
    """Descarga modelos desde S3 y los carga en XGBoost"""
    models = {}
    pollutants = ['o3', 'pm10', 'pm25']
    
    for p in pollutants:
        s3_key = f"{MODEL_S3_PREFIX}model_{p}.json"
        local_path = f"/tmp/model_{p}.json"
        
        try:
            print(f"descargando de S3: {s3_key}...")
            s3_client.download_file(S3_BUCKET, s3_key, local_path)
            
            m = xgb.XGBRegressor()
            m.load_model(local_path)
            models[p] = m
            print(f"✅ Modelo {p} cargado desde S3.")
        except Exception as e:
            print(f"⚠️ No se pudo cargar el modelo {p} desde S3: {e}")
            
    return models

def inverse_distance_weighting(x, y, z, xi, yi):
    """Interpolación espacial IDW"""
    dist = np.sqrt((xi[:, None] - x[None, :])**2 + (yi[:, None] - y[None, :])**2)
    dist = np.maximum(dist, 1e-12)
    weights = 1.0 / (dist ** 2)
    return np.sum(weights * z[None, :], axis=1) / np.sum(weights, axis=1)

def prepare_grid_features(stations_df):
    """Carga malla valle (con 'elevation') y la une con datos administrativos"""
    # 1. Cargar Malla Valle (GeoJSON)
    # Nota: Asegúrate de que el nombre del archivo en tu repo sea el correcto
    MALLA_PATH = f"{BASE_PATH}/app/geograficos/malla_valle_mexico_final.geojson"
    
    with open(MALLA_PATH, 'r') as f:
        malla_data = json.load(f)
    
    # Extraer datos del GeoJSON
    malla_list = []
    for feature in malla_data['features']:
        coords = feature['geometry']['coordinates']
        props = feature['properties']
        malla_list.append({
            'lon': coords[0],
            'lat': coords[1],
            'altitude': props.get('elevation', 2240) # Aquí corregimos el nombre de la columna
        })
    
    grid_df = pd.DataFrame(malla_list)
    
    # 2. Cargar Capa de Edificios / Administrativa
    # (Hacemos un merge por cercanía o si las coordenadas coinciden exactamente)
    with open(f"{BASE_PATH}/app/geograficos/capa_edificios_v2.json", 'r') as f:
        edificios_df = pd.DataFrame(json.load(f))
    
    # Unimos las capas (suponiendo que las coordenadas son las mismas en la malla)
    grid_df = pd.merge(grid_df, edificios_df[['lat', 'lon', 'building_vol']], on=['lat', 'lon'], how='left').fillna(0)
    
    # 3. Variables Temporales e Interpolación Meteo
    tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(tz)
    grid_df['hour_sin'] = np.sin(2 * np.pi * now.hour / 24)
    grid_df['hour_cos'] = np.cos(2 * np.pi * now.hour / 24)
    grid_df['month_sin'] = np.sin(2 * np.pi * now.month / 12)
    grid_df['month_cos'] = np.cos(2 * np.pi * now.month / 12)

    for feat, default in [('tmp', 20.0), ('rh', 40.0), ('wsp', 1.0)]:
        valid = stations_df.dropna(subset=[feat])
        if not valid.empty:
            grid_df[feat] = inverse_distance_weighting(
                valid['lat'].values, valid['lon'].values, valid[feat].values, 
                grid_df['lat'].values, grid_df['lon'].values
            )
        else: grid_df[feat] = default
    
    grid_df['station_numeric'] = -1
    return grid_df

# --- 4. HANDLER PRINCIPAL ---
def lambda_handler(event, context):
    VERSION = "V56.9" 
    print(f"🚀 INICIANDO PREDICTOR MAESTRO {VERSION} - ESTABILIZACIÓN FINAL")
    
    try:
        models = load_models()
        
        # Ingesta de API con Blindaje
        stations_raw = []
        try:
            r = requests.get(SMABILITY_API_URL, timeout=15)
            if r.status_code == 200:
                res = r.json()
                stations_raw = res.get('stations') if isinstance(res, dict) else []
        except Exception as e:
            print(f"⚠️ API Error: {e}")

        counts = {"o3": 0, "pm10": 0, "pm25": 0, "tmp": 0, "rh": 0, "wsp": 0}
        parsed = []

        for s in stations_raw:
            if not isinstance(s, dict): continue
            pol = s.get('pollutants') or {}
            met = s.get('meteorological') or {}
            
            def safe_val(d, key, c_key):
                obj = d.get(key)
                if isinstance(obj, dict):
                    inner = obj.get('avg_1h')
                    if isinstance(inner, dict):
                        val = inner.get('value')
                        if val is not None:
                            counts[c_key] += 1
                            return val
                return None

            parsed.append({
                'name': s.get('station_name', 'Unknown'),
                'lat': float(s.get('latitude')) if s.get('latitude') else None,
                'lon': float(s.get('longitude')) if s.get('longitude') else None,
                'o3_real': safe_val(pol, 'o3', 'o3'),
                'pm10_real': safe_val(pol, 'pm10', 'pm10'),
                'pm25_real': safe_val(pol, 'pm25', 'pm25'),
                'tmp': safe_val(met, 'temperature', 'tmp'),
                'rh': safe_val(met, 'relative_humidity', 'rh'),
                'wsp': safe_val(met, 'wind_speed', 'wsp')
            })
        
        print(f"📊 SALUD API: Recibidas {len(stations_raw)} | O3:{counts['o3']} | TMP:{counts['tmp']}")
        stations_df = pd.DataFrame(parsed).dropna(subset=['lat', 'lon'])

        # Procesamiento de Malla y Predicción
        grid_df = prepare_grid_features(stations_df)
        
        feats = ['lat', 'lon', 'altitude', 'building_vol', 'station_numeric', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'tmp', 'rh', 'wsp']
        for p in ['o3', 'pm10', 'pm25']:
            if p in models:
                grid_df[p] = models[p].predict(grid_df[feats]).clip(0)
                # Calibración BIAS
                v_real = stations_df[f'{p}_real'].dropna()
                if not v_real.empty:
                    bias = v_real.mean() - grid_df[p].mean()
                    grid_df[p] = (grid_df[p] + bias).clip(0)
                    print(f"⚖️ BIAS {p.upper()}: {bias:.2f}")
            else: grid_df[p] = 0.0

        # Marcadores y Métricas Finales
        grid_df['station'] = None
        for _, st in stations_df.iterrows():
            dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
            idx = dist.idxmin()
            grid_df.at[idx, 'station'] = st['name']

        def calc_ias_row(row):
            s = {'O3': get_ias_score(row['o3'], 'o3'), 'PM10': get_ias_score(row['pm10'], 'pm10'), 'PM2.5': get_ias_score(row['pm25'], 'pm25')}
            dom = max(s, key=s.get)
            return pd.Series([max(s.values()), dom])

        grid_df[['ias', 'dominant']] = grid_df.apply(calc_ias_row, axis=1)
        grid_df['risk'] = grid_df['ias'].apply(get_risk_level)
        
        now_mx = datetime.now(ZoneInfo("America/Mexico_City"))
        grid_df['timestamp'] = now_mx.strftime("%Y-%m-%d %H:%M:%S")

        cols = ['timestamp', 'lat', 'lon', 'mun', 'edo', 'altitude', 'building_vol', 'tmp', 'rh', 'wsp', 'o3', 'pm10', 'pm25', 'ias', 'station', 'risk', 'dominant']
        final_json = grid_df[cols].replace({np.nan: None}).to_json(orient='records')
        
        s3_client.put_object(Bucket=S3_BUCKET, Key=S3_GRID_OUTPUT_KEY, Body=final_json, ContentType='application/json')
        print(f"📦 SUCCESS V56.9: {len(final_json)/1024:.2f} KB en S3.")
        
        return {'statusCode': 200, 'body': f'Predictor {VERSION} OK'}

    except Exception as e:
        print(f"❌ ERROR FATAL: {e}")
        return {'statusCode': 500, 'body': str(e)}
