import json
import boto3
import pandas as pd
import numpy as np
import xgboost as xgb
import requests
from datetime import datetime
from scipy.interpolate import griddata
import os
from zoneinfo import ZoneInfo

# --- 1. CONFIGURACIÓN Y CONSTANTES ---
S3_BUCKET = "smability-data-lake"
S3_FORECAST_PREFIX = "forecast/"
MODEL_S3_PREFIX = "models/"
BASE_PATH = os.environ.get('LAMBDA_TASK_ROOT', '/var/task')

# Endpoint calibrado (Zona Metropolitana del Valle de México)
# Trae 24h de pronóstico para múltiples puntos clave de la malla
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast?latitude=19.15,19.15,19.15,19.15,19.15,19.15,19.276,19.276,19.276,19.276,19.276,19.276,19.402,19.402,19.402,19.402,19.402,19.402,19.528,19.528,19.528,19.528,19.528,19.528,19.654,19.654,19.654,19.654,19.654,19.654,19.78,19.78,19.78,19.78,19.78,19.78&longitude=-99.39,-99.284,-99.178,-99.072,-98.966,-98.86,-99.39,-99.284,-99.178,-99.072,-98.966,-98.86,-99.39,-99.284,-99.178,-99.072,-98.966,-98.86,-99.39,-99.284,-99.178,-99.072,-98.966,-98.86,-99.39,-99.284,-99.178,-99.072,-98.966,-98.86,-99.39,-99.284,-99.178,-99.072,-98.966,-98.86&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m&timezone=America%2FMexico_City&forecast_days=1&wind_speed_unit=ms"

s3_client = boto3.client('s3')

# --- 2. NORMATIVIDAD (IAS - NOM-172-SEMARNAT-2019) ---

# O3 (1h) - Unidades: ppb
BPS_O3 = [(0,58,0,50), (59,92,51,100), (93,135,101,150), (136,175,151,200), (176,240,201,300)]
# PM10 (Promedio) - Unidades: µg/m³
BPS_PM10 = [(0,45,0,50), (46,60,51,100), (61,132,101,150), (133,213,151,200), (214,354,201,300)]
# PM2.5 (Promedio) - Unidades: µg/m³
BPS_PM25 = [(0,25,0,50), (26,45,51,100), (46,79,101,150), (80,147,151,200), (148,250,201,300)]
# CO (8h) - Unidades: ppm
BPS_CO = [(0, 8.75, 0, 50), (8.76, 11.00, 51, 100), (11.01, 13.30, 101, 150), (13.31, 15.50, 151, 200), (15.51, 20.00, 201, 300)]
# SO2 (1h) - Unidades: ppb
BPS_SO2 = [(0, 40, 0, 50), (41, 75, 51, 100), (76, 185, 101, 150), (186, 304, 151, 200), (305, 500, 201, 300)]

def get_ias_score(c, pollutant):
    """Calcula el índice Aire y Salud (IAS) interpolado"""
    try:
        c = float(c)
        if pollutant == 'o3': bps = BPS_O3
        elif pollutant == 'pm10': bps = BPS_PM10
        elif pollutant == 'pm25': bps = BPS_PM25
        elif pollutant == 'co': bps = BPS_CO
        elif pollutant == 'so2': bps = BPS_SO2
        else: return 0 
        
        for (c_lo, c_hi, i_lo, i_hi) in bps:
            if c <= c_hi:
                return i_lo + ((c - c_lo) / (c_hi - c_lo)) * (i_hi - i_lo)
        return bps[-1][3] # Saturación máxima (Riesgo Extremo)
    except: return 0

def get_risk_level(ias):
    if ias <= 50: return "Bajo"
    if ias <= 100: return "Moderado"
    if ias <= 150: return "Alto"
    if ias <= 200: return "Muy Alto"
    return "Extremadamente Alto"

# --- 3. GESTIÓN DE RECURSOS ---
def load_models():
    """Descarga y carga en memoria los 5 modelos XGBoost"""
    models = {}
    pollutants = ['o3', 'pm10', 'pm25', 'co', 'so2']
    print(f"🔄 Cargando modelos Forecast: {pollutants}")
    
    for p in pollutants:
        local_path = f"/tmp/model_{p}.json"
        s3_key = f"{MODEL_S3_PREFIX}model_{p}.json"
        try:
            if not os.path.exists(local_path):
                s3_client.download_file(S3_BUCKET, s3_key, local_path)
            
            m = xgb.XGBRegressor()
            m.load_model(local_path)
            models[p] = m
            print(f"   ✅ Modelo {p} OK.")
        except Exception as e:
            print(f"   ⚠️ Falló carga de {p} ({e}). Se usará 0.0 por defecto.")
    return models

