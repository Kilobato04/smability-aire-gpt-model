import os
# --- FIX PARA MATPLOTLIB EN LAMBDA ---
os.environ['MPLCONFIGDIR'] = '/tmp/matplotlib'
import json
import boto3
import requests
from datetime import datetime, timedelta
import math
import io

# --- NUEVOS IMPORTS PARA GRÁFICAS ---
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches 
import mplcyberpunk
from matplotlib.collections import LineCollection
from matplotlib.colors import ListedColormap, BoundaryNorm
from scipy.interpolate import make_interp_spline
import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer

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
    
    # 1. Leer datos del usuario desde DynamoDB
    response = table.get_item(Key={'user_id': user_id})
    user = response.get('Item')
    if not user or 'locations' not in user or 'casa' not in user['locations']:
        return {"status": "error", "error": "Usuario no tiene casa configurada"}

    locs = user['locations']
    
    # --- LÓGICA DINÁMICA DE TRANSPORTE ---
    transp = user.get('profile_transport', {'medio': 'auto_ventana', 'tiempo_traslado_horas': 2})
    medio_transporte = transp.get('medio', 'transito').upper().replace('_', ' ')
    duracion_traslado = float(transp.get('tiempo_traslado_horas', 2))
    es_ho = (transp.get('medio') == 'home_office')

    if es_ho or 'trabajo' not in locs:
        hora_salida, hora_llegada_casa = 25, 25 # Nunca ocurre en un día de 24h
        hora_llegada_trabajo, hora_salida_trabajo = 25, 25
    else:
        hora_salida = 7  # Sale de casa a las 7 AM
        mitad_traslado = math.ceil(duracion_traslado / 2.0)
        hora_llegada_trabajo = hora_salida + mitad_traslado
        hora_salida_trabajo = 18 # Sale de la oficina a las 6 PM
        hora_llegada_casa = hora_salida_trabajo + mitad_traslado

    # 2. Traer Vectores de API Ligera (CASA)
    lat_c, lon_c = locs['casa']['lat'], locs['casa']['lon']
    resp_c = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_c}&lon={lon_c}", timeout=20).json()
    
    # Traer Vectores (TRABAJO - Si aplica)
    resp_t = resp_c 
    if 'trabajo' in locs and not es_ho:
        lat_t, lon_t = locs['trabajo']['lat'], locs['trabajo']['lon']
        resp_t = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_t}&lon={lon_t}", timeout=20).json()

    # 3. MOTOR DE MEZCLA
    ultima_hora = resp_c.get("metadata_tiempo", {}).get("hoy_ultima_hora", 12)
    
    casa_full = resp_c["vectores"]["ayer"]["ias"] + resp_c["vectores"]["hoy"]["ias"][:ultima_hora+1] + resp_c["vectores"]["futuro"]["ias"]
    trabajo_full = resp_t["vectores"]["ayer"]["ias"] + resp_t["vectores"]["hoy"]["ias"][:ultima_hora+1] + resp_t["vectores"]["futuro"]["ias"]

    ahora_idx = 24 + ultima_hora 
    vector_completo = []
    estados = []
    horas_labels = []

    ts_str = resp_c.get("ts", get_mexico_time().strftime("%Y-%m-%d %H:20:00"))
    base_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(minute=20, second=0)

    for offset in range(-24, 13): 
        i = ahora_idx + offset
        if i >= len(casa_full): i = len(casa_full) - 1 
        h = base_dt.hour + offset
        h_norm = h % 24
        
        # --- APLICACIÓN DE LA RUTINA DEL USUARIO ---
        if es_ho or 'trabajo' not in locs:
            estado = 'casa'
            val = casa_full[i]
        else:
            if h_norm < hora_salida or h_norm >= hora_llegada_casa:
                estado = 'casa'
                val = casa_full[i]
            elif hora_salida <= h_norm < hora_llegada_trabajo:
                estado = 'transito'
                val = max(casa_full[i], trabajo_full[i]) + 15 
            elif hora_llegada_trabajo <= h_norm < hora_salida_trabajo:
                estado = 'trabajo'
                val = trabajo_full[i]
            elif hora_salida_trabajo <= h_norm < hora_llegada_casa:
                estado = 'transito'
                val = max(casa_full[i], trabajo_full[i]) + 15
            else:
                estado = 'casa'
                val = casa_full[i]
            
        vector_completo.append(val)
        estados.append(estado)
        
        point_dt = base_dt + timedelta(hours=offset)
        hora_str = point_dt.strftime("%H:%M")
        
        if offset == 0: 
            horas_labels.append(f"AHORA\n({hora_str})")
        else: 
            horas_labels.append(hora_str)

    # 4. CONFIGURACIÓN VISUAL Y RENDERIZADO
    BOT_URL = "https://t.me/airegptcdmx_bot"
    fecha_str = base_dt.strftime("%d %b %Y • %I:%M %p")

    plt.style.use("cyberpunk")
    fig1, ax1 = plt.subplots(figsize=(10, 14), dpi=120) 
    fig1.patch.set_alpha(0.0) 
    ax1.set_facecolor('none')

    ax_grad = fig1.add_axes([0, 0, 1, 1], zorder=-1)
    gradient = np.linspace(0, 1, 256).reshape(-1, 1)
    cmap_grad = mcolors.LinearSegmentedColormap.from_list("cyber_grad", ['#1a0b2e', '#000000'])
    ax_grad.imshow(gradient, aspect='auto', cmap=cmap_grad, extent=[0, 1, 0, 1])
    ax_grad.axis('off')

    x = np.arange(len(vector_completo))
    y = np.array(vector_completo)
    x_smooth = np.linspace(x.min(), x.max(), 500)
    y_smooth = np.clip(make_interp_spline(x, y, k=3)(x_smooth), 0, None)

    cmap1 = ListedColormap(['#00FF00', '#FFFF00', '#FF9900', '#FF0000', '#CC00FF'])
    norm1 = BoundaryNorm([0, 50, 100, 150, 200, 500], cmap1.N)
    points = np.array([x_smooth, y_smooth]).T.reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)

    mask_past = x_smooth[:-1] <= 24
    lc_past = LineCollection(segments[mask_past], cmap=cmap1, norm=norm1, linewidth=5)
    lc_past.set_array(y_smooth[:-1][mask_past])
    ax1.add_collection(lc_past)

    mask_future = x_smooth[:-1] > 24
    lc_future = LineCollection(segments[mask_future], cmap=cmap1, norm=norm1, linewidth=5, linestyles='dotted')
    lc_future.set_array(y_smooth[:-1][mask_future])
    ax1.add_collection(lc_future)

    # 1. CAJA DEL VALOR MÁXIMO (PICO DE EXPOSICIÓN)
    max_idx = int(np.argmax(vector_completo))
    max_val = vector_completo[max_idx]
    peak_color = '#00FF00' if max_val<=50 else '#FFFF00' if max_val<=100 else '#FF9900' if max_val<=150 else '#FF0000' if max_val<=200 else '#CC00FF'
    
    # Matemáticas para sacar la hora exacta del pico (índice 24 es el centro)
    peak_dt = base_dt + timedelta(hours=(max_idx - 24))
    peak_time_str = peak_dt.strftime("%H:%M")
    
    ax1.annotate(f"{int(max_val)} IAS\n{peak_time_str}", xy=(max_idx, max_val), xytext=(0, 15), textcoords="offset points", ha='center', va='bottom',
                 fontsize=11, fontweight='black', color='black' if max_val <= 100 else 'white',
                 bbox=dict(boxstyle="round,pad=0.4", fc=peak_color, ec="white", lw=2))

    # 2. CAJA DEL VALOR ACTUAL (AHORA)
    # Lógica de UX: Solo la dibujamos si el pico no está ocurriendo AHORA mismo, para no encimar globos.
    if max_idx != 24: 
        current_val = vector_completo[24]
        current_color = '#00FF00' if current_val<=50 else '#FFFF00' if current_val<=100 else '#FF9900' if current_val<=150 else '#FF0000' if current_val<=200 else '#CC00FF'
        
        ax1.annotate(f"{int(current_val)} IAS\nAHORA", xy=(24, current_val), xytext=(0, 15), textcoords="offset points", ha='center', va='bottom',
                     fontsize=10, fontweight='black', color='black' if current_val <= 100 else 'white',
                     bbox=dict(boxstyle="round,pad=0.4", fc=current_color, ec='#08F7FE', lw=2))

    ax1.set_xlim(x_smooth.min(), x_smooth.max())
    ax1.set_ylim(0, max(140, max_val + 30)) 

    legend_patches = [
        mpatches.Patch(facecolor='#CC00FF', edgecolor='white', linewidth=1.5, label='Extremadamente Mala'),
        mpatches.Patch(facecolor='#FF0000', edgecolor='white', linewidth=1.5, label='Muy Mala'),
        mpatches.Patch(facecolor='#FF9900', edgecolor='white', linewidth=1.5, label='Mala'),
        mpatches.Patch(facecolor='#FFFF00', edgecolor='white', linewidth=1.5, label='Regular'),
        mpatches.Patch(facecolor='#00FF00', edgecolor='white', linewidth=1.5, label='Buena')
    ]
    ax1.legend(handles=legend_patches, loc='upper left', facecolor='#1c1c28', edgecolor='#08F7FE', fontsize=10, framealpha=0.8)

    ax1.axhline(y=100, color='red', linestyle='--', alpha=0.3)
    ax1.text(35, 102, "Límite", color='red', alpha=0.6, fontsize=12, ha='right')
    
    # 1. LÍNEA DIVISORIA EN AZUL NEÓN (Pasado vs Futuro)
    azul_neon = '#08F7FE'
    ax1.axvline(x=24, color=azul_neon, linestyle='-', alpha=0.8, linewidth=2)

    ax1.set_xticks(range(0, 37, 4))
    
    # 2. DIBUJAR ETIQUETAS Y CAJA PARA "AHORA"
    etiquetas_x = ax1.set_xticklabels(horas_labels[::4], rotation=0, fontsize=10, color='#aaaaaa', fontweight='bold')
    
    for etiqueta in etiquetas_x:
        if "AHORA" in etiqueta.get_text():
            etiqueta.set_color(azul_neon)
            etiqueta.set_bbox(dict(facecolor='#1c1c28', edgecolor=azul_neon, boxstyle='round,pad=0.4', lw=1.5))

    ax1.set_ylabel("Exposición Personal en IAS", fontsize=14, color='#aaaaaa', fontweight='bold')

    y_base = ax1.get_ylim()[1] * 0.03
    estilos = {
        'casa': dict(facecolor='#1c1c28', edgecolor='#08F7FE', boxstyle='round,pad=0.4', alpha=0.9, lw=1.5),
        'trabajo': dict(facecolor='#1c1c28', edgecolor='#FE53BB', boxstyle='round,pad=0.4', alpha=0.9, lw=1.5),
        'transito': dict(facecolor='#08F7FE', edgecolor='none', boxstyle='round,pad=0.2', alpha=0.8)
    }

    current_state = estados[0]
    start_x = 0

    for x_idx in range(1, len(estados) + 1):
        if x_idx == len(estados) or estados[x_idx] != current_state:
            end_x = x_idx - 1
            mid_x = (start_x + end_x) / 2
            
            if current_state == 'casa':
                ax1.axvspan(start_x, end_x, color='white', alpha=0.02)
                if end_x - start_x >= 2: ax1.text(mid_x, y_base, " CASA ", fontsize=11, fontweight='bold', ha='center', color='white', bbox=estilos['casa'])
            elif current_state == 'trabajo':
                ax1.axvspan(start_x, end_x, color='white', alpha=0.02)
                if end_x - start_x >= 2: ax1.text(mid_x, y_base, " TRABAJO ", fontsize=11, fontweight='bold', ha='center', color='white', bbox=estilos['trabajo'])
            elif current_state == 'transito':
                ax1.axvspan(start_x, end_x, color='#08F7FE', alpha=0.05)
                # Ponemos dinámicamente el medio de transporte del usuario
                ax1.text(mid_x, y_base, medio_transporte, fontsize=9, fontweight='bold', ha='center', va='bottom', color='black', bbox=estilos['transito'], rotation=90)
            
            if x_idx < len(estados):
                current_state = estados[x_idx]
                start_x = x_idx

    fig1.text(0.38, 0.92, "Mi exposición al humo de hoy en CDMX/EDMX\n¡Conoce el tuyo en AIreGPT!", fontsize=16, color='white', ha='center', va='center', fontweight='bold', bbox=dict(facecolor='#1c1c28', edgecolor='#FE53BB', boxstyle='round,pad=1.0', alpha=0.9, lw=2.5), rotation=2)
    
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=1)
    qr.add_data(BOT_URL)
    qr.make(fit=True)
    qr_img = qr.make_image(image_factory=StyledPilImage, module_drawer=RoundedModuleDrawer(radius_ratio=1)).convert("RGBA")
    qr_ax = fig1.add_axes([0.735, 0.78, 0.15, 0.10]) 
    qr_ax.imshow(qr_img)
    qr_ax.axis('off')
    fig1.text(0.5, 0.02, f"{fecha_str} | AIreGPT | Smability.io", color='#888888', fontsize=11, ha='center', va='center')

    mplcyberpunk.add_underglow(ax1, alpha_underglow=0.1)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0, transparent=False, facecolor='#000000', dpi=120)
    buf.seek(0)
    plt.close(fig1) 

    file_name = f"serpiente_{user_id}_{datetime.now().strftime('%H%M%S')}.png"
    url = subir_imagen_a_s3(buf, file_name)

    return {"status": "success", "url": url, "tipo": "serpiente"}

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
