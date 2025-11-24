import pandas as pd
import xgboost as xgb
import glob
import os
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

# CONFIGURACIÓN
RAW_DATA_DIR = 'raw_data' # Busca CSVs en la subcarpeta raw_data
MODEL_OUTPUT_PATH = '../app/model.json' # Guarda el modelo en la app de producción

def load_and_merge_data():
    print("🔄 Cargando y unificando CSVs...")
    all_files = glob.glob(os.path.join(RAW_DATA_DIR, "*.csv"))
    
    if not all_files:
        raise Exception("❌ No se encontraron archivos CSV en 'raw_data'. Ejecuta scraper_cdmx.py primero.")

    df_list = []
    for filename in all_files:
        df = pd.read_csv(filename)
        df_list.append(df)
    
    full_df = pd.concat(df_list, ignore_index=True)
    print(f"📊 Total registros crudos: {len(full_df)}")
    
    # Pivotear: Convertir parámetros (filas) a columnas
    print("🔄 Pivoteando tabla (esto puede tomar unos segundos)...")
    pivot_df = full_df.pivot_table(
        index=['date', 'hour', 'station_id'], 
        columns='parameter', 
        values='value'
    ).reset_index()
    
    print(f"✅ Tabla pivoteada: {len(pivot_df)} filas temporales-espaciales.")
    return pivot_df

def feature_engineering(df):
    print("🛠️ Ingeniería de Características...")
    
    # 1. Eliminar filas sin target (PM10 y O3)
    df = df.dropna(subset=['pm10', 'o3']) 
    
    # 2. Features Cíclicas (Hora y Mes)
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    
    df['date'] = pd.to_datetime(df['date'])
    df['month'] = df['date'].dt.month
    df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12)
    df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12)
    
    # 3. Imputar valores meteorológicos faltantes
    meteo_cols = ['tmp', 'rh', 'wsp', 'wdr']
    for col in meteo_cols:
        if col in df.columns:
            df[col] = df[col].fillna(df[col].mean())
        else:
            df[col] = 0
            
    # 4. Codificar Estaciones (Simple Label Encoding)
    df['station_code'] = df['station_id'].astype('category').cat.codes
    
    return df

def train():
    df = load_and_merge_data()
    df = feature_engineering(df)
    
    # Entrenamos para predecir PM10 como MVP
    TARGET = 'pm10' 
    
    FEATURES = [
        'station_code', 
        'hour_sin', 'hour_cos',
        'month_sin', 'month_cos',
        'tmp', 'rh', 'wsp', 'wdr'
    ]
    
    X = df[FEATURES]
    y = df[TARGET]
    
    print(f"🏋️ Entrenando modelo XGBoost para: {TARGET}...")
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    model = xgb.XGBRegressor(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        objective='reg:squarederror',
        n_jobs=-1
    )
    
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    
    predictions = model.predict(X_test)
    rmse = np.sqrt(mean_squared_error(y_test, predictions))
    
    print(f"✅ Entrenamiento finalizado.")
    print(f"📉 RMSE (Error Promedio): {rmse:.2f}")
    
    # Guardar modelo para AWS Lambda
    # Asegurar que el directorio destino exista
    os.makedirs(os.path.dirname(MODEL_OUTPUT_PATH), exist_ok=True)
    model.save_model(MODEL_OUTPUT_PATH)
    print(f"💾 Modelo guardado exitosamente en: {MODEL_OUTPUT_PATH}")

if __name__ == "__main__":
    train()