def load_static_grid():
    """Carga Malla enriquecida con Colonias y Edificios"""
    # 1. Cargar Malla Base
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

    # 2. Enriquecer con Colonias (Paridad con Live Grid)
    try:
        COLONIAS_PATH = f"{BASE_PATH}/app/geograficos/grid_colonias_db.json"
        with open(COLONIAS_PATH, 'r') as f:
            colonias_data = json.load(f)
        cols_df = pd.DataFrame(colonias_data)
        
        # Merge por llaves redondeadas para precisión
        grid_df['lat_key'] = grid_df['lat'].round(5)
        grid_df['lon_key'] = grid_df['lon'].round(5)
        cols_df['lat_key'] = cols_df['lat'].round(5)
        cols_df['lon_key'] = cols_df['lon'].round(5)
        
        grid_df = pd.merge(grid_df, cols_df[['lat_key', 'lon_key', 'col', 'mun', 'edo', 'pob']], 
                           on=['lat_key', 'lon_key'], how='left')
        
        grid_df.drop(columns=['lat_key', 'lon_key'], inplace=True)
    except Exception as e:
        print(f"⚠️ No se pudo cargar DB de Colonias: {e}")
        for c in ['col', 'mun', 'edo', 'pob']: grid_df[c] = None
    
    # 3. Edificios
    try:
        EDIFICIOS_PATH = f"{BASE_PATH}/app/geograficos/capa_edificios_v2.json"
        with open(EDIFICIOS_PATH, 'r') as f:
            edificios_df = pd.DataFrame(json.load(f))
        edificios_df['lat_key'] = edificios_df['lat'].round(5)
        edificios_df['lon_key'] = edificios_df['lon'].round(5)
        grid_df['lat_key'] = grid_df['lat'].round(5)
        grid_df['lon_key'] = grid_df['lon'].round(5)
        
        grid_df = pd.merge(grid_df, edificios_df[['lat_key', 'lon_key', 'building_vol']], 
                           on=['lat_key', 'lon_key'], how='left')
        grid_df.drop(columns=['lat_key', 'lon_key'], inplace=True)
    except:
        grid_df['building_vol'] = 0

    # Limpieza final
    grid_df['col'] = grid_df['col'].fillna("Zona Federal")
    grid_df['mun'] = grid_df['mun'].fillna("Valle de México")
    grid_df['pob'] = grid_df['pob'].fillna(0)
    grid_df['building_vol'] = grid_df['building_vol'].fillna(0)
    
    return grid_df

# --- 4. MOTOR MATEMÁTICO ---
def interpolate_on_grid(grid_df, x_src, y_src, z_src, method='linear'):
    """Interpolación IDW/Linear robusta"""
    if len(x_src) < 4: return [np.mean(z_src)] * len(grid_df) # Fallback
    
    grid_coords = (grid_df['lon'], grid_df['lat'])
    # Interpolación lineal
    grid_z = griddata((x_src, y_src), z_src, grid_coords, method=method)
    
    # Rellenar bordes (NaNs) con nearest
    if np.isnan(grid_z).any():
        grid_z_nearest = griddata((x_src, y_src), z_src, grid_coords, method='nearest')
        grid_z = np.where(np.isnan(grid_z), grid_z_nearest, grid_z)
    
    return grid_z

