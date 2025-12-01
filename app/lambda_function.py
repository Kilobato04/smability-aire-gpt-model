import json
import boto3
import pandas as pd
import numpy as np
import xgboost as xgb
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import time

# --- CONFIGURACIÓN ---
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
S3_GRID_OUTPUT_KEY = os.environ.get('S3_GRID_OUTPUT_KEY', 'live_grid/latest_grid.json')

MODEL_PATH_O3 = os.path.join(os.environ.get('LAMBDA_TASK_ROOT', '/var/task'), 'model_o3.json')
MODEL_PATH_PM10 = os.path.join(os.environ.get('LAMBDA_TASK_ROOT', '/var/task'), 'model_pm10.json')

GRID_BASE_PATH = '/tmp/grid_base_v10.csv'
SMABILITY_API_URL = os.environ.get('SMABILITY_API_URL', 'https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference,smaa')

s3_client = boto3.client('s3')

def load_models():
    print("⬇️ Cargando modelos...", flush=True)
    models = {}
    try:
        m_o3 = xgb.XGBRegressor()
        m_o3.load_model(MODEL_PATH_O3)
        models['o3'] = m_o3
        
        if os.path.exists(MODEL_PATH_PM10):
            m_pm10 = xgb.XGBRegressor()
            m_pm10.load_model(MODEL_PATH_PM10)
            models['pm10'] = m_pm10
        
        return models
    except Exception as e:
        print(f"❌ Error cargando modelos: {e}", flush=True)
        raise e

def get_live_data():
    try:
        response = requests.get(SMABILITY_API_URL, timeout=15)
        response.raise_for_status()
        return response.json().get('stations', [])
    except Exception as e:
        print(f"❌ Error API: {e}", flush=True)
        return []

def process_stations_data(stations_list):
    parsed_data = []
    for s in stations_list:
        try:
            if s.get('latitude') is None or s.get('longitude') is None: continue
            
            st_name = s.get('station_name', 'Unknown')
            lat = float(s.get('latitude'))
            lon = float(s.get('longitude'))
            alt = float(s.get('altitude')) if s.get('altitude') is not None else np.nan
            
            pollutants = s.get('pollutants', {})
            o3_val = pollutants.get('o3', {}).get('avg_1h', {}).get('value')
            pm10_val = pollutants.get('pm10', {}).get('avg_1h', {}).get('value')
            
            meteo = s.get('meteorological', {})
            def get_val(key): return meteo.get(key, {}).get('avg_1h', {}).get('value')
            temp = get_val('temperature')
            rh = get_val('relative_humidity')
            wsp = get_val('wind_speed')
            wdr = get_val('wind_direction')
            
            parsed_data.append({
                'name': st_name,
                'lat': lat, 'lon': lon, 'alt': alt,
                'o3_real': float(o3_val) if o3_val is not None else np.nan,
                'pm10_real': float(pm10_val) if pm10_val is not None else np.nan,
                'tmp': float(temp) if temp is not None else np.nan, 
                'rh': float(rh) if rh is not None else np.nan, 
                'wsp': float(wsp) if wsp is not None else np.nan, 
                'wdr': float(wdr) if wdr is not None else np.nan
            })
        except Exception:
            continue
            
    df = pd.DataFrame(parsed_data)
    v_o3 = df['o3_real'].count() if not df.empty else 0
    v_pm10 = df['pm10_real'].count() if not df.empty else 0
    print(f"📊 Datos Vivos: {len(df)} st. | O3: {v_o3} | PM10: {v_pm10}", flush=True)
    return df

def inverse_distance_weighting(x, y, z, xi, yi, power=2):
    dist = np.sqrt((xi[:, None] - x[None, :])**2 + (yi[:, None] - y[None, :])**2)
    dist = np.maximum(dist, 1e-12)
    weights = 1.0 / (dist ** power)
    return np.sum(weights * z[None, :], axis=1) / np.sum(weights, axis=1)

