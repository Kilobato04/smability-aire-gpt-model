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
MODEL_PATH_CO   = f"{BASE_PATH}/app/artifacts/model_co.json"   # <--- NUEVO
MODEL_PATH_SO2  = f"{BASE_PATH}/app/artifacts/model_so2.json"  # <--- NUEVO
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

    pollutants = ['o3', 'pm10', 'pm25', 'co', 'so2'] 
    
    print(f"🔄 Iniciando carga de modelos: {pollutants}")
    
    for p in pollutants:
        s3_key = f"{MODEL_S3_PREFIX}model_{p}.json"
        local_path = f"/tmp/model_{p}.json"
        
        try:
            # Optimización Cold Start: Solo descargar si no existe en /tmp
            if not os.path.exists(local_path):
                print(f"⬇️ Descargando de S3: {s3_key}...")
                s3_client.download_file(S3_BUCKET, s3_key, local_path)
            
            # Cargar en XGBoost
            m = xgb.XGBRegressor()
            m.load_model(local_path)
            models[p] = m
            print(f"✅ Modelo {p} cargado correctamente.")
        except Exception as e:
            # Si falla (ej. no existe el archivo en S3), avisamos pero no tronamos
            print(f"⚠️ No se pudo cargar el modelo {p}. Razón: {e}")
            
    return models

def inverse_distance_weighting(x, y, z, xi, yi):
    """Interpolación espacial IDW"""
    dist = np.sqrt((xi[:, None] - x[None, :])**2 + (yi[:, None] - y[None, :])**2)
    dist = np.maximum(dist, 1e-12)
    weights = 1.0 / (dist ** 2)
    return np.sum(weights * z[None, :], axis=1) / np.sum(weights, axis=1)

def prepare_grid_features(stations_df):
    """
    Carga malla valle, edificios (con Fix Alias) y colonias.
    """
    print("🏗️ Preparando Grid Features (Alias Strategy)...")
    
    # 1. Cargar Malla Valle (Geometría Base)
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
    
    # 2. Cargar Colonias
    COLONIAS_PATH = f"{BASE_PATH}/app/geograficos/grid_colonias_db.json"
    try:
        with open(COLONIAS_PATH, 'r') as f:
            colonias_data = json.load(f)
        cols_df = pd.DataFrame(colonias_data)
    except Exception as e:
        print(f"⚠️ Error cargando grid_colonias_db.json: {e}")
        cols_df = pd.DataFrame(columns=['lat', 'lon', 'col', 'mun', 'edo', 'pob'])

    # 3. Cargar Edificios (FIX: ALIAS STRATEGY)
    # Renombramos la columna antes del merge para evitar conflictos silenciosos
    with open(f"{BASE_PATH}/app/geograficos/capa_edificios_v2.json", 'r') as f:
        edificios_df = pd.DataFrame(json.load(f))
        
        # --- EL TRUCO DEL ALIAS ---
        if 'building_vol' in edificios_df.columns:
            edificios_df.rename(columns={'building_vol': 'vol_alias'}, inplace=True)
        
    # --- FUSIÓN (MERGE) ESTRATÉGICA ---
    # Llaves de 5 decimales
    grid_df['lat_key'] = grid_df['lat'].round(5)
    grid_df['lon_key'] = grid_df['lon'].round(5)
    
    cols_df['lat_key'] = cols_df['lat'].round(5)
    cols_df['lon_key'] = cols_df['lon'].round(5)
    
    edificios_df['lat_key'] = edificios_df['lat'].round(5)
    edificios_df['lon_key'] = edificios_df['lon'].round(5)

    # A) Pegar Colonias
    grid_df = pd.merge(grid_df, cols_df[['lat_key', 'lon_key', 'col', 'mun', 'edo', 'pob']], 
                       on=['lat_key', 'lon_key'], how='left')
                        
    # B) Pegar Edificios (Usando el Alias 'vol_alias')
    grid_df = pd.merge(grid_df, edificios_df[['lat_key', 'lon_key', 'vol_alias']], 
                       on=['lat_key', 'lon_key'], how='left')

    # --- LIMPIEZA FINAL ---
    grid_df.drop(columns=['lat_key', 'lon_key'], inplace=True)
    
    # Restaurar nombre oficial y limpiar nulos
    # Aquí aseguramos que los datos pasen de vol_alias a building_vol
    grid_df['building_vol'] = grid_df['vol_alias'].fillna(0)
    grid_df.drop(columns=['vol_alias'], inplace=True) # Borrar el alias temporal
    
    # Rellenar vacíos administrativos
    grid_df['col'] = grid_df['col'].fillna("Zona Federal / Sin Colonia")
    grid_df['mun'] = grid_df['mun'].fillna("Valle de México")
    grid_df['edo'] = grid_df['edo'].fillna(np.nan)
    grid_df['pob'] = grid_df['pob'].fillna(0).astype(int)

    # 4. Variables Temporales e Interpolación
    tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(tz)
    grid_df['hour_sin'] = np.sin(2 * np.pi * now.hour / 24)
    grid_df['hour_cos'] = np.cos(2 * np.pi * now.hour / 24)
    grid_df['month_sin'] = np.sin(2 * np.pi * now.month / 12)
    grid_df['month_cos'] = np.cos(2 * np.pi * now.month / 12)

    features_to_interp = [('tmp', 20.0), ('rh', 40.0), ('wsp', 1.0), ('wdr', 90.0)]
    for feat, default in features_to_interp:
        valid = stations_df.dropna(subset=[feat])
        if not valid.empty:
            grid_df[feat] = inverse_distance_weighting(
                valid['lat'].values, valid['lon'].values, valid[feat].values, 
                grid_df['lat'].values, grid_df['lon'].values
            )
        else: grid_df[feat] = default
    
    grid_df['station_numeric'] = -1
    
    # Diagnóstico rápido en logs (para confirmar que funcionó)
    max_vol = grid_df['building_vol'].max()
    print(f"✅ Grid Features Listo. Max Vol Edificios detectado: {max_vol}")
    
    return grid_df

