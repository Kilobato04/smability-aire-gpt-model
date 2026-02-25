import os
import json
import boto3
import requests
from datetime import datetime, timedelta
import io

# Importamos tu clase matemática
from calculos import CalculadoraRiesgoSmability

# --- CONFIGURACIÓN ---
# Nombres de recursos AWS (Asegúrate que coincidan con los tuyos)
DYNAMODB_TABLE = 'SmabilityUsers'
# IMPORTANTE: Reemplaza esto con el nombre real de tu bucket S3 para gráficas temporales
S3_BUCKET = 'smability-graficas-temp' 
API_LIGHT_URL = os.environ.get('API_LIGHT_URL', 'https://vuy3dprsp2udtuelnrb5leg6ay0ygsky.lambda-url.us-east-1.on.aws/')

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)
s3_client = boto3.client('s3')

def get_mexico_time():
    # Ajuste simple a CDMX (UTC-6). En horario de verano podría variar.
    return datetime.utcnow() - timedelta(hours=6)

# ========================================================
# 🌙 MÓDULO 1: EL BATCH NOCTURNO (Persistencia de Datos)
# ========================================================
def ejecutar_job_nocturno():
    print("🌙 Iniciando Batch Nocturno de Exposición...")
    
    hoy_mx = get_mexico_time()
    ayer = hoy_mx - timedelta(days=1)
    fecha_str = ayer.strftime("%Y-%m-%d")
    # 6 es Domingo en Python (lunes=0, ... domingo=6)
    es_domingo = ayer.weekday() == 6

    print(f"Procesando fecha: {fecha_str}, ¿Es corte semanal?: {es_domingo}")

    # En producción con miles de usuarios, usar paginación. Para empezar, scan está bien.
    response = table.scan(ProjectionExpression="user_id, locations, profile_transport, health_stats")
    usuarios = response.get('Items', [])
    
    procesados = 0
    errores = 0
    
    for user in usuarios:
        user_id = user.get('user_id')
        locs = user.get('locations', {})
        transp = user.get('profile_transport', {'medio': 'auto_ventana', 'horas': 2})
        
        # Saltamos a los que no han configurado su casa
        if not isinstance(locs, dict) or 'casa' not in locs: continue
            
        try:
            # 1. API Call Casa
            lat_c, lon_c = locs['casa']['lat'], locs['casa']['lon']
            resp_c = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_c}&lon={lon_c}", timeout=5).json()
            vector_c = resp_c.get("vectores", {}).get("ayer")
            
            # 2. API Call Trabajo (Si aplica)
            vector_t = None
            es_ho = (transp.get('medio') == 'home_office')
            if 'trabajo' in locs and not es_ho:
                lat_t, lon_t = locs['trabajo']['lat'], locs['trabajo']['lon']
                resp_t = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_t}&lon={lon_t}", timeout=5).json()
                vector_t = resp_t.get("vectores", {}).get("ayer")

            # 3. Calcular si hay datos
            if vector_c:
                calc = CalculadoraRiesgoSmability()
                res = calc.calcular_usuario(vector_c, transp, vector_t, es_ho)
                
                # Usamos Decimal o Strings para DynamoDB para evitar problemas de float
                cigarros_val = res['cigarros']
                dias_edad_val = res['dias_perdidos']

                dato_diario = {
                    "fecha": fecha_str,
                    "cigarros": str(cigarros_val),          
                    "dias_edad": str(dias_edad_val),
                    "promedio_pm25": str(res['promedio_riesgo'])
                }

                # 4. Lógica de DynamoDB (Semana vs Histórico)
                health_stats = user.get('health_stats')

                if es_domingo:
                    # Sumamos la semana acumulada + el día de hoy (domingo)
                    current_week = health_stats.get('current_week', []) if health_stats else []
                    historical_weeks = health_stats.get('historical_weeks', []) if health_stats else []
                    
                    total_cigarros = sum(float(dia.get('cigarros', 0)) for dia in current_week) + cigarros_val
                    total_dias_edad = sum(float(dia.get('dias_edad', 0)) for dia in current_week) + dias_edad_val
                    
                    resumen_semanal = {
                        "fecha_cierre": fecha_str,
                        "cigarros_totales": str(round(total_cigarros, 1)),
                        "dias_edad_totales": str(round(total_dias_edad, 1))
                    }
                    historical_weeks.append(resumen_semanal)
                    
                    if not health_stats:
                        # Si es domingo y es su primer día usando la app
                        table.update_item(
                            Key={'user_id': user_id},
                            UpdateExpression="SET health_stats = :hs",
                            ExpressionAttributeValues={
                                ':hs': {'current_week': [], 'historical_weeks': historical_weeks}
                            }
                        )
                    else:
                        table.update_item(
                            Key={'user_id': user_id},
                            UpdateExpression="SET health_stats.historical_weeks = :hist, health_stats.current_week = :empty",
                            ExpressionAttributeValues={':hist': historical_weeks, ':empty': []}
                        )
                    print(f"✅ [CIERRE SEMANAL] Usuario {user_id}: {round(total_cigarros,1)} cigs totales.")
                    
                else:
                    # Lunes a Sábado
                    if not health_stats:
                        # El usuario es completamente nuevo en esta métrica, creamos el esqueleto
                        table.update_item(
                            Key={'user_id': user_id},
                            UpdateExpression="SET health_stats = :hs",
                            ExpressionAttributeValues={
                                ':hs': {'current_week': [dato_diario], 'historical_weeks': []}
                            }
                        )
                    else:
                        # El usuario ya tiene health_stats, solo agregamos al arreglo current_week
                        table.update_item(
                            Key={'user_id': user_id},
                            UpdateExpression="SET health_stats.current_week = list_append(if_not_exists(health_stats.current_week, :empty_list), :new_day)",
                            ExpressionAttributeValues={':empty_list': [], ':new_day': [dato_diario]}
                        )
                    print(f"✅ [DIARIO] Usuario {user_id}: {cigarros_val} cigs.")
                
                procesados += 1
            else:
                 print(f"⚠️ Usuario {user_id}: Sin datos vectoriales de ayer.")
                
        except Exception as e:
            print(f"❌ Error Job Nocturno {user_id}: {e}")
            errores += 1

    return {"status": "success", "message": f"Batch finalizado. Procesados: {procesados}, Errores: {errores}"}


