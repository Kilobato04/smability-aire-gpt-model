import json
import boto3
import pandas as pd
import numpy as np
import xgboost as xgb
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os

# --- 1. CONFIGURACIÓN Y RUTAS ---
BASE_PATH = os.environ.get('LAMBDA_TASK_ROOT', '/var/task')
S3_BUCKET = os.environ.get('S3_BUCKET', 'smability-data-lake')
S3_GRID_OUTPUT_KEY = os.environ.get('S3_GRID_OUTPUT_KEY', 'live_grid/latest_grid.json')
# --- CONFIGURACIÓN DE CONTROL MAESTRO ---
# 1.0 = Original | >1.0 = Más limpio (Verde) | <1.0 = Más contaminado (Rojo)
BIAS_SENSITIVITY = 1.0
# --- CONFIGURACIÓN S3 ---
S3_BUCKET = "smability-data-lake"
# Los modelos ahora viven en: models/model_xxx.json
MODEL_S3_PREFIX = "models/"

MODEL_PATH_O3 = f"{BASE_PATH}/app/artifacts/model_o3.json"
MODEL_PATH_PM10 = f"{BASE_PATH}/app/artifacts/model_pm10.json"
MODEL_PATH_PM25 = f"{BASE_PATH}/app/artifacts/model_pm25.json"
STATIC_ADMIN_PATH = f"{BASE_PATH}/app/geograficos/grid_admin_info.json"
SMABILITY_API_URL = "https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference,smaa"

s3_client = boto3.client('s3')

# --- 2. LÓGICA NORMATIVA NOM-172-2024 ---
BPS_O3 = [(0,58,0,50), (59,92,51,100), (93,135,101,150), (136,175,151,200), (176,240,201,300)]
BPS_PM10 = [(0,45,0,50), (46,60,51,100), (61,132,101,150), (133,213,151,200), (214,354,201,300)]
BPS_PM25 = [(0,25,0,50), (26,45,51,100), (46,79,101,150), (80,147,151,200), (148,250,201,300)]

def get_ias_score(c, pollutant):
    try:
        c = float(c)
        bps = BPS_O3 if pollutant == 'o3' else (BPS_PM10 if pollutant == 'pm10' else BPS_PM25)
        for (c_lo, c_hi, i_lo, i_hi) in bps:
            if c <= c_hi: return i_lo + ((c - c_lo) / (c_hi - c_lo)) * (i_hi - i_lo)
        return bps[-1][3]
    except: return 0

def get_risk_level(ias):
    if ias <= 50: return "Bajo"
    if ias <= 100: return "Moderado"
    if ias <= 150: return "Alto"
    if ias <= 200: return "Muy Alto"
    return "Extremadamente Alto"

# --- 3. FUNCIONES DE CARGA Y PROCESAMIENTO ---
def load_models():
    """Descarga modelos desde S3 y los carga en XGBoost"""
    models = {}
    pollutants = ['o3', 'pm10', 'pm25']
    
    for p in pollutants:
        s3_key = f"{MODEL_S3_PREFIX}model_{p}.json"
        local_path = f"/tmp/model_{p}.json"
        
        try:
            print(f"descargando de S3: {s3_key}...")
            s3_client.download_file(S3_BUCKET, s3_key, local_path)
            
            m = xgb.XGBRegressor()
            m.load_model(local_path)
            models[p] = m
            print(f"✅ Modelo {p} cargado desde S3.")
        except Exception as e:
            print(f"⚠️ No se pudo cargar el modelo {p} desde S3: {e}")
            
    return models

def inverse_distance_weighting(x, y, z, xi, yi):
    """Interpolación espacial IDW"""
    dist = np.sqrt((xi[:, None] - x[None, :])**2 + (yi[:, None] - y[None, :])**2)
    dist = np.maximum(dist, 1e-12)
    weights = 1.0 / (dist ** 2)
    return np.sum(weights * z[None, :], axis=1) / np.sum(weights, axis=1)

