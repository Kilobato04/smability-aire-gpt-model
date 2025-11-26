import pandas as pd
import xgboost as xgb
import glob
import os
import numpy as np
import random 

# --- CONFIGURACIÓN ---
RAW_DATA_DIR = '/tmp/dataset_final'
MODEL_OUTPUT_PATH = 'model_o3.json' 
TARGET_POLLUTANT = 'o3'

# RUTA EXACTA DEL CATÁLOGO DE ESTACIONES
STATION_CATALOG_PATH = 'training/raw_data/stationssimat.csv' 

# === FUNCIONES REEMPLAZADAS (Para eliminar dependencia de sklearn) ===
def split_data_manual(X, y, test_size=0.2, random_state=42):
    random.seed(random_state)
    indices = np.arange(len(X))
    random.shuffle(indices)
    split_point = int(len(X) * (1 - test_size))
    
    X_train = X.iloc[indices[:split_point]]
    X_test = X.iloc[indices[split_point:]]
    y_train = y.iloc[indices[:split_point]]
    y_test = y.iloc[indices[split_point:]]
    return X_train, X_test, y_train, y_test

def mean_squared_error(y_true, y_pred):
    return np.mean((y_true - y_pred) ** 2)

# === CÓDIGO PRINCIPAL DEL ENTRENAMIENTO ===
def load_and_merge_data():
    print(f"🔄 Buscando archivos CSV en: {RAW_DATA_DIR}")
    # Buscamos de forma recursiva por si el zip contiene carpetas de año
    all_files = glob.glob(os.path.join(RAW_DATA_DIR, '**', "*.csv"), recursive=True)
    
    if not all_files:
        raise Exception(f"❌ No hay archivos en {RAW_DATA_DIR}. Verifique la descompresión.")

    # ... (Proceso de carga y pivotado de datos) ...
    full_df = pd.concat([pd.read_csv(f).rename(columns=str.lower) for f in all_files], ignore_index=True)
    pivot_df = full_df.pivot_table(index=['date', 'hour', 'station_id'], columns='parameter', values='value').reset_index()
    
    print(f"✅ Tabla maestra lista: {len(pivot_df)} filas.")
    return pivot_df

def feature_engineering(df):
    print("🛠️ Ingeniería de Características...")
    
    # === PASO CRÍTICO: MAPEAR COORDENADAS GEOGRÁFICAS (VLOOKUP) ===
    try:
        catalog_df = pd.read_csv(STATION_CATALOG_PATH).rename(columns=str.lower)
        catalog_df = catalog_df.rename(columns={'lon': 'longitude', 'lat': 'latitude'})
        catalog_df = catalog_df[['station_id', 'longitude', 'latitude']].drop_duplicates(subset=['station_id'])
        
        df = pd.merge(df, catalog_df, on='station_id', how='left').rename(columns={'longitude': 'lon', 'latitude': 'lat'})
        df = df.dropna(subset=['lat', 'lon'])
        
    except FileNotFoundError:
        raise Exception(f"❌ ERROR: El catálogo de estaciones no se encuentra en {STATION_CATALOG_PATH}. Verifique la ruta.")
    
    # ... (Creación de features temporales y meteorológicas) ...
    df['date'] = pd.to_datetime(df['date'])
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    df['month_sin'] = np.sin(2 * np.pi * df['date'].dt.month / 12)
    df['month_cos'] = np.cos(2 * np.pi * df['date'].dt.month / 12)
    
    df['station_code'] = df['station_id'].astype('category').cat.codes
    return df

def train_single_model():
    # ... (Resto de la función de entrenamiento) ...
    try:
        df = load_and_merge_data()
        df = feature_engineering(df)
        
        if TARGET_POLLUTANT not in df.columns or df.dropna(subset=[TARGET_POLLUTANT]).empty:
             raise Exception(f"❌ ERROR: Datos no válidos para {TARGET_POLLUTANT}")
            
        X = df[POSSIBLE_FEATURES]
        y = df[TARGET_POLLUTANT]
        
        X_train, X_test, y_train, y_test = split_data_manual(X, y, test_size=0.2, random_state=42)
        model = xgb.XGBRegressor(n_estimators=500, learning_rate=0.05, max_depth=6, n_jobs=-1)
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        
        # GUARDA EN LA RAÍZ DEL CONTENEDOR
        model.save_model(MODEL_OUTPUT_PATH)
        print(f"💾 Modelo guardado en: {MODEL_OUTPUT_PATH}")
            
    except Exception as e:
        print(f"❌ Error crítico: {e}")

if __name__ == "__main__":
    train_single_model()
