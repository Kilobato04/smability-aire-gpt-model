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
GRID_BASE_PATH = '/tmp/grid_base_v10.csv'
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
    parsed_data = []
    for s in stations_list:
        try:
            if s.get('latitude') is None or s.get('longitude') is None: continue
            
            lat = float(s.get('latitude'))
            lon = float(s.get('longitude'))
            alt = float(s.get('altitude')) if s.get('altitude') is not None else np.nan
            
            # Contaminantes
            pollutants = s.get('pollutants', {})
            o3_val = pollutants.get('o3', {}).get('avg_1h', {}).get('value')
            
            # Meteo
            meteo = s.get('meteorological', {})
            def get_val(key): return meteo.get(key, {}).get('avg_1h', {}).get('value')
            temp = get_val('temperature')
            rh = get_val('relative_humidity')
            wsp = get_val('wind_speed')
            wdr = get_val('wind_direction')
            
            # Aceptamos todo (Estrategia de Resiliencia V12)
            parsed_data.append({
                'lat': lat, 'lon': lon, 'alt': alt,
                'o3_real': float(o3_val) if o3_val is not None else np.nan,
                'tmp': float(temp) if temp is not None else np.nan, 
                'rh': float(rh) if rh is not None else np.nan, 
                'wsp': float(wsp) if wsp is not None else np.nan, 
                'wdr': float(wdr) if wdr is not None else np.nan
            })
        except Exception:
            continue
            
    df = pd.DataFrame(parsed_data)
    valid_o3 = df['o3_real'].count() if not df.empty else 0
    valid_tmp = df['tmp'].count() if not df.empty else 0
    print(f"📊 Raw Data: {len(df)} estaciones. Con O3: {valid_o3}. Con Temp: {valid_tmp}.")
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
    hour = now.hour; month = now.month
    
    grid_df['hour_sin'] = np.sin(2 * np.pi * hour / 24)
    grid_df['hour_cos'] = np.cos(2 * np.pi * hour / 24)
    grid_df['month_sin'] = np.sin(2 * np.pi * month / 12)
    grid_df['month_cos'] = np.cos(2 * np.pi * month / 12)
    
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

def calibrate_predictions(model, grid_df, stations_df):
    FEATURES = ['lat', 'lon', 'altitude', 'station_numeric', 
                'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 
                'tmp', 'rh', 'wsp', 'wdr']
    
    raw_preds = model.predict(grid_df[FEATURES])
    raw_preds = np.maximum(raw_preds, 0)
    
    # Rescate de Estaciones
    calibration_set = stations_df.dropna(subset=['o3_real']).copy()
    
    if not calibration_set.empty:
        # Imputación de huecos en puntos de calibración
        source_tmp = stations_df.dropna(subset=['tmp'])
        source_rh = stations_df.dropna(subset=['rh'])
        source_alt = stations_df.dropna(subset=['alt'])
        
        target_lats = calibration_set['lat'].values
        target_lons = calibration_set['lon'].values
        
        def impute_missing(col, source_df):
            missing_idx = calibration_set[col].isna()
            if missing_idx.any() and not source_df.empty:
                imputed_values = inverse_distance_weighting(
                    source_df['lat'].values, source_df['lon'].values, source_df[col].values,
                    target_lats[missing_idx], target_lons[missing_idx]
                )
                calibration_set.loc[missing_idx, col] = imputed_values

        impute_missing('tmp', source_tmp)
        impute_missing('rh', source_rh)
        impute_missing('alt', source_alt)
        
        # Defaults finales
        calibration_set['tmp'] = calibration_set['tmp'].fillna(20)
        calibration_set['rh'] = calibration_set['rh'].fillna(40)
        calibration_set['altitude'] = calibration_set['alt'].fillna(2240)
        calibration_set['wsp'] = calibration_set['wsp'].fillna(0)
        calibration_set['wdr'] = calibration_set['wdr'].fillna(0)
        calibration_set['station_numeric'] = -1
        
        for col in ['hour_sin', 'hour_cos', 'month_sin', 'month_cos']:
            calibration_set[col] = grid_df[col].iloc[0]
            
        # Predicción y Bias
        station_preds = model.predict(calibration_set[FEATURES])
        residuals = calibration_set['o3_real'].values - station_preds
        
        print(f"🔧 Calibrando con {len(calibration_set)} puntos rescatados. Bias Medio: {np.mean(residuals):.2f}")
        
        grid_bias = inverse_distance_weighting(
            calibration_set['lat'].values, calibration_set['lon'].values, residuals,
            grid_df['lat'].values, grid_df['lon'].values
        )
        
        final_preds = raw_preds + grid_bias
        grid_df[TARGET_POLLUTANT] = np.maximum(final_preds, 0).round(1)
    else:
        print("⚠️ Sin datos de O3. Usando RAW.")
        grid_df[TARGET_POLLUTANT] = raw_preds.round(1)

    return grid_df

def lambda_handler(event, context):
    print("🚀 Iniciando Lambda V12 (Calibration Fix)...")
    try:
        model = load_model_local()
        raw_stations = get_live_data()
        stations_df = process_stations_data(raw_stations)
        
        grid_df = prepare_grid_features(stations_df)
        result_grid = calibrate_predictions(model, grid_df, stations_df)
        
        cdmx_tz = ZoneInfo("America/Mexico_City")
        current_ts = datetime.now(cdmx_tz).strftime("%Y-%m-%d %H:%M:%S")
        result_grid['timestamp'] = current_ts
        
        final_df = result_grid[['timestamp', 'lat', 'lon', 'altitude', 'tmp', 'rh', 'wsp', 'wdr', TARGET_POLLUTANT]]
        json_output = final_df.to_json(orient='records')
        
        timestamp_file = datetime.now(cdmx_tz).strftime("%Y-%m-%d_%H-%M")
        history_key = f"live_grid/grid_{timestamp_file}.json"
        
        print(f"💾 Guardando: {history_key}")
        s3_client.put_object(Bucket=S3_BUCKET, Key=history_key, Body=json_output, ContentType='application/json')
        s3_client.put_object(Bucket=S3_BUCKET, Key=S3_GRID_OUTPUT_KEY, Body=json_output, ContentType='application/json')

        return {'statusCode': 200, 'body': json.dumps('Grid V12 OK')}
    except Exception as e:
        print(f"❌ Error Fatal: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps(f"Error: {str(e)}")}