def prepare_grid_features(stations_df):
    """Carga malla valle, edificios y datos administrativos para una fusión total"""
    # 1. Cargar Malla Valle (Geometría y Elevación)
    MALLA_PATH = f"{BASE_PATH}/app/geograficos/malla_valle_mexico_final.geojson"
    with open(MALLA_PATH, 'r') as f:
        malla_data = json.load(f)
    
    malla_list = []
    for feature in malla_data['features']:
        coords = feature['geometry']['coordinates']
        malla_list.append({
            'lon': round(coords[0], 5),
            'lat': round(coords[1], 5),
            'altitude': feature['properties'].get('elevation', 2240)
        })
    grid_df = pd.DataFrame(malla_list)
    
# --- AJUSTE SIMPLIFICADO: COINCIDENCIA TOTAL GEOGRÁFICA ---
    # 1. Cargar Edificios
    with open(f"{BASE_PATH}/app/geograficos/capa_edificios_v2.json", 'r') as f:
        edificios_df = pd.DataFrame(json.load(f))
    
    # 2. Cargar Administración
    with open(f"{BASE_PATH}/app/geograficos/grid_admin_info.json", 'r') as f:
        admin_df = pd.DataFrame(json.load(f))

    # Creamos llaves de cruce con 2 decimales para máxima compatibilidad
    # Esto asegura que no queden celdas en gris (building_vol = 0)
    grid_df['lat_key'] = grid_df['lat'].round(2)
    grid_df['lon_key'] = grid_df['lon'].round(2)
    
    for df_temp in [edificios_df, admin_df]:
        df_temp['lat_key'] = df_temp['lat'].round(2)
        df_temp['lon_key'] = df_temp['lon'].round(2)
        df_temp.drop_duplicates(subset=['lat_key', 'lon_key'], inplace=True)

    # Fusionamos Edificios
    grid_df = pd.merge(grid_df, edificios_df[['lat_key', 'lon_key', 'building_vol']], 
                       on=['lat_key', 'lon_key'], how='left')

    # Fusionamos Administración
    grid_df = pd.merge(grid_df, admin_df[['lat_key', 'lon_key', 'mun', 'edo']], 
                       on=['lat_key', 'lon_key'], how='left')

    # Limpieza: Borramos llaves y llenamos nulos
    grid_df.drop(columns=['lat_key', 'lon_key'], inplace=True)
    grid_df['building_vol'] = grid_df['building_vol'].fillna(0)
    grid_df['mun'] = grid_df['mun'].fillna("Valle de México")
    grid_df['edo'] = grid_df['edo'].fillna("Edomex/CDMX")
    # --- FIN DEL REEMPLAZO ---

    # 4. Variables Temporales e Interpolación
    tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(tz)
    grid_df['hour_sin'] = np.sin(2 * np.pi * now.hour / 24)
    grid_df['hour_cos'] = np.cos(2 * np.pi * now.hour / 24)
    grid_df['month_sin'] = np.sin(2 * np.pi * now.month / 12)
    grid_df['month_cos'] = np.cos(2 * np.pi * now.month / 12)

    for feat, default in [('tmp', 20.0), ('rh', 40.0), ('wsp', 1.0)]:
        valid = stations_df.dropna(subset=[feat])
        if not valid.empty:
            grid_df[feat] = inverse_distance_weighting(
                valid['lat'].values, valid['lon'].values, valid[feat].values, 
                grid_df['lat'].values, grid_df['lon'].values
            )
        else: grid_df[feat] = default
    
    grid_df['station_numeric'] = -1
    return grid_df

# --- 4. HANDLER PRINCIPAL ---
def lambda_handler(event, context):
    VERSION = "V56.9" 
    print(f"🚀 INICIANDO PREDICTOR MAESTRO {VERSION} - ESTABILIZACIÓN FINAL")
    
    try:
        models = load_models()
        
        # Ingesta de API con Blindaje
        stations_raw = []
        try:
            r = requests.get(SMABILITY_API_URL, timeout=15)
            if r.status_code == 200:
                res = r.json()
                stations_raw = res.get('stations') if isinstance(res, dict) else []
        except Exception as e:
            print(f"⚠️ API Error: {e}")

        counts = {"o3": 0, "pm10": 0, "pm25": 0, "tmp": 0, "rh": 0, "wsp": 0}
        parsed = []

        for s in stations_raw:
            if not isinstance(s, dict): continue
            pol = s.get('pollutants') or {}
            met = s.get('meteorological') or {}
            
