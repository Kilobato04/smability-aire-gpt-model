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
    """Carga malla valle, edificios y datos administrativos para una fusión total"""
    # 1. Cargar Malla Valle (Geometría y Elevación)
    MALLA_PATH = f"{BASE_PATH}/app/geograficos/malla_valle_mexico_final.geojson"
    with open(MALLA_PATH, 'r') as f:
        malla_data = json.load(f)
    
    malla_list = []
    for feature in malla_data['features']:
        coords = feature['geometry']['coordinates']
        malla_list.append({
            'lon': round(coords[0], 5),
            'lat': round(coords[1], 5),
            'altitude': feature['properties'].get('elevation', 2240)
        })
    grid_df = pd.DataFrame(malla_list)
    
    # 2. Cargar Capa de Edificios
    with open(f"{BASE_PATH}/app/geograficos/capa_edificios_v2.json", 'r') as f:
        edificios_df = pd.DataFrame(json.load(f))
        edificios_df['lat'] = edificios_df['lat'].round(5)
        edificios_df['lon'] = edificios_df['lon'].round(5)
    
    # 3. Cargar Capa Administrativa (Para MUN y EDO)
    # Usamos el archivo que mencionaste que tiene los nombres políticos
    with open(f"{BASE_PATH}/app/geograficos/grid_admin_info.json", 'r') as f:
        admin_df = pd.DataFrame(json.load(f))
        admin_df['lat'] = admin_df['lat'].round(5)
        admin_df['lon'] = admin_df['lon'].round(5)

    # --- FUSIÓN MAESTRA ---
    # Unimos elevación con edificios
    grid_df = pd.merge(grid_df, edificios_df[['lat', 'lon', 'building_vol']], on=['lat', 'lon'], how='left')
    
    # Unimos con datos administrativos (mun, edo)
    grid_df = pd.merge(grid_df, admin_df[['lat', 'lon', 'mun', 'edo']], on=['lat', 'lon'], how='left')
    
    # Limpieza: Llenar nulos en caso de que alguna coordenada no coincida exactamente
    grid_df['building_vol'] = grid_df['building_vol'].fillna(0)
    grid_df['mun'] = grid_df['mun'].fillna("Valle de México")
    grid_df['edo'] = grid_df['edo'].fillna("Edomex/CDMX")

    # 4. Variables Temporales e Interpolación
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
        
# --- INICIO DEL REEMPLAZO (SECCIÓN E y F) ---
        grid_df['station_numeric'] = -1
        feats = ['lat', 'lon', 'altitude', 'building_vol', 'station_numeric', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'tmp', 'rh', 'wsp']
        
        for p in ['o3', 'pm10', 'pm25']:
            if p in models:
                # 1. Predicción base de la IA
                grid_df[p] = models[p].predict(grid_df[feats]).clip(0)
                
                # 2. Preparar datos para la Tabla de Auditoría
                real_col = f'{p}_real'
                v_real_all = stations_df[['name', 'lat', 'lon', real_col]].copy()
                
                # Comparativa: ¿Qué dijo la IA en la ubicación de la estación?
                st_preds = []
                for _, st in v_real_all.iterrows():
                    dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
                    idx_closest = dist.idxmin()
                    st_preds.append(grid_df.at[idx_closest, p])
                v_real_all['raw_ai'] = st_preds
                
                # 3. Cálculo del Bias
                v_valid = v_real_all.dropna(subset=[real_col])
                bias = 0
                if not v_valid.empty:
                    bias = v_valid[real_col].mean() - v_valid['raw_ai'].mean()
                    grid_df[p] = (grid_df[p] + bias).clip(0)
                
                # 4. REPORTE VISUAL EN LOGS (TABLA)
                unit = "ppb" if p == "o3" else "µg/m³"
                print(f"\n📊 TABLA DE CALIBRACIÓN: {p.upper()} (Bias: {bias:+.2f} {unit})")
                header = f"{'Estación':<25} | {'Raw AI':<10} | {'Real':<10} | {'Bias':<10} | {'Final':<10}"
                print("-" * len(header))
                print(header)
                print("-" * len(header))
                
                for _, row in v_real_all.iterrows():
                    real_val = f"{row[real_col]:.2f}" if pd.notnull(row[real_col]) else "N/A"
                    # Si hay dato real, el final es el real. Si no, es AI + Bias.
                    final_val = row[real_col] if pd.notnull(row[real_col]) else (row['raw_ai'] + bias)
                    print(f"{row['name'][:24]:<25} | {row['raw_ai']:<10.2f} | {real_val:<10} | {bias:<+10.2f} | {max(0, final_val):<10.2f}")
                print("-" * len(header))
            else:
                grid_df[p] = 0.0

        # F. Marcadores y Sobrescritura de Estaciones (Garantiza error 0 en sensores)
        grid_df['station'] = None
        for _, st in stations_df.iterrows():
            dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
            idx = dist.idxmin()
            grid_df.at[idx, 'station'] = st['name']
            for p in ['o3', 'pm10', 'pm25']:
                real_val = st.get(f'{p}_real')
                if pd.notnull(real_val):
                    grid_df.at[idx, p] = real_val
# --- FIN DEL REEMPLAZO ---

        # G. Cálculo de IAS y Dominante (NOM-172-2024)
        def calc_ias_row(row):
            scores = {
                'O3': get_ias_score(row['o3'], 'o3'), 
                'PM10': get_ias_score(row['pm10'], 'pm10'), 
                'PM2.5': get_ias_score(row['pm25'], 'pm25')
            }
            dom_pol = max(scores, key=scores.get)
            return pd.Series([max(scores.values()), dom_pol])

        grid_df[['ias', 'dominant']] = grid_df.apply(calc_ias_row, axis=1)
        grid_df['risk'] = grid_df['ias'].apply(get_risk_level)
        
        now_mx = datetime.now(ZoneInfo("America/Mexico_City"))
        grid_df['timestamp'] = now_mx.strftime("%Y-%m-%d %H:%M:%S")

        # H. Exportación Final a S3
        cols = ['timestamp', 'lat', 'lon', 'mun', 'edo', 'altitude', 'building_vol', 
                'tmp', 'rh', 'wsp', 'o3', 'pm10', 'pm25', 'ias', 'station', 'risk', 'dominant']
        
        final_json = grid_df[cols].replace({np.nan: None}).to_json(orient='records')
        
        s3_client.put_object(
            Bucket=S3_BUCKET, 
            Key=S3_GRID_OUTPUT_KEY, 
            Body=final_json, 
            ContentType='application/json'
        )
        
        print(f"📦 SUCCESS {VERSION}: {len(final_json)/1024:.2f} KB guardados en S3.")
        return {'statusCode': 200, 'body': f'Predictor {VERSION} OK'}

    except Exception as e:
        print(f"❌ ERROR FATAL: {e}")
        return {'statusCode': 500, 'body': str(e)}
