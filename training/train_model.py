import pandas as pd
import xgboost as xgb
import glob
import os
import json
import numpy as np
import boto3
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

# --- CONFIGURACIÓN ---
RAW_DATA_DIR = '/tmp/dataset_final'
STATIONS_FILE = os.path.join(os.path.dirname(__file__), 'raw_data/stationssimat.csv')

# Busca el archivo de edificios en la raíz (donde Docker lo pone) o relativo
BUILDINGS_FILE = 'capa_edificios_v2.json' 
if not os.path.exists(BUILDINGS_FILE):
    BUILDINGS_FILE = '../capa_edificios_v2.json'

# Configuración S3 (Para subida automática si se requiere, aunque CodeBuild suele extraerlos)
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
S3_MODELS_DIR = 'models/' 

TARGETS_TO_TRAIN = ['o3', 'pm10', 'pm25']

def upload_to_s3(local_file, s3_key):
    """Intenta subir el modelo a S3 (si hay credenciales disponibles)."""
    try:
        s3 = boto3.client('s3')
        print(f"⬆️ Subiendo {local_file} a s3://{S3_BUCKET}/{s3_key}...")
        s3.upload_file(local_file, S3_BUCKET, s3_key)
        print("✅ Subida exitosa.")
    except Exception as e:
        print(f"⚠️ No se pudo subir a S3 desde el script (CodeBuild lo hará): {e}")

def get_station_building_density(stations_df):
    """Asigna densidad urbana a las estaciones de entrenamiento."""
    if not os.path.exists(BUILDINGS_FILE):
        print(f"⚠️ No se encontró {BUILDINGS_FILE}. Usando 0.")
        return np.zeros(len(stations_df))
    
    print(f"🏗️ Cruzando estaciones con {BUILDINGS_FILE}...")
    with open(BUILDINGS_FILE, 'r') as f:
        grid_data = json.load(f)
    
    buildings_df = pd.DataFrame(grid_data)
    grid_lats = buildings_df['lat'].values
    grid_lons = buildings_df['lon'].values
    grid_vols = buildings_df['building_vol'].values
    
    st_vols = []
    for _, row in stations_df.iterrows():
        # Distancia euclidiana rápida (Nearest Neighbor)
        dists = (grid_lats - row['lat'])**2 + (grid_lons - row['lon'])**2
        nearest_idx = np.argmin(dists)
        st_vols.append(grid_vols[nearest_idx])
        
    return np.array(st_vols)

def load_and_merge_data():
    print(f"🔄 Buscando CSVs en {RAW_DATA_DIR}...")
    all_files = glob.glob(os.path.join(RAW_DATA_DIR, "*.csv"))
    
    df_list = []
    for filename in all_files:
        try:
            df = pd.read_csv(filename)
            df.columns = [c.lower() for c in df.columns]
            df_list.append(df)
        except: pass
        
    full_df = pd.concat(df_list, ignore_index=True)
    if 'station_id' in full_df.columns: full_df['station_id'] = full_df['station_id'].astype(str)

    pivot_df = full_df.pivot_table(
        index=['date', 'hour', 'station_id'], 
        columns='parameter', 
        values='value'
    ).reset_index()
    
    if os.path.exists(STATIONS_FILE):
        print(f"🗺️ Cruzando con catálogo de estaciones...")
        stations_df = pd.read_csv(STATIONS_FILE)
        stations_df.columns = [c.lower() for c in stations_df.columns]
        
        if 'station_id' in stations_df.columns: stations_df = stations_df.drop(columns=['station_id'])
        if 'station_code' in stations_df.columns: stations_df = stations_df.rename(columns={'station_code': 'station_id'})
        
        # Inyección de Variable Física: Edificios
        stations_df['building_vol'] = get_station_building_density(stations_df)
        print(f"   Densidad Urbana Promedio: {stations_df['building_vol'].mean():.1f} m3/km2")

        cols_needed = ['station_id', 'lat', 'lon', 'altitude', 'building_vol']
        stations_subset = stations_df[cols_needed].copy()
        stations_subset['station_id'] = stations_subset['station_id'].astype(str).str.strip()
        pivot_df['station_id'] = pivot_df['station_id'].astype(str).str.strip()
        
        pivot_df = pd.merge(pivot_df, stations_subset, on='station_id', how='inner')
    
    return pivot_df

def feature_engineering(df, target):
    if target not in df.columns: return None, None
    df_clean = df.dropna(subset=[target]).copy()
    df_clean['date'] = pd.to_datetime(df_clean['date'])
    
    # Ciclos Temporales
    df_clean['hour_sin'] = np.sin(2 * np.pi * df_clean['hour'] / 24)
    df_clean['hour_cos'] = np.cos(2 * np.pi * df_clean['hour'] / 24)
    df_clean['month'] = df_clean['date'].dt.month
    df_clean['month_sin'] = np.sin(2 * np.pi * df_clean['month'] / 12)
    df_clean['month_cos'] = np.cos(2 * np.pi * df_clean['month'] / 12)
    
    for col in ['tmp', 'rh', 'wsp', 'wdr']:
        if col in df_clean.columns: df_clean[col] = df_clean[col].fillna(df_clean[col].mean())
        else: df_clean[col] = 0 
    
    if 'altitude' in df_clean.columns: df_clean['altitude'] = df_clean['altitude'].fillna(2240)
    if 'building_vol' in df_clean.columns: df_clean['building_vol'] = df_clean['building_vol'].fillna(0)
            
    df_clean['station_numeric'] = df_clean['station_id'].astype('category').cat.codes
    return df_clean, target

def train():
    try:
        master_df = load_and_merge_data()
        
        for target in TARGETS_TO_TRAIN:
            print("="*60)
            print(f"🧠 ENTRENANDO: {target.upper()}")
            
            df_target, target_col = feature_engineering(master_df, target)
            if df_target is None or len(df_target) < 100: continue

            POSSIBLE_FEATURES = [
                'lat', 'lon', 'altitude', 'building_vol', 
                'station_numeric', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 
                'tmp', 'rh', 'wsp', 'wdr'
            ]
            FEATURES = [f for f in POSSIBLE_FEATURES if f in df_target.columns]
            print(f"   Features: {FEATURES}")
            
            X = df_target[FEATURES]
            y = df_target[target_col]
            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
            
            model = xgb.XGBRegressor(n_estimators=150, learning_rate=0.05, max_depth=7, n_jobs=-1)
            model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
            
            rmse = np.sqrt(mean_squared_error(y_test, model.predict(X_test)))
            print(f"✅ RMSE: {rmse:.2f}")
            
            output_filename = f"model_{target}.json"
            model.save_model(output_filename)
            print(f"💾 Guardado local: {output_filename}")
            
            # Intento de subida (Opcional si CodeBuild lo maneja)
            if S3_BUCKET:
                upload_to_s3(output_filename, f"{S3_MODELS_DIR}{output_filename}")

    except Exception as e:
        print(f"❌ Error crítico: {e}")
        exit(1)

if __name__ == "__main__":
    train()
