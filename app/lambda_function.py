import json
import boto3
import pandas as pd
import numpy as np
import xgboost as xgb
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os

# --- 1. CONFIGURACIÓN Y RUTAS (ESTADO: ESTABLE) ---
BASE_PATH = os.environ.get('LAMBDA_TASK_ROOT', '/var/task')
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
S3_GRID_OUTPUT_KEY = os.environ.get('S3_GRID_OUTPUT_KEY', 'live_grid/latest_grid.json')

MODEL_PATH_O3 = f"{BASE_PATH}/app/artifacts/model_o3.json"
MODEL_PATH_PM10 = f"{BASE_PATH}/app/artifacts/model_pm10.json"
MODEL_PATH_PM25 = f"{BASE_PATH}/app/artifacts/model_pm25.json"
STATIC_ADMIN_PATH = f"{BASE_PATH}/app/geograficos/grid_admin_info.json"
SMABILITY_API_URL = "https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference,smaa"

s3_client = boto3.client('s3')

# --- 2. LÓGICA NORMATIVA NOM-172 (ESTADO: ESTABLE) ---
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

# --- 3. FUNCIONES DE CÁLCULO ESPACIAL (ESTADO: ESTABLE) ---
def inverse_distance_weighting(x, y, z, xi, yi):
    dist = np.sqrt((xi[:, None] - x[None, :])**2 + (yi[:, None] - y[None, :])**2)
    dist = np.maximum(dist, 1e-12)
    weights = 1.0 / (dist ** 2)
    return np.sum(weights * z[None, :], axis=1) / np.sum(weights, axis=1)

# --- 4. HANDLER PRINCIPAL (ESTADO: MONITORIZACIÓN V56.6) ---
def lambda_handler(event, context):
    print("🚀 INICIANDO PREDICTOR MAESTRO V56.6 - CONSOLIDADO")
    try:
        # A. Carga de Modelos
        models = {}
        for p in ['o3', 'pm10', 'pm25']:
            path = f"{BASE_PATH}/app/artifacts/model_{p}.json"
            if os.path.exists(path):
                m = xgb.XGBRegressor(); m.load_model(path); models[p] = m
                print(f"✅ Modelo {p} cargado correctamente.")
        
        # B. Consumo de API con Blindaje y Health Check
        # Propósito: Evitar 'NoneType' error si faltan bloques de meteorología o contaminantes.
        try:
            r = requests.get(SMABILITY_API_URL, timeout=15)
            stations_raw = r.json().get('stations', [])
        except Exception as e:
            print(f"⚠️ Error crítico consultando API: {e}")
            stations_raw = []

        counts = {"o3": 0, "pm10": 0, "pm25": 0, "tmp": 0, "rh": 0, "wsp": 0}
        parsed = []

        for s in stations_raw:
            # Blindaje estructural: Aseguramos que pol y met sean diccionarios siempre
            pol = s.get('pollutants') or {}
            met = s.get('meteorological') or {}
            
            # Función interna segura para extraer valores y contar salud del API
            def safe_extract(d, key, count_key):
                val = d.get(key, {}).get('avg_1h', {}).get('value') if isinstance(d.get(key), dict) else None
                if val is not None: counts[count_key] += 1
                return val

            parsed.append({
                'name': s.get('station_name', 'Unknown'),
                'lat': float(s.get('latitude')) if s.get('latitude') else None,
                'lon': float(s.get('longitude')) if s.get('longitude') else None,
                'o3_real': safe_extract(pol, 'o3', 'o3'),
                'pm10_real': safe_extract(pol, 'pm10', 'pm10'),
                'pm25_real': safe_extract(pol, 'pm25', 'pm25'),
                'tmp': safe_extract(met, 'temperature', 'tmp'),
                'rh': safe_extract(met, 'relative_humidity', 'rh'),
                'wsp': safe_extract(met, 'wind_speed', 'wsp')
            })
        
        print(f"📡 API MONITOR: {len(stations_raw)} recibidas. Salud Datos -> O3:{counts['o3']}, PM10:{counts['pm10']}, TMP:{counts['tmp']}")
        stations_df = pd.DataFrame(parsed).dropna(subset=['lat', 'lon'])

        # C. Carga de Malla y Datos Geográficos (ESTADO: BAJO TEST)
        with open(STATIC_ADMIN_PATH, 'r') as f:
            grid_df = pd.DataFrame(json.load(f))
        
        alt_mean = grid_df['altitude'].mean() if 'altitude' in grid_df else 0
        print(f"✅ MALLA CARGADA: {len(grid_df)} puntos. Altitud Promedio: {alt_mean:.1f}m")
        
        # D. Interpolación Meteorológica para Malla
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
            else: 
                grid_df[feat] = default

        # E. Predicción XGBoost y Calibración BIAS (ESTADO: BAJO TEST)
        grid_df['station_numeric'] = -1
        feats = ['lat', 'lon', 'altitude', 'building_vol', 'station_numeric', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'tmp', 'rh', 'wsp']
        
        for p in ['o3', 'pm10', 'pm25']:
            if p in models:
                grid_df[p] = models[p].predict(grid_df[feats]).clip(0)
                # Calibración de Bias solo si hay estaciones reales para ese contaminante
                v_real = stations_df[f'{p}_real'].dropna()
                if not v_real.empty:
                    bias = v_real.mean() - grid_df[p].mean()
                    grid_df[p] = (grid_df[p] + bias).clip(0)
                    print(f"⚖️ CALIBRACIÓN {p.upper()}: Bias aplicado = {bias:.2f}")
            else: 
                grid_df[p] = 0.0

        # F. Inyección de Marcadores y Nombres (ESTADO: ESTABLE)
        grid_df['station'] = None
        for _, st in stations_df.iterrows():
            dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
            idx = dist.idxmin()
            grid_df.at[idx, 'station'] = st['name']

        # G. Cálculo de IAS y Dominante (ESTADO: ESTABLE)
        def process_row_ias(row):
            scores = {
                'O3': get_ias_score(row['o3'], 'o3'), 
                'PM10': get_ias_score(row['pm10'], 'pm10'), 
                'PM2.5': get_ias_score(row['pm25'], 'pm25')
            }
            dom_pol = max(scores, key=scores.get)
            return pd.Series([max(scores.values()), dom_pol])

        grid_df[['ias', 'dominant']] = grid_df.apply(process_row_ias, axis=1)
        grid_df['risk'] = grid_df['ias'].apply(get_risk_level)
        grid_df['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

        # H. Guardado Final S3 (ESTADO: ESTABLE)
        cols = ['timestamp', 'lat', 'lon', 'mun', 'edo', 'altitude', 'building_vol', 'tmp', 'rh', 'wsp', 'o3', 'pm10', 'pm25', 'ias', 'station', 'risk', 'dominant']
        final_json = grid_df[cols].replace({np.nan: None}).to_json(orient='records')
        
        s3_client.put_object(Bucket=S3_BUCKET, Key=S3_GRID_OUTPUT_KEY, Body=final_json, ContentType='application/json')
        print(f"📦 ÉXITO: Archivo generado ({len(final_json)/1024:.2f} KB) y guardado en S3.")
        
        return {'statusCode': 200, 'body': 'Predictor V56.6 Finalizado'}

    except Exception as e:
        print(f"❌ ERROR FATAL: {e}")
        return {'statusCode': 500, 'body': str(e)}
