import pandas as pd
import xgboost as xgb
import glob
import os
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

# --- CONFIGURACIÓN ---
# Leemos de la carpeta temporal donde unimos todo (2023+2024+2025)
RAW_DATA_DIR = '/tmp/dataset_final' 
MODEL_OUTPUT_PATH = 'app/model.json'

def load_and_merge_data():
    print(f"🔄 Buscando archivos CSV en: {RAW_DATA_DIR}")
    
    # Buscar CSVs
    all_files = glob.glob(os.path.join(RAW_DATA_DIR, "*.csv"))
    
    if not all_files:
        raise Exception(f"❌ No hay archivos en {RAW_DATA_DIR}. Verifica que hayas descomprimido el ZIP.")

    print(f"📂 Encontrados {len(all_files)} archivos. Cargando...")
    
    df_list = []
    for filename in all_files:
        try:
            df = pd.read_csv(filename)
            # Normalizar nombres de columnas a minúsculas
            df.columns = [c.lower() for c in df.columns]
            df_list.append(df)
        except Exception as e:
            print(f"⚠️ Error leyendo {filename}: {e}")

    if not df_list:
        raise Exception("❌ No se pudo cargar ningún dato.")

    full_df = pd.concat(df_list, ignore_index=True)
    print(f"📊 Total registros crudos: {len(full_df)}")
    
    print("🔄 Pivoteando tabla (esto toma unos segundos)...")
    # Pivotear: [Fecha, Hora, Estacion, Param, Valor] -> [Cols: PM10, O3...]
    pivot_df = full_df.pivot_table(
        index=['date', 'hour', 'station_id'], 
        columns='parameter', 
        values='value'
    ).reset_index()
    
    print(f"✅ Tabla maestra lista: {len(pivot_df)} filas.")
    return pivot_df

def feature_engineering(df):
    print("🛠️ Ingeniería de Características...")
    
    # 1. Definir Target (PM10)
    TARGET = 'pm10'
    if TARGET not in df.columns:
        # Fallback a O3 si no hay PM10
        if 'o3' in df.columns: 
            TARGET = 'o3'
            print("⚠️ No encontré PM10, entrenaré con O3.")
        else: 
            raise Exception("❌ El dataset no tiene PM10 ni O3.")
    
    # Eliminar filas donde no sepamos la respuesta (target nulo)
    df = df.dropna(subset=[TARGET])
    
    # 2. Features Temporales (Ciclos)
    df['date'] = pd.to_datetime(df['date'])
    
    # Hora (Seno/Coseno)
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    
    # Mes (Estacionalidad)
    df['month'] = df['date'].dt.month
    df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12)
    df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12)
    
    # 3. Imputar Meteorología (Rellenar huecos con media)
    meteo_cols = ['tmp', 'rh', 'wsp', 'wdr']
    for col in meteo_cols:
        if col in df.columns:
            df[col] = df[col].fillna(df[col].mean())
        else:
            df[col] = 0
            
    # 4. Encoding de Estaciones (Texto -> Número)
    df['station_code'] = df['station_id'].astype('category').cat.codes
    
    return df, TARGET

def train():
    try:
        # Asegurar carpeta de salida
        os.makedirs(os.path.dirname(MODEL_OUTPUT_PATH), exist_ok=True)
        
        df = load_and_merge_data()
        df, TARGET = feature_engineering(df)
        
        # Variables que usará el modelo
        POSSIBLE_FEATURES = [
            'station_code', 
            'hour_sin', 'hour_cos', 
            'month_sin', 'month_cos', 
            'tmp', 'rh', 'wsp', 'wdr'
        ]
        # Solo usar las que existan en los datos descargados
        FEATURES = [f for f in POSSIBLE_FEATURES if f in df.columns]
        
        print(f"🧠 Entrenando modelo para predecir '{TARGET}' con: {FEATURES}")
        
        X = df[FEATURES]
        y = df[TARGET]
        
        # Split 80/20
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        
        # Configuración XGBoost
        model = xgb.XGBRegressor(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            n_jobs=-1
        )
        
        print("⏳ Entrenando... (patience please)")
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        
        # Evaluar
        preds = model.predict(X_test)
        rmse = np.sqrt(mean_squared_error(y_test, preds))
        
        print("-" * 30)
        print(f"✅ ¡ENTRENAMIENTO EXITOSO!")
        print(f"📉 Error Promedio (RMSE): {rmse:.2f}")
        print("-" * 30)
        
        model.save_model(MODEL_OUTPUT_PATH)
        print(f"💾 Modelo guardado en: {MODEL_OUTPUT_PATH}")
        
    except Exception as e:
        print(f"❌ Error crítico: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    train()
