import pandas as pd
import xgboost as xgb
import glob
import os
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

# --- CONFIGURACIÓN ---
RAW_DATA_DIR = '/tmp/dataset_final'
STATIONS_FILE = os.path.join(os.path.dirname(__file__), 'raw_data/stationssimat.csv')
TARGETS_TO_TRAIN = ['o3', 'pm10']

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

    pivot_df = full_df.pivot_table(
        index=['date', 'hour', 'station_id'], 
        columns='parameter', 
        values='value'
    ).reset_index()
    
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

def feature_engineering(df, target_name):
    print(f"🛠️ Ingeniería para: {target_name.upper()}...")
    if target_name not in df.columns: return None, None
        
    df_clean = df.dropna(subset=[target_name]).copy()
    df_clean['date'] = pd.to_datetime(df_clean['date'])
    
    df_clean['hour_sin'] = np.sin(2 * np.pi * df_clean['hour'] / 24)
    df_clean['hour_cos'] = np.cos(2 * np.pi * df_clean['hour'] / 24)
    df_clean['month'] = df_clean['date'].dt.month
    df_clean['month_sin'] = np.sin(2 * np.pi * df_clean['month'] / 12)
    df_clean['month_cos'] = np.cos(2 * np.pi * df_clean['month'] / 12)
    
    for col in ['tmp', 'rh', 'wsp', 'wdr']:
        if col in df_clean.columns: df_clean[col] = df_clean[col].fillna(df_clean[col].mean())
        else: df_clean[col] = 0 
    
    if 'altitude' in df_clean.columns: df_clean['altitude'] = df_clean['altitude'].fillna(2240)
            
    df_clean['station_numeric'] = df_clean['station_id'].astype('category').cat.codes
    return df_clean, target_name

def train():
    try:
        master_df = load_and_merge_data()
        
        for target in TARGETS_TO_TRAIN:
            print("="*60)
            print(f"🧠 ENTRENANDO: {target.upper()}")
            
            df_target, target_col = feature_engineering(master_df, target)
            
            if df_target is None or len(df_target) < 100: continue

            POSSIBLE_FEATURES = ['lat', 'lon', 'altitude', 'station_numeric', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'tmp', 'rh', 'wsp', 'wdr']
            FEATURES = [f for f in POSSIBLE_FEATURES if f in df_target.columns]
            
            X = df_target[FEATURES]
            y = df_target[target_col]
            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
            
            model = xgb.XGBRegressor(n_estimators=150, learning_rate=0.05, max_depth=7, n_jobs=-1)
            model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
            
            rmse = np.sqrt(mean_squared_error(y_test, model.predict(X_test)))
            print(f"✅ RMSE: {rmse:.2f}")
            
            output_filename = f"model_{target}.json"
            model.save_model(output_filename)
            print(f"💾 Guardado: {output_filename}")

    except Exception as e:
        print(f"❌ Error crítico: {e}")
        exit(1)

if __name__ == "__main__":
    train()