# --- 5. HANDLER PRINCIPAL ---
def lambda_handler(event, context):
    print("🚀 INICIANDO FORECAST ENGINE V2.1 (Full Chemistry)")
    
    try:
        # A. Inicialización
        models = load_models()
        base_grid_df = load_static_grid()
        print(f"🗺️ Malla lista: {len(base_grid_df)} celdas.")
        
        # B. Ingesta Open-Meteo
        print("🌍 Consultando Meteorología Futura...")
        r = requests.get(OPEN_METEO_URL, timeout=25)
        raw_data = r.json()
        if not isinstance(raw_data, list): raw_data = [raw_data]

        # C. Pivoteo (Ubicación -> Tiempo)
        hourly_buffer = {} 
        for location in raw_data:
            lat, lon = location['latitude'], location['longitude']
            hourly = location['hourly']
            times = hourly['time'] # ISO Strings
            
            for i, t_iso in enumerate(times):
                if t_iso not in hourly_buffer:
                    hourly_buffer[t_iso] = {'lats':[], 'lons':[], 'tmp':[], 'rh':[], 'wsp':[], 'wdr':[]}
                
                # Acumulamos los puntos dispersos para esta hora
                hourly_buffer[t_iso]['lats'].append(lat)
                hourly_buffer[t_iso]['lons'].append(lon)
                hourly_buffer[t_iso]['tmp'].append(hourly['temperature_2m'][i])
                hourly_buffer[t_iso]['rh'].append(hourly['relative_humidity_2m'][i])
                hourly_buffer[t_iso]['wsp'].append(hourly['wind_speed_10m'][i])
                hourly_buffer[t_iso]['wdr'].append(hourly['wind_direction_10m'][i])

        # D. Generación de Archivos
        generated_files = []
        print(f"⏳ Procesando {len(hourly_buffer)} horas...")
        
        for t_iso, met_data in hourly_buffer.items():
            current_grid = base_grid_df.copy()
            
            # Parsing Fecha
            dt_obj = datetime.strptime(t_iso, "%Y-%m-%dT%H:%M")
            file_name = dt_obj.strftime("%Y-%m-%d_%H-%M.json")
            
            # 1. Interpolación Meteorológica
            current_grid['tmp'] = interpolate_on_grid(current_grid, met_data['lons'], met_data['lats'], met_data['tmp'])
            current_grid['rh']  = interpolate_on_grid(current_grid, met_data['lons'], met_data['lats'], met_data['rh'])
            current_grid['wsp'] = interpolate_on_grid(current_grid, met_data['lons'], met_data['lats'], met_data['wsp'])
            
            # Viento vectorial
            u_vec = [-w * np.sin(np.radians(d)) for w, d in zip(met_data['wsp'], met_data['wdr'])]
            v_vec = [-w * np.cos(np.radians(d)) for w, d in zip(met_data['wsp'], met_data['wdr'])]
            grid_u = interpolate_on_grid(current_grid, met_data['lons'], met_data['lats'], u_vec)
            grid_v = interpolate_on_grid(current_grid, met_data['lons'], met_data['lats'], v_vec)
            current_grid['wdr'] = (np.degrees(np.arctan2(-grid_u, -grid_v))) % 360

            # 2. Features IA
            current_grid['hour_sin'] = np.sin(2 * np.pi * dt_obj.hour / 24)
            current_grid['hour_cos'] = np.cos(2 * np.pi * dt_obj.hour / 24)
            current_grid['month_sin'] = np.sin(2 * np.pi * dt_obj.month / 12)
            current_grid['month_cos'] = np.cos(2 * np.pi * dt_obj.month / 12)
            current_grid['station_numeric'] = -1 
            
            features_ia = ['lat', 'lon', 'altitude', 'building_vol', 'station_numeric', 
                           'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 
                           'tmp', 'rh', 'wsp', 'wdr']
            
            # 3. Inferencia Química (5 Gases)
            for p in ['o3', 'pm10', 'pm25', 'co', 'so2']:
                if p in models:
                    preds = models[p].predict(current_grid[features_ia]).clip(0)
                    current_grid[p] = preds
                else:
                    current_grid[p] = 0.0

            # 4. Cálculo de Índices (IAS)
            def calc_ias_row(row):
                scores = {
                    'O3': get_ias_score(row['o3'], 'o3'),
                    'PM10': get_ias_score(row['pm10'], 'pm10'),
                    'PM2.5': get_ias_score(row['pm25'], 'pm25'),
                    'CO': get_ias_score(row['co'], 'co'),
                    'SO2': get_ias_score(row['so2'], 'so2')
                }
                dom_pol = max(scores, key=scores.get)
                return pd.Series([max(scores.values()), dom_pol])
            
            current_grid[['ias', 'dominant']] = current_grid.apply(calc_ias_row, axis=1)
            current_grid['risk'] = current_grid['ias'].apply(get_risk_level)
            
            # 5. Exportación
            output_df = pd.DataFrame()
            output_df['timestamp'] = [t_iso.replace("T", " ")] * len(current_grid)
            
            # Geografía
            for c in ['lat', 'lon', 'col', 'mun', 'edo', 'pob', 'altitude', 'building_vol']:
                output_df[c] = current_grid[c]
            
            # Meteorología
            output_df['tmp'] = current_grid['tmp'].round(1)
            output_df['rh'] = current_grid['rh'].round(0)
            output_df['wsp'] = current_grid['wsp'].round(1)
            output_df['wdr'] = current_grid['wdr'].round(0)
            
            # Química (Mapping Frontend)
            output_df['o3 1h']    = current_grid['o3'].round(1)
            output_df['pm10 12h'] = current_grid['pm10'].round(1)
            output_df['pm25 12h'] = current_grid['pm25'].round(1)
            output_df['co 8h']    = current_grid['co'].round(2)
            output_df['so2 1h']   = current_grid['so2'].round(1)
            
            # Indices y Metadata
            output_df['ias'] = current_grid['ias'].astype(int)
            output_df['risk'] = current_grid['risk']
            output_df['dominant'] = current_grid['dominant']
            output_df['station'] = None 
            
            # Sources Explícitos
            sources_map = {
                "tmp": "Open-Meteo", "rh": "Open-Meteo", "wsp": "Open-Meteo",
                "o3": "AI Forecast", "pm10": "AI Forecast", "pm25": "AI Forecast",
                "co": "AI Forecast", "so2": "AI Forecast"
            }
            output_df['sources'] = json.dumps(sources_map)
            
            # Guardar S3
            json_body = output_df.replace({np.nan: None}).to_json(orient='records')
            s3_key = f"{S3_FORECAST_PREFIX}{file_name}"
            s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json_body, ContentType='application/json')
            generated_files.append(file_name)

        print(f"✅ FORECAST COMPLETADO: {len(generated_files)} archivos generados.")
        return {'statusCode': 200, 'body': json.dumps({'files': len(generated_files)})}

    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        raise e
