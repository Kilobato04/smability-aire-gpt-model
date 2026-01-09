import json
import boto3
from datetime import datetime, timedelta
from statistics import mean

# --- CONFIGURACIÓN ---
BUCKET_NAME = "smability-data-lake" 
FORECAST_PREFIX = "forecast/"
REAL_PREFIX = "live_grid/"
OUTPUT_KEY = "config/calibration_coefficients.json"
ROLLING_WINDOW_DAYS = 7  
LEARNING_RATE = 1.0 # 1.0 = Aprendizaje rápido, 0.5 = Suave

s3 = boto3.client('s3')

def get_s3_json(key):
    try:
        response = s3.get_object(Bucket=BUCKET_NAME, Key=key)
        return json.loads(response['Body'].read().decode('utf-8'))
    except Exception:
        return None

def get_grid_average(grid_data, param):
    if isinstance(grid_data, list): data_list = grid_data
    elif isinstance(grid_data, dict) and 'grid' in grid_data: data_list = grid_data['grid']
    else: return None
    values = [float(item.get(param)) for item in data_list if item.get(param) is not None]
    return sum(values) / len(values) if values else None

def lambda_handler(event, context):
    print(f"🚀 Iniciando Calibración V2 (Con Memoria). Ventana: {ROLLING_WINDOW_DAYS} días.")
    
    # 1. CARGAR MEMORIA (Coeficientes Anteriores)
    old_coeffs = {}
    try:
        old_data = get_s3_json(OUTPUT_KEY)
        if old_data and 'hourly_bias' in old_data:
            old_coeffs = old_data['hourly_bias']
            print("🧠 Memoria cargada exitosamente.")
        else:
            print("⚠️ Memoria vacía. Iniciando calibración desde cero.")
    except Exception as e:
        print(f"⚠️ No se pudo leer memoria: {e}")

    today = datetime.now()
    # Estructura para guardar los errores residuales (lo que le faltó al modelo YA calibrado)
    residual_errors = {h: {'o3': [], 'pm10': [], 'pm25': []} for h in range(24)}
    
    # 2. CALCULAR ERROR RESIDUAL (Barrido de 7 días)
    for i in range(1, ROLLING_WINDOW_DAYS + 1):
        target_date = today - timedelta(days=i)
        date_str = target_date.strftime("%Y-%m-%d")
        
        # Filtros de fechas atípicas
        if target_date.month == 12 and target_date.day in [24, 25, 31]: continue
        elif target_date.month == 1 and target_date.day == 1: continue

        for hour in range(24):
            hour_str = f"{hour:02d}"
            forecast_key = f"{FORECAST_PREFIX}{date_str}_{hour_str}-00.json"
            real_key = f"{REAL_PREFIX}grid_{date_str}_{hour_str}-20.json" 

            f_data = get_s3_json(forecast_key)
            r_data = get_s3_json(real_key)

            if f_data and r_data:
                for pollutant in ['o3', 'pm10', 'pm25']:
                    val_f = get_grid_average(f_data, pollutant)
                    val_r = get_grid_average(r_data, pollutant)
                    
                    if val_f is not None and val_r is not None:
                        # Error = Realidad - Pronóstico
                        # Si el pronóstico ya estaba calibrado, este error debería ser pequeño
                        error = val_r - val_f
                        residual_errors[hour][pollutant].append(error)

    # 3. ACTUALIZAR CONOCIMIENTO (Refinamiento)
    final_bias = {}
    
    for hour in range(24):
        h_key = str(hour)
        final_bias[h_key] = {}
        
        # Recuperamos el conocimiento previo
        prev_o3 = old_coeffs.get(h_key, {}).get('o3', 0.0)
        prev_pm10 = old_coeffs.get(h_key, {}).get('pm10', 0.0)
        prev_pm25 = old_coeffs.get(h_key, {}).get('pm25', 0.0)
        
        # Aplicamos el aprendizaje
        for pol, prev_val in zip(['o3', 'pm10', 'pm25'], [prev_o3, prev_pm10, prev_pm25]):
            errors = residual_errors[hour][pol]
            if errors:
                avg_residual = mean(errors)
                # NUEVO = VIEJO + (ERROR_RESIDUAL * TASA)
                new_val = prev_val + (avg_residual * LEARNING_RATE)
                final_bias[h_key][pol] = round(new_val, 2)
            else:
                # Si no hay datos nuevos, confiamos en la memoria
                final_bias[h_key][pol] = prev_val

    # 4. GUARDAR
    output_json = {
        "generated_at": today.strftime("%Y-%m-%d %H:%M:%S"),
        "window_days": ROLLING_WINDOW_DAYS,
        "version": "V2-Memory",
        "hourly_bias": final_bias
    }
    
    s3.put_object(Bucket=BUCKET_NAME, Key=OUTPUT_KEY, Body=json.dumps(output_json, indent=2), ContentType='application/json')
    
    print(f"✅ Calibración V2 completada y guardada.")
    return {'statusCode': 200, 'body': json.dumps('Calibración V2 OK')}
