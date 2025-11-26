import json
import boto3
import pandas as pd
import numpy as np
import xgboost as xgb
import requests
from datetime import datetime
import os

# --- CONFIGURACIÓN ---
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
# Ahora espera un solo modelo de O3
S3_MODEL_KEY = os.environ.get('S3_MODEL_KEY', 'models/model_o3.json')
S3_GRID_OUTPUT_KEY = os.environ.get('S3_GRID_OUTPUT_KEY', 'live_grid/latest_grid.json')
TARGET_POLLUTANT = 'o3' # Definimos el target para la columna de salida

# Ruta al grid base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GRID_BASE_PATH = os.path.join(BASE_DIR, 'grid_base.csv')

SMABILITY_API_URL = os.environ.get('SMABILITY_API_URL', 'https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference,smaa')

s3_client = boto3.client('s3')

def load_model_from_s3():
    """Descarga y carga el modelo único de O3 desde S3."""
    print(f"⬇️ Descargando modelo {TARGET_POLLUTANT.upper()} desde s3://{S3_BUCKET}/{S3_MODEL_KEY}...")
    try:
        local_model_path = f'/tmp/model_{TARGET_POLLUTANT}.json'
        s3_client.download_file(S3_BUCKET, S3_MODEL_KEY, local_model_path)
        
        model = xgb.XGBRegressor()
        model.load_model(local_model_path)
        return model
    except Exception as e:
        print(f"❌ Error cargando modelo {TARGET_POLLUTANT.upper()}: {e}")
        raise e

def get_live_data():
    """Consulta la API Maestra."""
    print(f"📡 Consultando API Smability...")
    # ... [Resto de la función get_live_data es el mismo]
    try:
        response = requests.get(SMABILITY_API_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get('stations', [])
    except Exception as e:
        print(f"❌ Error consultando API: {e}")
        return []

def process_stations_data(stations_list):
    """Convierte el JSON complejo de tu API en un DataFrame plano."""
    # ... [Resto de la función process_stations_data es el mismo, solo asegurando tmp y rh]
    parsed_data = []
    
    for s in stations_list:
        try:
            station_id = s.get('station_id')
            lat = float(s.get('latitude'))
            lon = float(s.get('longitude'))
            
            meteo = s.get('meteorological', {})
            temp_obj = meteo.get('temperature', {}).get('avg_1h', {})
            rh_obj = meteo.get('relative_humidity', {}).get('avg_1h', {})
            
            temp = temp_obj.get('value') if temp_obj else None
            rh = rh_obj.get('value') if rh_obj else None
            wsp = 0 
            wdr = 0 
            
            if lat and lon:
                parsed_data.append({
                    'station_id': station_id,
                    'lat': lat,
                    'lon': lon,
                    'tmp': temp,
                    'rh': rh,
                    'wsp': wsp,
                    'wdr': wdr
                })
                
        except Exception:
            continue
            
    df = pd.DataFrame(parsed_data)
    return df

def prepare_grid_features(stations_df):
    """Prepara el DataFrame del grid con todas las características calculadas."""
    
    # 1. Cargar Malla Base
    print(f"🗺️ Cargando Grid Base desde: {GRID_BASE_PATH}...")
    try:
        grid_df = pd.read_csv(GRID_BASE_PATH)
    except FileNotFoundError:
        print(f"⚠️ Grid base no encontrado. Usando grid de emergencia...")
        lats = np.arange(19.05, 19.60, 0.009)
        lons = np.arange(-99.35, -98.90, 0.009)
        grid_points = [{'lat': lat, 'lon': lon, 'alt': 2240} for lat in lats for lon in lons]
        grid_df = pd.DataFrame(grid_points)

    # 2. Features Temporales y Meteorológicas
    now = datetime.now()
    hour = now.hour
    month = now.month
    
    # Cliclos (Iguales para todo el mapa)
    grid_df['hour_sin'] = np.sin(2 * np.pi * hour / 24)
    grid_df['hour_cos'] = np.cos(2 * np.pi * hour / 24)
    grid_df['month_sin'] = np.sin(2 * np.pi * month / 12)
    grid_df['month_cos'] = np.cos(2 * np.pi * month / 12)
    
    # Meteorología (Promedio de la Ciudad)
    current_tmp = stations_df['tmp'].mean() if not stations_df['tmp'].isnull().all() else 20
    current_rh = stations_df['rh'].mean() if not stations_df['rh'].isnull().all() else 40
    grid_df['tmp'] = current_tmp
    grid_df['rh'] = current_rh
    grid_df['wsp'] = 0 
    grid_df['wdr'] = 0
    grid_df['station_code'] = -1 # Código dummy
    
    return grid_df

def interpolate_grid(model, grid_df):
    """Genera la predicción para la malla usando el modelo de O3."""
    
    # Las features deben coincidir con las usadas en el entrenamiento (incluyendo lat/lon)
    FEATURES = [
        'lat', 'lon', 'station_code', 'hour_sin', 'hour_cos', 
        'month_sin', 'month_cos', 'tmp', 'rh', 'wsp', 'wdr'
    ]
    
    X_predict = grid_df[FEATURES]
    
    print(f"🔮 Generando predicciones para {TARGET_POLLUTANT.upper()}...")
    predictions = model.predict(X_predict)
    
    predictions = np.maximum(predictions, 0)
    # Nombrar la columna de salida con el contaminante target
    grid_df[TARGET_POLLUTANT] = predictions.round(1)
    
    # Output: lat, lon, y el valor de O3
    output_cols = ['lat', 'lon', TARGET_POLLUTANT]
    return grid_df[output_cols]

def lambda_handler(event, context):
    print("🚀 Iniciando Lambda (Modelo Simple)...")
    
    # 1. Cargar el Modelo de O3
    model = load_model_from_s3()
    
    # 2. Obtener Datos Reales (para meteorología promedio)
    raw_stations = get_live_data()
    if not raw_stations:
        return {'statusCode': 500, 'body': 'Error: API maestra sin datos'}
        
    stations_df = process_stations_data(raw_stations)
    print(f"✅ Datos procesados: {len(stations_df)} estaciones.")
    
    # 3. Preparar el Grid con Features de Entrada
    grid_df = prepare_grid_features(stations_df)
    
    # 4. Generar Grid de Predicciones
    result_grid = interpolate_grid(model, grid_df)
    
    # 5. Guardar en S3 (JSON ligero)
    json_output = result_grid.to_json(orient='records')
    
    print(f"💾 Guardando en s3://{S3_BUCKET}/{S3_GRID_OUTPUT_KEY}")
    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=S3_GRID_OUTPUT_KEY,
        Body=json_output,
        ContentType='application/json'
    )
    
    return {
        'statusCode': 200,
        'body': json.dumps(f'Grid de {TARGET_POLLUTANT.upper()} generado: {len(result_grid)} puntos.')
    }