# --- [ANCLA HELPERS: AGREGAR ANTES DE LAMBDA_HANDLER] ---

from scipy.interpolate import griddata # Asegúrate de que este import esté arriba con los demás

def interpolate_grid(grid_df, x_points, y_points, z_values, method='linear'):
    """
    Interpola valores dispersos (x,y,z) sobre la malla completa del grid_df.
    """
    # 1. Coordenadas de destino (Toda la malla)
    grid_coords = (grid_df['lon'], grid_df['lat'])
    
    # 2. Interpolación principal (Linear es mejor para evitar 'islas')
    grid_z = griddata(
        (x_points, y_points), 
        z_values, 
        grid_coords, 
        method=method
    )
    
    # 3. Relleno de bordes (Extrapolación con 'nearest' para cubrir esquinas vacías)
    # Si 'linear' deja huecos (NaNs) en los bordes, los llenamos con el valor más cercano
    if np.isnan(grid_z).any():
        grid_z_nearest = griddata(
            (x_points, y_points), 
            z_values, 
            grid_coords, 
            method='nearest'
        )
        # Donde sea NaN en linear, usamos nearest
        grid_z = np.where(np.isnan(grid_z), grid_z_nearest, grid_z)
        
    return grid_z

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

        # Agregamos TODAS las llaves para evitar errores de contabilidad
        counts = {
            "o3": 0, "pm10": 0, "pm25": 0, 
            "co": 0, "so2": 0, 
            "tmp": 0, "rh": 0, "wsp": 0, "wdr": 0
        }
        parsed = []

        for s in stations_raw:
            if not isinstance(s, dict): continue
            pol = s.get('pollutants') or {}
            met = s.get('meteorological') or {}
            
            # --- FUNCIÓN DE EXTRACCIÓN NORMATIVA ---
            def safe_val(d, key, c_key):
                obj = d.get(key)
                if isinstance(obj, dict):
                    # LÓGICA DE VENTANAS DE TIEMPO (NOM-172)
                    if key == 'co':
                        target_window = 'avg_8h'   # <--- CO usa 8 horas
                    elif key in ['pm10', 'pm25']:
                        target_window = 'avg_12h'  # Partículas usan 12 horas
                    else:
                        target_window = 'avg_1h'   # O3, SO2 y Met usan 1 hora
                    
                    inner = obj.get(target_window)
                    if isinstance(inner, dict):
                        val = inner.get('value')
                        if val is not None:
                            if c_key in counts: counts[c_key] += 1
                            return val
                return None

            parsed.append({
                'name': s.get('station_name', 'Unknown'),
                'lat': float(s.get('latitude')) if s.get('latitude') else None,
                'lon': float(s.get('longitude')) if s.get('longitude') else None,
                'o3_real': safe_val(pol, 'o3', 'o3'),
                'pm10_real': safe_val(pol, 'pm10', 'pm10'),
                'pm25_real': safe_val(pol, 'pm25', 'pm25'),
                # --- NUEVOS CAMPOS (QUÍMICA) ---
                'co_real': safe_val(pol, 'co', 'co'),     
                'so2_real': safe_val(pol, 'so2', 'so2'),  
                # --- METEOROLOGÍA ---
                'tmp': safe_val(met, 'temperature', 'tmp'),
                'rh': safe_val(met, 'relative_humidity', 'rh'),
                'wsp': safe_val(met, 'wind_speed', 'wsp'),     # API key: wind_speed
                'wdr': safe_val(met, 'wind_direction', 'wdr')  # API key: wind_direction
            })
        
        print(f"📊 SALUD API: Recibidas {len(stations_raw)} | O3:{counts['o3']} | TMP:{counts['tmp']}")
        # --- [PROCESAMIENTO ROBUSTO DE STATIONS_DF] ---
        if not parsed:
            print("⚠️ [SISTEMA] 0 estaciones recibidas del SIMAT. Preparando contingencia...")
            stations_df = pd.DataFrame(columns=['name', 'lat', 'lon', 'o3_real', 'pm10_real', 'pm25_real', 'tmp', 'rh', 'wsp'])
        else:
            stations_df = pd.DataFrame(parsed).dropna(subset=['lat', 'lon'])
        
        if 'wdr' not in stations_df.columns:
            stations_df['wdr'] = 90.0

        # Procesamiento de Malla y Predicción
        grid_df = prepare_grid_features(stations_df)

        # --- [BLOQUE C V58.5: METEOROLOGÍA RESILIENTE (SIMAT + 15 PUNTOS VIRTUALES)] ---
        
        # 1. Diagnóstico: ¿Tenemos datos locales reales?
        met_stations = [s for s in stations_raw if s.get('meteorological')]
        count_met = len(met_stations)
        
        # Umbral: Si hay menos de 3 estaciones activas, activamos el Protocolo OpenMeteo
        USE_OPENMETEO = count_met < 3
        
        x_pts, y_pts = [], []
        z_temps, z_rhs, z_wsps, z_wdrs = [], [], [], []

        if not USE_OPENMETEO:
            print(f"\n☀️ MODO DIURNO: Usando {count_met} estaciones SIMAT.")
            # --- ESTRATEGIA A: DATOS REALES (SIMAT) ---
            for s in met_stations:
                met = s['meteorological']
                # Validamos que existan los 4 datos clave (INCLUYENDO WSP)
                if all(k in met for k in ['tmp', 'rh', 'wsp', 'wdr']):
                    x_pts.append(s['location']['lon'])
                    y_pts.append(s['location']['lat'])
                    z_temps.append(float(met['tmp']))
                    z_rhs.append(float(met['rh']))
                    z_wsps.append(float(met['wsp']))
                    z_wdrs.append(float(met['wdr']))
        
        else:
            print(f"\n🌙 MODO NOCTURNO/FALLBACK: Descargando 15 Puntos Virtuales (OpenMeteo)...")
            # --- ESTRATEGIA B: 15 PUNTOS VIRTUALES (OPENMETEO) ---
            lats = [19.5, 19.5, 19.5, 19.4, 19.4, 19.4, 19.3, 19.3, 19.3, 19.2, 19.2, 19.2, 19.6, 19.1, 19.4]
            lons = [-99.2, -99.1, -99.0, -99.2, -99.1, -99.0, -99.2, -99.1, -99.0, -99.2, -99.1, -99.0, -99.1, -99.1, -98.9]
            
            str_lats = ",".join(map(str, lats))
            str_lons = ",".join(map(str, lons))
            om_url = f"https://api.open-meteo.com/v1/forecast?latitude={str_lats}&longitude={str_lons}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m&timezone=auto"
            
            try:
                import urllib.request
                with urllib.request.urlopen(om_url, timeout=3) as url:
                    data = json.loads(url.read().decode())
                    
                    if isinstance(data, list):
                        for i, d in enumerate(data):
                            curr = d.get('current', {})
                            x_pts.append(lons[i])
                            y_pts.append(lats[i])
                            z_temps.append(float(curr.get('temperature_2m', 15)))
                            z_rhs.append(float(curr.get('relative_humidity_2m', 50)))
                            z_wsps.append(float(curr.get('wind_speed_10m', 2)) / 3.6) # Importante: WSP está aquí
                            z_wdrs.append(float(curr.get('wind_direction_10m', 0)))
                    else:
                        # Fallback formato simple
                        curr = data.get('current', {})
                        t_val = curr.get('temperature_2m', 15)
                        if isinstance(t_val, list):
                            z_temps = t_val
                            z_rhs = curr.get('relative_humidity_2m', [50]*15)
                            z_wsps = [v/3.6 for v in curr.get('wind_speed_10m', [7.2]*15)]
                            z_wdrs = curr.get('wind_direction_10m', [0]*15)
                            x_pts = lons
                            y_pts = lats
                        else:
                            x_pts, y_pts = lons, lats
                            z_temps = [float(curr.get('temperature_2m', 15))] * 15
                            z_rhs = [float(curr.get('relative_humidity_2m', 50))] * 15
                            z_wsps = [float(curr.get('wind_speed_10m', 7.2))/3.6] * 15
                            z_wdrs = [float(curr.get('wind_direction_10m', 0))] * 15
                            
                print(f"   ✅ Datos OpenMeteo obtenidos exitosamente ({len(z_temps)} puntos).")
                
            except Exception as e:
                print(f"   ⚠️ Error OpenMeteo: {e}. Usando valores default.")
                x_pts, y_pts = lons, lats
                z_temps = [12.0] * 15
                z_rhs = [60.0] * 15
                z_wsps = [1.0] * 15
                z_wdrs = [0.0] * 15

        # 3. Interpolación Espacial (Sobrescribe grid_df con datos frescos)
        if len(x_pts) >= 3:
            grid_df['tmp'] = interpolate_grid(grid_df, x_pts, y_pts, z_temps, method='linear')
            grid_df['rh']  = interpolate_grid(grid_df, x_pts, y_pts, z_rhs,   method='linear')
            grid_df['wsp'] = interpolate_grid(grid_df, x_pts, y_pts, z_wsps,  method='linear')
            
            # Interpolación vectorial WDR
            u_vec = [-w * np.sin(np.radians(d)) for w, d in zip(z_wsps, z_wdrs)]
            v_vec = [-w * np.cos(np.radians(d)) for w, d in zip(z_wsps, z_wdrs)]
            grid_u = interpolate_grid(grid_df, x_pts, y_pts, u_vec, method='linear')
            grid_v = interpolate_grid(grid_df, x_pts, y_pts, v_vec, method='linear')
            grid_df['wdr'] = (np.degrees(np.arctan2(-grid_u, -grid_v))) % 360
        else:
            grid_df.fillna({'tmp': 15.0, 'rh': 50.0, 'wsp': 1.0, 'wdr': 0.0}, inplace=True)

        grid_df.fillna({'tmp': 15.0, 'rh': 50.0, 'wsp': 1.0, 'wdr': 0.0}, inplace=True)
        # --- [FIN BLOQUE C] ---

        # --- [BLOQUE MAESTRO V58.3: LOGS PREMIUM + CALIBRACIÓN HÍBRIDA + CLEAN TYPES] ---

        # E. Predicción y Calibración
        grid_df['station_numeric'] = -1
        target_pollutants = ['o3', 'pm10', 'pm25', 'co', 'so2']
        
        # Guardrails
        MIN_STATIONS_REG = 4
        SLOPE_MIN = 0.5
        SLOPE_MAX = 1.5
        calib_summary = []

        # Inicialización
        for p in target_pollutants:
            grid_df[p] = 0.0
            grid_df[p] = grid_df[p].astype(float)
        
        feats = [
            'lat', 'lon', 'altitude', 'building_vol', 'station_numeric', 
            'hour_sin', 'hour_cos', 'month_sin', 'month_cos', 
            'tmp', 'rh', 'wsp', 'wdr' 
        ]
        if 'wdr' not in grid_df.columns: grid_df['wdr'] = 90.0

        # --- [NUEVO ENCABEZADO DE LOGS] ---
        print("\n📊 REPORTE DE SALUD DEL SISTEMA (V58.3)")
        print("=" * 70)
        
        # 1. Auditoría de Datos de Entrada (Dinámico)
        # Crea una string bonita con todo lo que traiga counts > 0
        api_details = " | ".join([f"{k.upper()}:{v}" for k, v in counts.items()])
        print(f"📡 API SIMAT    : {len(stations_raw)} Estaciones recibidas")
        print(f"📥 Desglose     : {api_details}")
        
        # 2. Auditoría de Modelos
        loaded_mods = list(models.keys())
        check_icon = "✅" if len(loaded_mods) == 5 else "⚠️"
        print(f"🧠 IA Engine    : {check_icon} {len(loaded_mods)}/5 Modelos cargados {loaded_mods}")
        print("=" * 70 + "\n")

        print(f"🚀 INICIANDO CALIBRACIÓN HÍBRIDA...")

        # --- BUCLE PRINCIPAL ---
        for p in target_pollutants:
            unit = "ppm" if p == "co" else ("ppb" if p in ["o3", "so2"] else "µg/m³")
            
            if p in models:
                # 1. PREDICCIÓN BASE
                # FIX: Convertimos a float64 explícitamente para evitar Warnings de Pandas
                preds = models[p].predict(grid_df[feats]).clip(0)
                grid_df[p] = preds.astype('float64') 
                
                # 2. CALIBRACIÓN INTELIGENTE
                real_col = f'{p}_real'
                m_final, b_final = 1.0, 0.0
                strategy = "N/A"
                r2_score = "N/A"
                
                if real_col in stations_df.columns:
                    v_real_all = stations_df[['name', 'lat', 'lon', real_col]].copy()
                    st_preds = []
                    for _, st in v_real_all.iterrows():
                        dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
                        st_preds.append(grid_df.at[dist.idxmin(), p])
                    v_real_all['raw_ai'] = st_preds
                    
                    v_valid = v_real_all.dropna(subset=[real_col])
                    
                    if not v_valid.empty:
                        X = v_valid['raw_ai'].values
                        Y = v_valid[real_col].values
                        n_points = len(X)
                        
                        # LÓGICA DE DECISIÓN
                        if n_points < MIN_STATIONS_REG:
                            m_final = 1.0
                            b_final = np.mean(Y - X) 
                            strategy = "Solo Bias (Escasez)"
                        else:
                            try:
                                m, b = np.polyfit(X, Y, 1)
                                if m < 0: 
                                    m_final = 1.0
                                    b_final = np.mean(Y - X)
                                    strategy = "Solo Bias (Slope Neg)"
                                else:
                                    m_final = np.clip(m, SLOPE_MIN, SLOPE_MAX)
                                    b_final = np.mean(Y) - (m_final * np.mean(X))
                                    if m != m_final: strategy = "Regresión (Clipped)"
                                    else: strategy = "Regresión Lineal"
                                    
                                    # R2
                                    residuals = Y - ((m_final * X) + b_final)
                                    ss_tot = np.sum((Y - np.mean(Y))**2)
                                    r2 = 1 - (np.sum(residuals**2) / ss_tot) if ss_tot != 0 else 0
                                    r2_score = f"{r2:.2f}"
                            except:
                                m_final = 1.0
                                b_final = np.mean(Y - X)
                                strategy = "Solo Bias (Error)"

                        # APLICAR A MALLA
                        grid_df[p] = (grid_df[p] * m_final) + b_final
                        grid_df[p] = grid_df[p].clip(0)

                        calib_summary.append({
                            'gas': p.upper(), 'n': n_points, 'strat': strategy,
                            'eq': f"y={m_final:.2f}x {'+' if b_final >=0 else ''}{b_final:.2f}",
                            'r2': r2_score
                        })

                        # LOG DETALLADO
                        print(f"\n🔬 DETALLE: {p.upper()} ({strategy}) -> {calib_summary[-1]['eq']}")
                        header = f"{'Estación':<20} | {'IA Raw':<8} | {'Real':<8} | {'Final':<8} | {'Delta':<8}"
                        print("-" * len(header))
                        print(header)
                        print("-" * len(header))
                        
                        mae_raw = np.mean(np.abs(v_valid['raw_ai'] - v_valid[real_col]))
                        mae_cal = 0
                        for _, row in v_valid.iterrows(): 
                            final_val = max(0, (row['raw_ai'] * m_final) + b_final)
                            delta = final_val - row[real_col]
                            mae_cal += abs(delta)
                            print(f"{row['name'][:19]:<20} | {row['raw_ai']:<8.2f} | {row[real_col]:<8.2f} | {final_val:<8.2f} | {delta:<+8.2f}")
                        
                        mae_cal /= len(v_valid)
                        print("-" * len(header))
                        print(f"📉 Mejora MAE: {mae_raw:.2f} -> {mae_cal:.2f} ({unit})")
                        
                    else:
                        calib_summary.append({'gas': p.upper(), 'n': 0, 'strat': 'Sin Datos', 'eq': 'N/A', 'r2': 'N/A'})
                else:
                    calib_summary.append({'gas': p.upper(), 'n': 0, 'strat': 'No Column', 'eq': 'N/A', 'r2': 'N/A'})
            else:
                grid_df[p] = 0.0

        # IMPRIMIR REPORTE EJECUTIVO
        print("\n📋 REPORTE DE CALIBRACIÓN DE RED (V58.3)")
        print("-" * 85)
        print(f"{'Gas':<6} | {'N° Est':<6} | {'Estrategia':<20} | {'Ecuación Aplicada':<25} | {'R²':<5}")
        print("-" * 85)
        for row in calib_summary:
            print(f"{row['gas']:<6} | {row['n']:<6} | {row['strat']:<20} | {row['eq']:<25} | {row['r2']:<5}")
        print("-" * 85)

        # F. Marcadores y Fuentes
        grid_df['station'] = None
        grid_df['sources'] = "{}"

        for _, st in stations_df.iterrows():
            dist = ((grid_df['lat'] - st['lat'])**2 + (grid_df['lon'] - st['lon'])**2)
            idx = dist.idxmin()
            grid_df.at[idx, 'station'] = st['name']
            
            cell_sources = {}
            # Química
            for p in target_pollutants:
                real_val = st.get(f'{p}_real')
                if p == 'co': window = "8h"
                elif p in ['pm10', 'pm25']: window = "12h"
                else: window = "1h"
                
                if pd.notnull(real_val):
                    grid_df.at[idx, p] = float(real_val)
                    cell_sources[p] = f"Oficial {window}"
                else:
                    cell_sources[p] = f"IA {window}"
            
            # Meteorología (Incluyendo WDR)
            for m in ['tmp', 'rh', 'wsp', 'wdr']:
                real_met = st.get(m)
                if pd.notnull(real_met):
                    grid_df.at[idx, m] = float(real_met)
                    cell_sources[m] = "Oficial"
                else:
                    cell_sources[m] = "IA"

            grid_df.at[idx, 'sources'] = json.dumps(cell_sources)

        # G. IAS y Riesgo
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
        
        # H. Exportación
        now_mx = datetime.now(ZoneInfo("America/Mexico_City"))
        str_time = now_mx.strftime("%Y-%m-%d %H:%M:%S")

        final_df = pd.DataFrame()
        final_df['timestamp'] = [str_time] * len(grid_df)
        
        cols_direct = ['lat', 'lon', 'col', 'mun', 'edo', 'pob', 'altitude', 'building_vol', 
                       'tmp', 'rh', 'wsp', 'ias', 'station', 'risk', 'dominant', 'sources']
        for c in cols_direct:
            final_df[c] = grid_df[c]

        final_df['o3 1h']    = grid_df['o3']
        final_df['pm10 12h'] = grid_df['pm10']
        final_df['pm25 12h'] = grid_df['pm25']
        final_df['co 8h']    = grid_df['co']
        final_df['so2 1h']   = grid_df['so2']
        final_df['wdr']      = grid_df['wdr']

        cols_ordered = [
            'timestamp', 'lat', 'lon', 'col', 'mun', 'edo', 'pob', 'altitude', 'building_vol', 
            'tmp', 'rh', 'wsp', 'wdr', 
            'o3 1h', 'pm10 12h', 'pm25 12h', 'co 8h', 'so2 1h', 
            'ias', 'station', 'risk', 'dominant', 'sources'
        ]
        final_df = final_df[cols_ordered]

        # HEALTH CHECK
        print("\n🏥 HEALTH CHECK FINAL (JSON Output):")
        stations_to_check = ["Merced", "Villa de las Flores"]
        for st_name in stations_to_check:
            try:
                row = final_df[final_df['station'].str.contains(st_name, case=False, na=False)]
                if not row.empty:
                    print(f"--- {st_name.upper()} ---")
                    print(row.iloc[0].to_json(indent=2))
                else:
                    print(f"⚠️ {st_name}: No encontrada en el Grid final.")
            except Exception as e:
                print(f"⚠️ Error check {st_name}: {e}")
        print("-" * 30)

        # Guardar
        final_json = final_df.replace({np.nan: None}).to_json(orient='records')
        s3_client.put_object(Bucket=S3_BUCKET, Key=S3_GRID_OUTPUT_KEY, Body=final_json, ContentType='application/json')
        
        timestamp_name = now_mx.strftime("%Y-%m-%d_%H-%M")
        history_key = f"live_grid/grid_{timestamp_name}.json"
        s3_client.put_object(Bucket=S3_BUCKET, Key=history_key, Body=final_json, ContentType='application/json')
        
        print(f"📦 SUCCESS: Grid Generado V58.3 (Logs Premium).")
        
        return {
            'statusCode': 200, 
            'body': json.dumps({'message': 'Grid generado', 'timestamp': timestamp_name}),
            'headers': {'Content-Type': 'application/json'}
        }
        # --- [FIN ANCLA D] ---

    except Exception as e:
        print(f"❌ ERROR FATAL: {e}")
        return {'statusCode': 500, 'body': str(e)}
