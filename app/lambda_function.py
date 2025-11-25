import json
import boto3
import pandas as pd
import numpy as np
import xgboost as xgb
import requests
from datetime import datetime
import io
import os

# --- CONFIGURACIÓN ---
# Variables de entorno que configurarás en AWS Lambda
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
S3_MODEL_KEY = os.environ.get('S3_MODEL_KEY', 'models/production_model.json')
S3_GRID_OUTPUT_KEY = os.environ.get('S3_GRID_OUTPUT_KEY', 'live_grid/latest_grid.json')

# Construir la ruta absoluta a grid_base.csv basada en la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GRID_BASE_PATH = os.path.join(BASE_DIR, 'grid_base.csv')

# Tu URL real
SMABILITY_API_URL = os.environ.get('SMABILITY_API_URL', 'https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference,smaa')

s3_client = boto3.client('s3')

def load_model_from_s3():
    """Descarga el modelo entrenado desde S3 a /tmp para usarlo."""
    print(f"⬇️ Descargando modelo desde s3://{S3_BUCKET}/{S3_MODEL_KEY}...")
    try:
        local_model_path = '/tmp/model.json'
        s3_client.download_file(S3_BUCKET, S3_MODEL_KEY, local_model_path)
        
        model = xgb.XGBRegressor()
        model.load_model(local_model_path)
        return model
    except Exception as e:
        print(f"❌ Error cargando modelo: {e}")
        raise e

def get_live_data():
    """Consulta tu API Maestra."""
    print(f"📡 Consultando API Smability...")
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
    parsed_data = []
    
    for s in stations_list:
        try:
            # Datos básicos
            station_id = s.get('station_id')
            
            # Coordenadas (son strings en tu JSON, convertimos a float)
            lat = float(s.get('latitude'))
            lon = float(s.get('longitude'))
            
            # Meteorología
            meteo = s.get('meteorological', {})
            temp_obj = meteo.get('temperature', {}).get('avg_1h', {})
            rh_obj = meteo.get('relative_humidity', {}).get('avg_1h', {})
            
            # Extraer valores con seguridad (puede venir None)
            temp = temp_obj.get('value') if temp_obj else None
            rh = rh_obj.get('value') if rh_obj else None
            
            # Viento (Placeholder: Tu API no lo trae hoy, usamos 0 o promedio global luego)
            # Cuando tu API lo tenga, ajusta aquí:
            wsp = 0 
            wdr = 0 

            # Contaminantes (Target)
            pollutants = s.get('pollutants', {})
            pm10_obj = pollutants.get('pm10', {}).get('avg_1h', {})
            o3_obj = pollutants.get('o3', {}).get('avg_1h', {})
            
            pm10 = pm10_obj.get('value') if pm10_obj else None
            o3 = o3_obj.get('value') if o3_obj else None
            
            # Solo agregamos si tenemos coordenadas válidas
            if lat and lon:
                parsed_data.append({
                    'station_id': station_id,
                    'lat': lat,
                    'lon': lon,
                    'tmp': temp,
                    'rh': rh,
                    'wsp': wsp,
                    'wdr': wdr,
                    'pm10': pm10,
                    'o3': o3
                })
                
        except Exception as e:
            # Si una estación falla, la saltamos pero no rompemos todo
            # print(f"⚠️ Saltando estación {s.get('station_id')}: {e}")
            continue
            
    df = pd.DataFrame(parsed_data)
    return df

def interpolate_grid(model, stations_df):
    """Genera la predicción para toda la malla de CDMX."""
    
    # 1. Cargar Malla Base
    print(f"🗺️ Cargando Grid Base desde: {GRID_BASE_PATH}...")
    try:
        grid_df = pd.read_csv(GRID_BASE_PATH)
    except FileNotFoundError:
        print(f"⚠️ Grid base no encontrado en {GRID_BASE_PATH}. Generando uno de emergencia...")
        lats = np.arange(19.05, 19.60, 0.009)
        lons = np.arange(-99.35, -98.90, 0.009)
        grid_points = [{'lat': lat, 'lon': lon, 'alt': 2240} for lat in lats for lon in lons]
        grid_df = pd.DataFrame(grid_points)

    # 2. Preparar Features para el Modelo
    # El modelo espera: [station_code, hour_sin, hour_cos, month_sin, month_cos, tmp, rh, wsp, wdr]
    
    now = datetime.now()
    hour = now.hour
    month = now.month
    
    # -- Features Temporales (Iguales para todo el mapa) --
    hour_sin = np.sin(2 * np.pi * hour / 24)
    hour_cos = np.cos(2 * np.pi * hour / 24)
    month_sin = np.sin(2 * np.pi * month / 12)
    month_cos = np.cos(2 * np.pi * month / 12)
    
    # -- Features Meteorológicas (Interpolación Simple) --
    # Calculamos el promedio actual de la ciudad y se lo aplicamos a todo el grid
    # (En V2 haríamos interpolación espacial de temperatura también)
    current_tmp = stations_df['tmp'].mean() if not stations_df['tmp'].isnull().all() else 20
    current_rh = stations_df['rh'].mean() if not stations_df['rh'].isnull().all() else 40
    current_wsp = 0 # Default por ahora
    current_wdr = 0 # Default por ahora
    
    # Asignar al grid
    grid_df['hour_sin'] = hour_sin
    grid_df['hour_cos'] = hour_cos
    grid_df['month_sin'] = month_sin
    grid_df['month_cos'] = month_cos
    grid_df['tmp'] = current_tmp
    grid_df['rh'] = current_rh
    grid_df['wsp'] = current_wsp
    grid_df['wdr'] = current_wdr
    grid_df['station_code'] = -1 # Código dummy para zonas sin estación
    
    # 3. Predecir
    features = ['station_code', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'tmp', 'rh', 'wsp', 'wdr']
    
    print("🔮 Generando predicciones...")
    predictions = model.predict(grid_df[features])
    
    # Limpiar negativos (el modelo a veces predice -1 si está mal calibrado)
    predictions = np.maximum(predictions, 0)
    
    grid_df['value'] = predictions.round(1)
    
    # Output limpio: lat, lon, value
    return grid_df[['lat', 'lon', 'value']]

def lambda_handler(event, context):
    print("🚀 Iniciando Lambda...")
    
    # 1. Cargar Modelo
    model = load_model_from_s3()
    
    # 2. Obtener Datos Reales
    raw_stations = get_live_data()
    if not raw_stations:
        return {'statusCode': 500, 'body': 'Error: API maestra sin datos'}
        
    stations_df = process_stations_data(raw_stations)
    print(f"✅ Datos procesados: {len(stations_df)} estaciones.")
    
    # 3. Generar Grid
    result_grid = interpolate_grid(model, stations_df)
    
    # 4. Guardar en S3 (JSON ligero para el frontend)
    json_output = result_grid.to_json(orient='records')
    
    print(f"💾 Guardando en s3://{S3_BUCKET}/{S3_GRID_OUTPUT_KEY}")
    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=S3_GRID_OUTPUT_KEY,
        Body=json_output,
        ContentType='application/json',
        ACL='public-read' # Opcional para mapa web
    )
    
    return {
        'statusCode': 200,
        'body': json.dumps(f'Grid generado: {len(result_grid)} puntos.')
    }
