import pandas as pd
import xgboost as xgb
import glob
import os
import numpy as np
import zipfile
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

# --- CONFIGURACIÓN ---
# Asegúrate de que el ZIP se llame así y esté en la carpeta 'training'
ZIP_FILE_NAME = 'dataset_aire_cdmx.zip' 
RAW_DATA_DIR = 'raw_data' 
MODEL_OUTPUT_PATH = '../app/model.json'

def check_and_unzip():
    """Revisa si hay CSVs. Si no, busca el ZIP y lo descomprime."""
    
    # 1. Verificar si ya está descomprimido
    if os.path.exists(RAW_DATA_DIR) and glob.glob(os.path.join(RAW_DATA_DIR, "*.csv")):
        print("✅ Archivos CSV detectados en carpeta 'raw_data'.")
        return

    # 2. Buscar el ZIP en la carpeta actual (training/)
    if os.path.exists(ZIP_FILE_NAME):
        print(f"📦 Encontrado '{ZIP_FILE_NAME}'. Descomprimiendo...")
        with zipfile.ZipFile(ZIP_FILE_NAME, 'r') as zip_ref:
            zip_ref.extractall(RAW_DATA_DIR)
        print(f"✅ Descompresión completada en '{RAW_DATA_DIR}'.")
    else:
        # Intento de búsqueda por si acaso está dentro de raw_data
        zip_alt = os.path.join(RAW_DATA_DIR, ZIP_FILE_NAME)
        if os.path.exists(zip_alt):
             print(f"📦 Encontrado '{zip_alt}'. Descomprimiendo...")
             with zipfile.ZipFile(zip_alt, 'r') as zip_ref:
                zip_ref.extractall(RAW_DATA_DIR)
        else:
            print(f"❌ ERROR: No se encuentra '{ZIP_FILE_NAME}' ni CSVs sueltos.")
            print(f"   -> Asegúrate de poner el ZIP dentro de la carpeta 'training/'")
            exit(1)

def load_and_merge_data():
    # Paso 0: Preparar datos
    check_and_unzip()
    
    print("🔄 Cargando y unificando CSVs...")
    all_files = glob.glob(os.path.join(RAW_DATA_DIR, "*.csv"))
    
    if not all_files:
        raise Exception("❌ Carpeta 'raw_data' vacía o sin CSVs.")

    df_list = []
    for filename in all_files:
        # Leemos solo columnas útiles para ahorrar memoria
        try:
            df = pd.read_csv(filename)
            # Estandarizar nombres de columnas por si acaso
            df.columns = [c.lower() for c in df.columns]
            df_list.append(df)
        except Exception as e:
            print(f"⚠️ Error leyendo {filename}: {e}")
    
    if not df_list:
        raise Exception("❌ No se pudo leer ningún archivo CSV.")

    full_df = pd.concat(df_list, ignore_index=True)
    print(f"📊 Total registros crudos cargados: {len(full_df)}")
    
    print("🔄 Pivoteando tabla (convirtiendo filas a columnas)...")
    # Pivot: De [Fecha, Hora, Estacion, Param, Valor] -> [Fecha, Hora, Estacion, PM10, O3...]
    pivot_df = full_df.pivot_table(
        index=['date', 'hour', 'station_id'], 
        columns='parameter', 
        values='value'
    ).reset_index()
    
    print(f"✅ Tabla maestra creada: {len(pivot_df)} registros consolidados.")
    return pivot_df

def feature_engineering(df):
    print("🛠️ Ingeniería de Características...")
    
    # 1. Limpieza del Target (Objetivo)
    # Entrenaremos para predecir PM10 como MVP (el más común)
    TARGET = 'pm10'
    if TARGET not in df.columns:
        # Si falta PM10, intentamos O3
        TARGET = 'o3'
        if TARGET not in df.columns:
             raise Exception("❌ El dataset no tiene datos de PM10 ni O3 para entrenar.")
    
    print(f"🎯 Objetivo del modelo: Predecir {TARGET}")
    df = df.dropna(subset=[TARGET])
    
    # 2. Features Temporales (Ciclos)
    # Convertir fecha
    df['date'] = pd.to_datetime(df['date'])
    
    # Hora del día (Seno/Coseno para continuidad 23h -> 00h)
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    
    # Mes del año (Estacionalidad: lluvias vs secas)
    df['month'] = df['date'].dt.month
    df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12)
    df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12)
    
    # 3. Imputación de Meteorología (Rellenar huecos con promedio)
    meteo_cols = ['tmp', 'rh', 'wsp', 'wdr']
    for col in meteo_cols:
        if col in df.columns:
            df[col] = df[col].fillna(df[col].mean())
        else:
            df[col] = 0 # Si falta la columna entera, asumir neutro
            
    # 4. Encoding de Estaciones
    # Convertir "MER", "PED" a números 0, 1, 2...
    df['station_code'] = df['station_id'].astype('category').cat.codes
    
    return df, TARGET

def train():
    try:
        df = load_and_merge_data()
        df, TARGET = feature_engineering(df)
        
        # DEFINIR VARIABLES DE ENTRADA (FEATURES)
        features_base = [
            'station_code', 
            'hour_sin', 'hour_cos',
            'month_sin', 'month_cos',
            'tmp', 'rh', 'wsp', 'wdr'
        ]
        # Solo usar las que existan realmente en el CSV
        FEATURES = [f for f in features_base if f in df.columns]
        
        print(f"🧠 Entrenando con {len(FEATURES)} variables: {FEATURES}")
        
        X = df[FEATURES]
        y = df[TARGET]
        
        # Separar: 80% para entrenar, 20% para examen final
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        
        # CONFIGURACIÓN DEL CEREBRO (XGBoost)
        model = xgb.XGBRegressor(
            n_estimators=500,      # Número de árboles de decisión
            learning_rate=0.05,    # Velocidad de aprendizaje (lento es mejor)
            max_depth=6,           # Complejidad del árbol
            objective='reg:squarederror',
            n_jobs=-1              # Usar todos los núcleos del CPU
        )
        
        print("⏳ Entrenando... (Esto puede tomar 1-2 minutos)")
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        
        # Evaluar
        predictions = model.predict(X_test)
        rmse = np.sqrt(mean_squared_error(y_test, predictions))
        
        print("-" * 30)
        print(f"✅ ¡ENTRENAMIENTO EXITOSO!")
        print(f"📉 Error Promedio (RMSE): {rmse:.2f} puntos")
        print("-" * 30)
        
        # Guardar modelo
        # Asegurar que la carpeta destino exista
        os.makedirs(os.path.dirname(MODEL_OUTPUT_PATH), exist_ok=True)
        
        model.save_model(MODEL_OUTPUT_PATH)
        print(f"💾 Modelo guardado en: {MODEL_OUTPUT_PATH}")
        print("🚀 ¡Listo para subir a AWS!")
        
    except Exception as e:
        print(f"❌ Error crítico: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    train()