# ========================================================
# 🎨 MÓDULO 2: GENERADOR DE GRÁFICAS (Matplotlib)
# ========================================================
def subir_imagen_a_s3(buffer, file_name):
    """Sube el buffer PNG a S3 y retorna la URL pública"""
    # TODO: Asegurarse que el bucket S3 tenga políticas públicas para la carpeta graficas_temp/
    s3_key = f"graficas_temp/{file_name}"
    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=buffer.getvalue(),
        ContentType='image/png',
        CacheControl='max-age=3600' 
    )
    # Construimos la URL virtual-hosted style
    return f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key}"

def generar_grafica_serpiente(user_id):
    print(f"🐍 Generando SERPIENTE para {user_id}...")
    # ---> AQUÍ METEREMOS EL CÓDIGO MATPLOTLIB DEL COLAB DE SERPIENTE <---
    # 1. Leer vectores de API Ligera (ayer, hoy, mañana)
    # 2. Generar plot en buffer
    # 3. url = subir_imagen_a_s3(buffer, f"serpiente_{user_id}.png")
    return {"status": "success", "url": "https://placehold.co/600x400/png", "tipo": "serpiente_placeholder"}

def generar_grafica_tetris(user_id):
    print(f"🧱 Generando TETRIS para {user_id}...")
    # ---> AQUÍ METEREMOS EL CÓDIGO MATPLOTLIB DEL COLAB DE TETRIS <---
    # 1. Leer DynamoDB (health_stats.current_week)
    # 2. Generar plot en buffer
    # 3. url = subir_imagen_a_s3(buffer, f"tetris_{user_id}.png")
    return {"status": "success", "url": "https://placehold.co/400x600/png", "tipo": "tetris_placeholder"}


# ========================================================
# 🔀 ENRUTADOR PRINCIPAL (LAMBDA HANDLER)
# ========================================================
def lambda_handler(event, context):
    print("Recibiendo evento:", json.dumps(event)[:200]) # Log parcial para debug
    try:
        # 1. ¿Me llamó EventBridge (Cron)?
        if event.get("source") == "aws.events" or event.get("detail-type") == "Scheduled Event":
            res = ejecutar_job_nocturno()
            return res

        # 2. ¿Me llamó el Bot (HTTP API Gateway)?
        # Los parámetros pueden venir en queryStringParameters (GET) o body (POST)
        params = event.get("queryStringParameters", {})
        if not params and event.get("body"):
            try:
                params = json.loads(event.get("body"))
            except:
                pass
        
        action = params.get("action")
        user_id = params.get("user_id")

        if not action or not user_id:
            return {"statusCode": 400, "body": json.dumps({"error": "Faltan parametros action o user_id"})}

        if action == "tetris":
            res = generar_grafica_tetris(user_id)
            # Retornamos estructura API Gateway response
            return {
                "statusCode": 200, 
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(res)
            }
            
        elif action == "serpiente":
            res = generar_grafica_serpiente(user_id)
            return {
                "statusCode": 200, 
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(res)
            }

        return {"statusCode": 400, "body": json.dumps({"error": f"Acción '{action}' no válida"})}

    except Exception as e:
        print(f"🔥 Error Crítico en Lambda: {str(e)}")
        # Retornar error 500 en formato API Gateway
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
