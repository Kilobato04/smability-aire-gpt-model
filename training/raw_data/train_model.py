import pandas as pd
import xgboost as xgb
import glob
import os
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

# --- CONFIGURACIÓN ---
RAW_DATA_DIR = '/tmp/dataset_final'
# El modelo único se guardará en la carpeta 'app/'
MODEL_OUTPUT_PATH = 'app/model_o3.json' 
TARGET_POLLUTANT = 'o3'

# RUTA EXACTA DEL CATÁLOGO DE ESTACIONES
STATION_CATALOG_PATH = 'training/raw_data/stationssimat.csv' 

def load_and_merge_data():
    """Carga y pivotea todos los CSVs en un único DataFrame."""
    print(f"🔄 Buscando archivos CSV en: {RAW_DATA_DIR}")
    
    all_files = glob.glob(os.path.join(RAW_DATA_DIR, "*.csv"))
    
    if not all_files:
        raise Exception(f"❌ No hay archivos en {RAW_DATA_DIR}. Verifica que hayas descomprimido el ZIP.")

    print(f"📂 Encontrados {len(all_files)} archivos. Cargando...")
    
    df_list = []
    for filename in all_files:
        try:
            df = pd.read_csv(filename)
            df.columns = [c.lower() for c in df.columns]
            df_list.append(df)
        except Exception as e:
            print(f"⚠️ Error leyendo {filename}: {e}")

    if not df_list:
        raise Exception("❌ No se pudo cargar ningún dato.")

    full_df = pd.concat(df_list, ignore_index=True)
    print(f"📊 Total registros crudos: {len(full_df)}")
    
    print("🔄 Pivoteando tabla...")
    pivot_df = full_df.pivot_table(
        index=['date', 'hour', 'station_id'], 
        columns='parameter', 
        values='value'
    ).reset_index()
    
    print(f"✅ Tabla maestra lista: {len(pivot_df)} filas.")
    return pivot_df

def feature_engineering(df):
    """Añade variables temporales, rellena meteorología y MAPEA COORDENADAS."""
    print("🛠️ Ingeniería de Características...")
    
    # === PASO CRÍTICO: MAPEAR COORDENADAS GEOGRÁFICAS (VLOOKUP) ===
    try:
        catalog_df = pd.read_csv(STATION_CATALOG_PATH)
        catalog_df.columns = [c.lower() for c in catalog_df.columns]
        
        # 1. Renombrar y seleccionar columnas CLAVE del catálogo
        catalog_df = catalog_df.rename(columns={'station_id': 'station_id', 'lon': 'longitude', 'lat': 'latitude', 'alt': 'altitude'})
        catalog_df = catalog_df[['station_id', 'longitude', 'latitude']].drop_duplicates(subset=['station_id'])
        
        print(f"🗺️ Mapeando coordenadas Lat/Lon a cada medición histórica (Merge de {len(df)} filas)...")
        
        # 2. Merge (VLOOKUP) usando 'station_id'
        df = pd.merge(
            df, 
            catalog_df, 
            on='station_id', 
            how='left'
        )
        
        # 3. Renombrar las columnas mapeadas a 'lat' y 'lon' para consistencia en el modelo
        df = df.rename(columns={'longitude': 'lon', 'latitude': 'lat'})
        
        # 4. Limpiar filas donde no se pudo encontrar la coordenada
        df = df.dropna(subset=['lat', 'lon'])
        
    except FileNotFoundError:
        raise Exception(f"❌ ERROR: El catálogo de estaciones no se encuentra en {STATION_CATALOG_PATH}. Verifique la ruta y el nombre del archivo.")
    
    # 1. Features Temporales (Ciclos)
    df['date'] = pd.to_datetime(df['date'])
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    df['month'] = df['date'].dt.month
    df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12)
    df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12)
    
    # 2. Imputar Meteorología
    meteo_cols = ['tmp', 'rh', 'wsp', 'wdr']
    for col in meteo_cols:
        if col in df.columns:
            df[col] = df[col].fillna(df[col].mean())
        else:
            df[col] = 0
            
    # 3. Encoding de Estaciones (Texto -> Número)
    df['station_code'] = df['station_id'].astype('category').cat.codes
    
    return df

def train_single_model():
    """Entrena y guarda un único modelo para el TARGET_POLLUTANT definido."""
    try:
        os.makedirs(os.path.dirname(MODEL_OUTPUT_PATH), exist_ok=True)
        
        df = load_and_merge_data()
        df = feature_engineering(df)
        
        # VALIDAR TARGET
        if TARGET_POLLUTANT not in df.columns:
            raise Exception(f"❌ ERROR: El contaminante objetivo '{TARGET_POLLUTANT}' no se encuentra en los datos.")

        # DEFINICIÓN DE FEATURES (Ahora incluye lat y lon)
        POSSIBLE_FEATURES = [
            'lat', 'lon', 
            'station_code', 
            'hour_sin', 'hour_cos', 
            'month_sin', 'month_cos', 
            'tmp', 'rh', 'wsp', 'wdr'
        ]
        
        FEATURES = [f for f in POSSIBLE_FEATURES if f in df.columns]
        
        print(f"\n=====================================")
        print(f"🧠 INICIANDO ENTRENAMIENTO: {TARGET_POLLUTANT.upper()}")
        print(f"=====================================")
        
        # Filtrar solo las filas con valores para el TARGET
        df_target = df.dropna(subset=[TARGET_POLLUTANT]).copy()
        
        if df_target.empty:
            raise Exception(f"❌ ERROR: No hay datos válidos para {TARGET_POLLUTANT} después de la limpieza.")
            
        X = df_target[FEATURES]
        y = df_target[TARGET_POLLUTANT]
        
        # Split 80/20
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        
        # Configuración XGBoost
        model = xgb.XGBRegressor(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            n_jobs=-1
        )
        
        print("⏳ Entrenando...")
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        
        # Evaluar
        preds = model.predict(X_test)
        rmse = np.sqrt(mean_squared_error(y_test, preds))
        
        print(f"✅ ENTRENAMIENTO EXITOSO para {TARGET_POLLUTANT.upper()}")
        print(f"📉 Error Promedio (RMSE): {rmse:.2f}")
        
        model.save_model(MODEL_OUTPUT_PATH)
        print(f"💾 Modelo guardado en: {MODEL_OUTPUT_PATH}")
            
    except Exception as e:
        print(f"❌ Error crítico: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    train_single_model()