def prepare_grid_features(stations_df):
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

    cdmx_tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(cdmx_tz)
    
    grid_df['hour_sin'] = np.sin(2 * np.pi * now.hour / 24)
    grid_df['hour_cos'] = np.cos(2 * np.pi * now.hour / 24)
    grid_df['month_sin'] = np.sin(2 * np.pi * now.month / 12)
    grid_df['month_cos'] = np.cos(2 * np.pi * now.month / 12)
    
    grid_lats = grid_df['lat'].values
    grid_lons = grid_df['lon'].values

    def interpolate_feature(col_name, default_val):
        valid = stations_df.dropna(subset=[col_name])
        if not valid.empty:
            return inverse_distance_weighting(
                valid['lat'].values, valid['lon'].values, valid[col_name].values,
                grid_lats, grid_lons
            )
        else:
            return np.full(len(grid_df), default_val)

    grid_df['tmp'] = interpolate_feature('tmp', 20.0).round(1)
    grid_df['rh'] = interpolate_feature('rh', 40.0).round(1)
    grid_df['altitude'] = interpolate_feature('alt', 2240.0).round(0)
    
    valid_wind = stations_df.dropna(subset=['wsp', 'wdr'])
    if not valid_wind.empty:
        wdr_rad = np.deg2rad(valid_wind['wdr'].values)
        u_comp = valid_wind['wsp'].values * np.cos(wdr_rad)
        v_comp = valid_wind['wsp'].values * np.sin(wdr_rad)
        st_lats, st_lons = valid_wind['lat'].values, valid_wind['lon'].values
        grid_u = inverse_distance_weighting(st_lats, st_lons, u_comp, grid_lats, grid_lons)
        grid_v = inverse_distance_weighting(st_lats, st_lons, v_comp, grid_lats, grid_lons)
        grid_df['wsp'] = np.sqrt(grid_u**2 + grid_v**2).round(1)
        grid_df['wdr'] = (np.degrees(np.arctan2(grid_v, grid_u)) % 360).round(0)
    else:
        grid_df['wsp'] = 0.0; grid_df['wdr'] = 0.0

    grid_df['station_numeric'] = -1 
    return grid_df

def predict_and_calibrate(model, grid_df, stations_df, real_col_name, output_col_name):
    FEATURES = ['lat', 'lon', 'altitude', 'station_numeric', 
                'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 
                'tmp', 'rh', 'wsp', 'wdr']
    
    raw_preds = model.predict(grid_df[FEATURES])
    raw_preds = np.maximum(raw_preds, 0)
    
    calibration_set = stations_df.dropna(subset=[real_col_name]).copy()
    
    if not calibration_set.empty:
        source_tmp = stations_df.dropna(subset=['tmp'])
        source_rh = stations_df.dropna(subset=['rh'])
        source_alt = stations_df.dropna(subset=['alt'])
        target_lats = calibration_set['lat'].values
        target_lons = calibration_set['lon'].values
        
        def impute(col, source_df):
            missing = calibration_set[col].isna()
            if missing.any() and not source_df.empty:
                calibration_set.loc[missing, col] = inverse_distance_weighting(
                    source_df['lat'].values, source_df['lon'].values, source_df[col].values,
                    target_lats[missing], target_lons[missing]
                )

        impute('tmp', source_tmp)
        impute('rh', source_rh)
        impute('alt', source_alt)
        
        calibration_set['tmp'] = calibration_set['tmp'].fillna(20)
        calibration_set['rh'] = calibration_set['rh'].fillna(40)
        calibration_set['altitude'] = calibration_set['alt'].fillna(2240)
        calibration_set['wsp'] = calibration_set['wsp'].fillna(0)
        calibration_set['wdr'] = calibration_set['wdr'].fillna(0)
        calibration_set['station_numeric'] = -1
        for col in ['hour_sin', 'hour_cos', 'month_sin', 'month_cos']:
            calibration_set[col] = grid_df[col].iloc[0]

        station_preds = model.predict(calibration_set[FEATURES])
        residuals = calibration_set[real_col_name].values - station_preds
        
        # --- LOGS FORZADOS V19 ---
        print("\n" + "="*85, flush=True)
        print(f"🔧 CALIBRACIÓN: {output_col_name.upper()} ({len(calibration_set)} estaciones)", flush=True)
        print(f"{'#':<3} | {'HORA':<6} | {'ESTACIÓN':<18} | {'REAL':<6} | {'BASE':<6} | {'BIAS':<6} | {'FINAL':<6}", flush=True)
        print("-" * 85, flush=True)
        
        cdmx_tz = ZoneInfo("America/Mexico_City")
        curr_time = datetime.now(cdmx_tz).strftime("%H:%M")
        
        for i, row in calibration_set.reset_index().iterrows():
            st_name = row['name'][:18] 
            real = row[real_col_name]
            base = station_preds[i]
            bias = residuals[i]
            final = base + bias
            # flush=True asegura que cada línea se imprima al instante y no se corte
            print(f"{i+1:<3} | {curr_time:<6} | {st_name:<18} | {real:<6.1f} | {base:<6.1f} | {bias:<+6.1f} | {final:<6.1f}", flush=True)
            
        print("-" * 85, flush=True)
        print(f"📉 BIAS PROMEDIO: {np.mean(residuals):+.2f}", flush=True)
        print("="*85 + "\n", flush=True)
        # -----------------------------

        grid_bias = inverse_distance_weighting(
            calibration_set['lat'].values, calibration_set['lon'].values, residuals,
            grid_df['lat'].values, grid_df['lon'].values
        )
        final_preds = raw_preds + grid_bias
        return np.maximum(final_preds, 0).round(1)
        
    else:
        print(f"⚠️ Sin datos reales de {output_col_name}. Usando RAW.", flush=True)
        return raw_preds.round(1)

