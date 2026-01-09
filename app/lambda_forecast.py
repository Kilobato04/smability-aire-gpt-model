import json
import boto3
import os
import pandas as pd
import numpy as np
import xgboost as xgb
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# --- CONFIGURACIÓN ---
BASE_PATH = os.environ.get('LAMBDA_TASK_ROOT', '/var/task')
S3_BUCKET = "smability-data-lake" # Ajusta a tu bucket
s3_client = boto3.client('s3')

# Rutas a los artefactos que ya limpiamos
MODELS = {
    "o3": f"{BASE_PATH}/artifacts/model_o3.json",
    "pm10": f"{BASE_PATH}/artifacts/model_pm10.json",
    "pm25": f"{BASE_PATH}/artifacts/model_pm25.json"
}

def lambda_handler(event, context):
    print("🔮 Iniciando Generación de Forecast 24h...")
    try:
        # 1. Cargar Modelos
        models = {k: xgb.Booster() for k in MODELS}
        for k, path in MODELS.items():
            models[k].load_model(path)

        # 2. Cargar Malla Base (Solo coordenadas y datos estáticos)
        grid_df = pd.read_csv(f"{BASE_PATH}/geograficos/grid_base.csv") 

        # 3. Bucle de 24 Horas
        forecast_results = []
        now = datetime.now(ZoneInfo("America/Mexico_City"))

        for i in range(24):
            target_time = now + timedelta(hours=i)
            # --- AQUÍ VA TU LÓGICA DE CALIBRACIÓN (BIAS VECTOR) ---
            # Adaptamos la entrada para la hora 'target_time'
            
            # Simulacro de predicción (aquí aplicas el models[k].predict)
            prediction = {
                "hour": target_time.strftime("%H:00"),
                "date": target_time.strftime("%Y-%m-%d"),
                "pollutants": {
                    "o3": 45.5, # Resultado de tu lógica de bias
                    "pm10": 30.2,
                    "pm25": 12.8
                },
                "weather": {
                    "tmp": 22.5, # Ajustado de 'temp' a 'tmp'
                    "rh": 45     # Ajustado de 'hum' a 'rh'
                }
            }
            forecast_results.append(prediction)

        # 4. Guardar en S3 (forecast_24h.json)
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key="forecast_24h.json",
            Body=json.dumps(forecast_results),
            ContentType='application/json'
        )

        return {"statusCode": 200, "body": "Forecast 24h generado exitosamente"}

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return {"statusCode": 500, "body": str(e)}
