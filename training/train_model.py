import pandas as pd
import xgboost as xgb
import glob
import os
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

# --- CONFIGURACIÓN ---
RAW_DATA_DIR = '/tmp/dataset_final'
MODEL_OUTPUT_PATH = 'model_o3.json' 
STATIONS_FILE = os.path.join(os.path.dirname(__file__), 'raw_data/stationssimat.csv')

def load_and_merge_data():
    print(f"🔄 Buscando archivos CSV en: {RAW_DATA_DIR}")
    all_files = glob.glob(os.path.join(RAW_DATA_DIR, "*.csv"))
    
    if not all_files: raise Exception(f"❌ No hay archivos CSV.")

    df_list = []
    for filename in all_files:
        try:
            df = pd.read_csv(filename)
            df.columns = [c.lower() for c in df.columns]
            df_list.append(df)
        except Exception as e: print(f"⚠️ Error leyendo {filename}: {e}")

    full_df = pd.concat(df_list, ignore_index=True)
    if 'station_id' in full_df.columns: full_df['station_id'] = full_df['station_id'].astype(str)

    print("🔄 Pivoteando tabla...")
    pivot_df = full_df.pivot_table(
        index=['date', 'hour', 'station_id'], 
        columns='parameter', 
        values='value'
    ).reset_index()
    
    # Merge con Catálogo (Lat/Lon/Altitud)
    if os.path.exists(STATIONS_FILE):
        print(f"🗺️ Cruzando con catálogo: {STATIONS_FILE}")
        stations_df = pd.read_csv(STATIONS_FILE)
        stations_df.columns = [c.lower() for c in stations_df.columns]
        
        if 'station_id' in stations_df.columns: stations_df = stations_df.drop(columns=['station_id'])
        if 'station_code' in stations_df.columns: stations_df = stations_df.rename(columns={'station_code': 'station_id'})
        
        cols_needed = ['station_id', 'lat', 'lon', 'altitude']
        stations_subset = stations_df[cols_needed].copy()
        stations_subset['station_id'] = stations_subset['station_id'].astype(str).str.strip()
        pivot_df['station_id'] = pivot_df['station_id'].astype(str).str.strip()
        
        pivot_df = pd.merge(pivot_df, stations_subset, on='station_id', how='inner')
    
    return pivot_df

def feature_engineering(df):
    print("🛠️ Ingeniería de Características...")
    TARGET = 'o3'
    if TARGET not in df.columns: raise Exception("Falta Target O3")
    df = df.dropna(subset=[TARGET])
    df['date'] = pd.to_datetime(df['date'])
    
    # Ciclos
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    df['month'] = df['date'].dt.month
    df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12)
    df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12)
    
    # Rellenar nulos
    for col in ['tmp', 'rh', 'wsp', 'wdr']:
        if col in df.columns: df[col] = df[col].fillna(df[col].mean())
        else: df[col] = 0 
    
    if 'altitude' in df.columns: df['altitude'] = df['altitude'].fillna(2240)
            
    df['station_numeric'] = df['station_id'].astype('category').cat.codes
    return df, TARGET

def train():
    try:
        df = load_and_merge_data()
        df, TARGET = feature_engineering(df)
        
        # FEATURES COMPLETOS
        POSSIBLE_FEATURES = [
            'lat', 'lon', 'altitude',
            'station_numeric',       
            'hour_sin', 'hour_cos',  
            'month_sin', 'month_cos',
            'tmp', 'rh', 'wsp', 'wdr'
        ]
        FEATURES = [f for f in POSSIBLE_FEATURES if f in df.columns]
        print(f"🧠 Entrenando con: {FEATURES}")
        
        X = df[FEATURES]
        y = df[TARGET]
        
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        
        model = xgb.XGBRegressor(n_estimators=100, learning_rate=0.05, n_jobs=-1)
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        
        rmse = np.sqrt(mean_squared_error(y_test, model.predict(X_test)))
        print(f"✅ RMSE Final: {rmse:.2f}")
        model.save_model(MODEL_OUTPUT_PATH)
        
    except Exception as e:
        print(f"❌ Error crítico: {e}")
        exit(1)

if __name__ == "__main__":
    train()