# --- INICIO DEL REEMPLAZO (AJUSTE TEMPORAL PMs) ---
            def safe_val(d, key, c_key):
                obj = d.get(key)
                if isinstance(obj, dict):
                    # Si es PM10 o PM25, usamos el promedio móvil de 12h de la norma
                    # Para O3 y Meteorología, seguimos usando el avg_1h
                    target_window = 'avg_12h' if key in ['pm10', 'pm25'] else 'avg_1h'
                    
                    inner = obj.get(target_window)
                    if isinstance(inner, dict):
                        val = inner.get('value')
                        if val is not None:
                            counts[c_key] += 1
                            return val
                return None
            # --- FIN DEL REEMPLAZO ---

            parsed.append({
                'name': s.get('station_name', 'Unknown'),
                'lat': float(s.get('latitude')) if s.get('latitude') else None,
                'lon': float(s.get('longitude')) if s.get('longitude') else None,
                'o3_real': safe_val(pol, 'o3', 'o3'),
                'pm10_real': safe_val(pol, 'pm10', 'pm10'),
                'pm25_real': safe_val(pol, 'pm25', 'pm25'),
                'tmp': safe_val(met, 'temperature', 'tmp'),
                'rh': safe_val(met, 'relative_humidity', 'rh'),
                'wsp': safe_val(met, 'wind_speed', 'wsp')
            })
        
        print(f"📊 SALUD API: Recibidas {len(stations_raw)} | O3:{counts['o3']} | TMP:{counts['tmp']}")
        # --- [PROCESAMIENTO ROBUSTO DE STATIONS_DF] ---
        if not parsed:
            print("⚠️ [SISTEMA] 0 estaciones recibidas del SIMAT. Preparando contingencia...")
            stations_df = pd.DataFrame(columns=['name', 'lat', 'lon', 'o3_real', 'pm10_real', 'pm25_real', 'tmp', 'rh', 'wsp'])
        else:
            stations_df = pd.DataFrame(parsed).dropna(subset=['lat', 'lon'])

        # --- [LÓGICA DE FUENTE DE DATOS Y RESCATE] ---
        if stations_df.empty or stations_df[['o3_real', 'pm10_real', 'pm25_real']].isnull().all().all():
            print("🌐 [OPEN-METEO] Intentando rescate de malla climática (20 puntos)...")
            
            pts = [(19.43,-99.13),(19.54,-99.20),(19.60,-99.04),(19.63,-99.10),(19.28,-99.17),
                   (19.25,-99.10),(19.19,-99.02),(19.36,-99.07),(19.40,-98.99),(19.42,-98.94),
                   (19.36,-99.26),(19.47,-99.23),(19.36,-99.35),(19.64,-98.91),(19.67,-99.18),
                   (19.26,-98.89),(19.30,-99.24),(19.49,-99.11),(19.35,-99.16),(19.42,-99.09)]
            
            lats = ",".join([str(p[0]) for p in pts])
            lons = ",".join([str(p[1]) for p in pts])
            url_om = f"https://api.open-meteo.com/v1/forecast?latitude={lats}&longitude={lons}&current=temperature_2m,relative_humidity_2m,wind_speed_10m&wind_speed_unit=ms&timezone=America%2FMexico_City"
            
            try:
                res = requests.get(url_om, timeout=10).json()
                
                # Manejo de respuesta (Lista vs Objeto único)
                if isinstance(res, list):
                    avg_tmp = np.mean([p['current']['temperature_2m'] for p in res])
                    avg_rh = np.mean([p['current']['relative_humidity_2m'] for p in res])
                    avg_wsp = np.mean([p['current']['wind_speed_10m'] for p in res])
                else:
                    avg_tmp = res['current']['temperature_2m']
                    avg_rh = res['current']['relative_humidity_2m']
                    avg_wsp = res['current']['wind_speed_10m']

                stations_df = pd.DataFrame([{
                    'name': 'Inercia Climática', 'lat': 19.43, 'lon': -99.13,
                    'o3_real': None, 'pm10_real': None, 'pm25_real': None,
                    'tmp': avg_tmp, 'rh': avg_rh, 'wsp': avg_wsp
                }])
                print(f"✅ [DATOS: OPEN-METEO] Rescate exitoso. Clima promedio: {avg_tmp:.1f}°C, RH: {avg_rh:.0f}%")
                
            except Exception as e:
                print(f"🚨 [ERROR: OPEN-METEO] Falló la API externa: {e}")
                print("🛠️ [DATOS: REGISTRO MANUAL] Aplicando valores failsafe de emergencia.")
                stations_df = pd.DataFrame([{
                    'name': 'Failsafe de Emergencia', 
                    'lat': 19.43, 'lon': -99.13, 
                    'o3_real': None, 'pm10_real': None, 'pm25_real': None,
                    'tmp': 18.0, 'rh': 45.0, 'wsp': 2.5
                }])
        else:
            print(f"📡 [DATOS: SIMAT] Utilizando {len(stations_df)} estaciones oficiales.")

        # --- [FIN DEL BLOQUE DE RESCATE] ---

        # Procesamiento de Malla y Predicción
        grid_df = prepare_grid_features(stations_df)
        
        # --- INICIO DEL REEMPLAZO (SECCIÓN E y F) ---
        # E. Predicción y Calibración
        grid_df['station_numeric'] = -1
        
        # 1. Aseguramos tipos de datos y pre-inicializamos
        for p in ['o3', 'pm10', 'pm25']:
            grid_df[p] = 0.0
            grid_df[p] = grid_df[p].astype(float)
        
        feats = ['lat', 'lon', 'altitude', 'building_vol', 'station_numeric', 'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'tmp', 'rh', 'wsp']
        
        # 2. Bucle de predicción y calibración (Alineación corregida)
        for p in ['o3', 'pm10', 'pm25']:
            if p in models:
                # 1. Predicción base de la IA
                grid_df[p] = models[p].predict(grid_df[feats]).clip(0)
                
                # 2. Preparar datos para la Tabla de Auditoría
                real_col = f'{p}_real'
                v_real_all = stations_df[['name', 'lat', 'lon', real_col]].copy()
                
                # Comparativa: ¿Qué dijo la IA en la ubicación de la estación?
                st_preds = []
                for _, st in v_real_all.iterrows():
                    dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
                    idx_closest = dist.idxmin()
                    st_preds.append(grid_df.at[idx_closest, p])
                v_real_all['raw_ai'] = st_preds
                
                # 3. Cálculo del Bias
                v_valid = v_real_all.dropna(subset=[real_col])
                applied_bias = 0  # <--- RED DE SEGURIDAD: Definir por defecto
                
                if not v_valid.empty:
                    # Cálculo del bias base
                    raw_bias = v_valid[real_col].mean() - v_valid['raw_ai'].mean()
                    
                    # APLICACIÓN DE LA PERILLA DE SENSIBILIDAD
                    applied_bias = raw_bias * BIAS_SENSITIVITY
                    
                    grid_df[p] = (grid_df[p] + applied_bias).clip(0)
                    print(f"DEBUG: {p.upper()} - Bias Original: {raw_bias:+.2f} | Aplicado (x{BIAS_SENSITIVITY}): {applied_bias:+.2f}")
                
                # 4. REPORTE VISUAL EN LOGS (TABLA)
                unit = "ppb" if p == "o3" else "µg/m³"
                
                # Reportamos claramente qué factor de sensibilidad se usó
                print(f"\n📊 TABLA DE CALIBRACIÓN: {p.upper()} (Bias Aplicado: {applied_bias:+.2f} {unit} | Sensibilidad: x{BIAS_SENSITIVITY})")
                header = f"{'Estación':<25} | {'Raw AI':<10} | {'Real':<10} | {'Bias':<10} | {'Final':<10}"
                print("-" * len(header))
                print(header)
                print("-" * len(header))
                
                for _, row in v_real_all.iterrows():
                    real_val = f"{row[real_col]:.2f}" if pd.notnull(row[real_col]) else "N/A"
                    
                    # El valor final en la tabla refleja la IA + el bias aplicado
                    final_val_in_table = row['raw_ai'] + applied_bias
                    
                    # Una sola línea de print limpia
                    print(f"{row['name'][:24]:<25} | {row['raw_ai']:<10.2f} | {real_val:<10} | {applied_bias:<+10.2f} | {max(0, final_val_in_table):<10.2f}")
                
                print("-" * len(header))
            else:
                grid_df[p] = 0.0

        # --- [INICIO REEMPLAZO SECCIÓN F: ETIQUETADO DE FUENTES] ---
        # F. Marcadores y Sobrescritura de Estaciones (Con Trazabilidad)
        grid_df['station'] = None
        grid_df['sources'] = "{}" # Inicializamos columna para guardar el origen del dato

        for _, st in stations_df.iterrows():
            # Encontrar la celda más cercana a la estación real
            dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
            idx = dist.idxmin()
            
            grid_df.at[idx, 'station'] = st['name']
            
            # Diccionario para rastrear fuente por contaminante
            cell_sources = {}
            
            for p in ['o3', 'pm10', 'pm25']:
                real_val = st.get(f'{p}_real')
                
                # Definimos la ventana de tiempo según el contaminante (Normativa)
                window = "12h" if p in ['pm10', 'pm25'] else "1h"
                
                if pd.notnull(real_val):
                    # CASO 1: HAY DATO DE SENSOR
                    # Sobrescribimos la predicción de la IA con el dato duro
                    grid_df.at[idx, p] = float(real_val)
                    cell_sources[p] = f"Oficial {window}"
                else:
                    # CASO 2: NO HAY DATO DE SENSOR (Gap)
                    # Mantenemos el valor que la IA predijo (ya calculado en Sección E)
                    cell_sources[p] = f"IA {window}"
            
            # Guardamos el diccionario como string JSON para que pase al CSV/JSON final
            grid_df.at[idx, 'sources'] = json.dumps(cell_sources)
        # --- [FIN REEMPLAZO SECCIÓN F] ---

        # G. Cálculo de IAS y Dominante (NOM-172-2024)
        def calc_ias_row(row):
            scores = {
                'O3': get_ias_score(row['o3'], 'o3'), 
                'PM10': get_ias_score(row['pm10'], 'pm10'), 
                'PM2.5': get_ias_score(row['pm25'], 'pm25')
            }
            dom_pol = max(scores, key=scores.get)
            return pd.Series([max(scores.values()), dom_pol])

        grid_df[['ias', 'dominant']] = grid_df.apply(calc_ias_row, axis=1)
        grid_df['risk'] = grid_df['ias'].apply(get_risk_level)
        
        now_mx = datetime.now(ZoneInfo("America/Mexico_City"))
        grid_df['timestamp'] = now_mx.strftime("%Y-%m-%d %H:%M:%S")

        # H. Exportación Final a S3
        cols = ['timestamp', 'lat', 'lon', 'mun', 'edo', 'altitude', 'building_vol', 
                'tmp', 'rh', 'wsp', 'o3', 'pm10', 'pm25', 'ias', 'station', 'risk', 'dominant', 'sources']
        
        print("🔍 [DEBUG] Iniciando transformación de columnas...")

        # 1. Renombrar columnas en un NUEVO DataFrame (final_df)
        final_df = grid_df[cols].rename(columns={
            'o3': 'o3 1h',
            'pm10': 'pm10 12h',
            'pm25': 'pm25 12h'
        })
        
        # 2. LOG DE VERIFICACIÓN (Esto debe salir en CloudWatch)
        print(f"🔍 [DEBUG] Columnas Finales: {final_df.columns.tolist()}")

        # 3. Generar JSON usando FINAL_DF (¡Aquí estaba el error!)
        final_json = final_df.replace({np.nan: None}).to_json(orient='records')
        
        # 4. Guardar en S3 (Latest)
        s3_client.put_object(
            Bucket=S3_BUCKET, 
            Key=S3_GRID_OUTPUT_KEY, 
            Body=final_json, 
            ContentType='application/json'
        )

        # 5. Guardar en S3 (Histórico)
        timestamp_name = now_mx.strftime("%Y-%m-%d_%H-%M")
        history_key = f"live_grid/grid_{timestamp_name}.json"
        
        s3_client.put_object(
            Bucket=S3_BUCKET, 
            Key=history_key, 
            Body=final_json, 
            ContentType='application/json'
        )
        
        print(f"📦 SUCCESS {VERSION}: Guardado Latest y Histórico ({history_key})")
        return {
            'statusCode': 200, 
            'body': final_json, # Regresamos el JSON nuevo para verlo en el Test de consola
            'headers': {'Content-Type': 'application/json'}
        }
        # --- FIN DEL REEMPLAZO ---

    except Exception as e:
        print(f"❌ ERROR FATAL: {e}")
        return {'statusCode': 500, 'body': str(e)}