def calculate_ias_row(row):
    def get_ias(c, breakpoints):
        for (c_lo, c_hi, i_lo, i_hi) in breakpoints:
            if c <= c_hi:
                return i_lo + ((c - c_lo) / (c_hi - c_lo)) * (i_hi - i_lo)
        last = breakpoints[-1]
        return last[2] + ((c - last[0]) / (last[1] - last[0])) * (last[3] - last[2])

    bps_o3 = [(0,58,0,50), (59,92,51,100), (93,135,101,150), (136,175,151,200), (176,240,201,300)]
    bps_pm10 = [(0,45,0,50), (46,60,51,100), (61,132,101,150), (133,213,151,200), (214,354,201,300)]

    ias_o3 = get_ias(row['o3'], bps_o3)
    ias_pm10 = get_ias(row['pm10'], bps_pm10)
    
    return round(max(ias_o3, ias_pm10))

def lambda_handler(event, context):
    print("🚀 Iniciando Lambda V19 (Logs Flush)...", flush=True)
    try:
        models = load_models()
        raw_stations = get_live_data()
        stations_df = process_stations_data(raw_stations)
        
        grid_df = prepare_grid_features(stations_df)
        
        grid_df['o3'] = predict_and_calibrate(models['o3'], grid_df, stations_df, 'o3_real', 'o3')
        
        if 'pm10' in models:
            grid_df['pm10'] = predict_and_calibrate(models['pm10'], grid_df, stations_df, 'pm10_real', 'pm10')
        else:
            grid_df['pm10'] = 0
            
        print("🧮 Calculando IAS...", flush=True)
        grid_df['ias'] = grid_df.apply(calculate_ias_row, axis=1)
        
        max_ias = grid_df['ias'].max()
        avg_ias = grid_df['ias'].mean()
        cat = "Buena" if max_ias<=50 else "Aceptable" if max_ias<=100 else "Mala" if max_ias<=150 else "Muy Mala"
        print(f"🚦 REPORTE IAS CIUDAD: Máximo={max_ias:.0f} ({cat}) | Promedio={avg_ias:.0f}", flush=True)
        
        cdmx_tz = ZoneInfo("America/Mexico_City")
        current_ts = datetime.now(cdmx_tz).strftime("%Y-%m-%d %H:%M:%S")
        grid_df['timestamp'] = current_ts
        
        final_df = grid_df[['timestamp', 'lat', 'lon', 'altitude', 'tmp', 'rh', 'wsp', 'wdr', 'o3', 'pm10', 'ias']]
        json_output = final_df.to_json(orient='records')
        
        timestamp_file = datetime.now(cdmx_tz).strftime("%Y-%m-%d_%H-%M")
        history_key = f"live_grid/grid_{timestamp_file}.json"
        
        print(f"💾 Guardando: {history_key}", flush=True)
        s3_client.put_object(Bucket=S3_BUCKET, Key=history_key, Body=json_output, ContentType='application/json')
        s3_client.put_object(Bucket=S3_BUCKET, Key=S3_GRID_OUTPUT_KEY, Body=json_output, ContentType='application/json')

        return {'statusCode': 200, 'body': json.dumps('Grid V19 OK')}
    except Exception as e:
        print(f"❌ Error Fatal: {str(e)}", flush=True)
        return {'statusCode': 500, 'body': json.dumps(f"Error: {str(e)}")}
