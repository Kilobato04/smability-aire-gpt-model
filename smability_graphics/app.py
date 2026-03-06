import os
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
        # 1. Traemos el objeto (con valores por defecto)
        transp = user.get('profile_transport', {'medio': 'auto_ventana', 'horas': 2})
        
        # 2. Normalizamos la duración (buscamos en ambas llaves posibles)
        duracion = float(transp.get('tiempo_traslado_horas', transp.get('horas', 2)))
        
        # 3. Aseguramos que la llave que usa la Calculadora esté presente
        transp['tiempo_traslado_horas'] = duracion
        
        # Saltamos a los que no han configurado su casa
        if not isinstance(locs, dict) or 'casa' not in locs: continue
            
        try:
            # 1. API Call Casa
            lat_c, lon_c = locs['casa']['lat'], locs['casa']['lon']
            resp_c = requests.get(f"{API_LIGHT_URL}?mode=live&lat={lat_c}&lon={lon_c}", timeout=5).json()
            vector_c = resp_c.get("vectores", {}).get("ayer")

            # --- 🎯 FIX: IDENTIFICACIÓN DINÁMICA DEL DESTINO ---
            # Buscamos la llave que no es casa, priorizando 'is_destination'
            dest_key = next((k for k, v in locs.items() if v.get('is_destination')), None)
            if not dest_key:
                dest_key = next((k for k in locs.keys() if k != 'casa'), None)
            
            # 2. API Call Trabajo (Si aplica)
            vector_t = None
            es_ho = (transp.get('medio') == 'home_office')

            if dest_key and not es_ho:
                lat_t, lon_t = locs[dest_key]['lat'], locs[dest_key]['lon']
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

    # --- 🎯 IDENTIFICACIÓN DINÁMICA DEL DESTINO ---
    # Buscamos la primera ubicación que NO sea 'casa'
    # Priorizamos la que el usuario haya marcado como destino principal
    # 1. Identificamos la llave (técnica) del destino
    destino_key = next((k for k, v in locs.items() if v.get('is_destination')), None)
    
    # 2. Si no hay marcada, tomamos la primera que no sea casa
    if not destino_key:
        destino_key = next((k for k in locs.keys() if k != 'casa'), None)

    # 3. Asignación del nombre visual (El que se dibuja en la gráfica)
    if destino_key and destino_key in locs:
        # Priorizamos display_name, si no existe usamos la llave, si no "DESTINO"
        raw_name = locs[destino_key].get('display_name', destino_key)
        nombre_destino_visual = str(raw_name).upper()
    else:
        # Este es el 'else' que mencionas por si no encuentra absolutamente nada
        nombre_destino_visual = "DESTINO"
    
    # --- LÓGICA DINÁMICA DE TRANSPORTE ---
    transp = user.get('profile_transport', {'medio': 'auto_ventana', 'tiempo_traslado_horas': 2})
    medio_transporte = transp.get('medio', 'transito').upper().replace('_', ' ')
    duracion_traslado = float(transp.get('tiempo_traslado_horas', transp.get('horas', 2)))
    es_ho = (transp.get('medio') == 'home_office')

    if es_ho or not destino_key:
        hora_salida, hora_llegada_casa = 25, 25 # Se queda en casa
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

    # 2b. Traer Vectores (DESTINO DETECTADO)
    resp_t = resp_c 
    if destino_key and not es_ho: # <--- Usar destino_key en lugar de 'trabajo'
        lat_t, lon_t = locs[destino_key]['lat'], locs[destino_key]['lon']
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

    print(f"DEBUG_SMABILITY: Destino: {destino_key} | Nombre: {nombre_destino_visual}")
    print(f"DEBUG_HORARIOS: Sale: {hora_salida} | Llega: {hora_llegada_trabajo} | Regresa: {hora_llegada_casa}")
    
    for offset in range(-24, 13): 
        i = ahora_idx + offset
        if i >= len(casa_full): i = len(casa_full) - 1 
        h = base_dt.hour + offset
        h_norm = h % 24
        
        # --- APLICACIÓN DE LA RUTINA DEL USUARIO (FIX DESTINO FLEXIBLE) ---
        # Si es Home Office o NO hay ningún destino configurado (ni trabajo ni otro)
        if es_ho or not destino_key:
            estado = 'casa'
            val = casa_full[i]
        else:
            # El usuario sí tiene un destino (ej. IBERO) y no es HO
            if h_norm < hora_salida or h_norm >= hora_llegada_casa:
                estado = 'casa'
                val = casa_full[i]
            elif hora_salida <= h_norm < hora_llegada_trabajo:
                estado = 'transito'
                # Usamos trabajo_full que ya contiene los datos del destino_key
                val = max(casa_full[i], trabajo_full[i]) + 15 
            elif hora_llegada_trabajo <= h_norm < hora_salida_trabajo:
                estado = 'trabajo' # Se queda como llave técnica para el color rosa
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
                if end_x - start_x >= 1: 
                    # Usamos la variable 'nombre_destino_visual' que calculamos al inicio
                    ax1.text(mid_x, y_base, f" {nombre_destino_visual} ", fontsize=11, fontweight='bold', ha='center', color='white', bbox=estilos['trabajo'])
            elif current_state == 'transito':
                ax1.axvspan(start_x, end_x, color='#08F7FE', alpha=0.05)
                # Ponemos dinámicamente el medio de transporte del usuario
                ax1.text(mid_x, y_base, medio_transporte, fontsize=9, fontweight='bold', ha='center', va='bottom', color='black', bbox=estilos['transito'], rotation=90)
            
            if x_idx < len(estados):
                current_state = estados[x_idx]
                start_x = x_idx

    fig1.text(0.38, 0.92, "Mi exposición al humo de hoy en CDMX/EDOMX\n¡Conoce el tuyo en AIreGPT!", fontsize=16, color='white', ha='center', va='center', fontweight='bold', bbox=dict(facecolor='#1c1c28', edgecolor='#FE53BB', boxstyle='round,pad=1.0', alpha=0.9, lw=2.5), rotation=2)
    
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=1)
    qr.add_data(BOT_URL)
    qr.make(fit=True)
    qr_img = qr.make_image(image_factory=StyledPilImage, module_drawer=RoundedModuleDrawer(radius_ratio=1)).convert("RGBA")
    qr_ax = fig1.add_axes([0.74, 0.86, 0.13, 0.13]) 
    qr_ax.imshow(qr_img)
    qr_ax.axis('off')
    # --- NUEVO: MARCAS DE AGUA REDES SOCIALES (Badge Inferior Izquierdo) ---
    fig1.text(0.04, 0.055, "IG: @airegpt.ai | TikTok: @airegpt", 
              color='#aaaaaa', fontsize=10, ha='left', va='center', fontname='monospace', fontweight='bold',
              bbox=dict(facecolor='#1c1c28', edgecolor='#08F7FE', boxstyle='round,pad=0.5', alpha=0.9, lw=1.5))
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
    try:
        # ==========================================
        # 1. LEER DATOS DE DYNAMODB
        # ==========================================
        response = table.get_item(Key={'user_id': str(user_id)})
        user = response.get('Item', {})

        health_stats = user.get('health_stats', {})
        current_week = health_stats.get('current_week', [])
        historical_weeks = health_stats.get('historical_weeks', [])
        all_logs = current_week + historical_weeks

        # ==========================================
        # 2. PROCESAMIENTO MATEMÁTICO
        # ==========================================
        now_mx = get_mexico_time()
        semanas_totales = 52
        semanas_transcurridas = now_mx.isocalendar()[1]

        cigarros_float = np.zeros(semanas_totales)
        ias_suma = np.zeros(semanas_totales)
        dias_con_datos = np.zeros(semanas_totales)

        for log in all_logs:
            # 1. Identificar si es un registro diario o un resumen histórico
            es_historico = 'fecha_cierre' in log
            fecha_str = log.get('fecha_cierre') if es_historico else log.get('fecha')
            
            if not fecha_str: continue
            
            dt = datetime.strptime(fecha_str, "%Y-%m-%d")
            if dt.year != now_mx.year: continue # Filtro año en curso
            
            w_idx = dt.isocalendar()[1] - 1 # Índice de array (0 a 51)
            if w_idx < 0 or w_idx >= 52: continue
            
            # 2. Sumar cigarros dependiendo del tipo de registro
            if es_historico:
                cigarros_float[w_idx] += float(log.get('cigarros_totales', 0))
                # (El IAS histórico no lo necesitamos sumar aquí, la tarjeta mensual ya se congeló)
            else:
                cigarros_float[w_idx] += float(log.get('cigarros', 0))
                
                # El IAS solo lo promediamos de los días activos (current_week)
                pm25 = float(log.get('promedio_pm25', 0))
                ias_val = float(log.get('promedio_ias', pm25 * 4)) 
                ias_suma[w_idx] += ias_val
                dias_con_datos[w_idx] += 1

        # --- FIX: Mantener 1 decimal exacto sin redondear al entero ---
        cigarros_semana = np.round(cigarros_float, 1) 
        total_cigarros_ytd = round(np.sum(cigarros_semana), 1)
        anios_edad_urbana = round((total_cigarros_ytd * 2.0) / 365.0, 2)

        idx_inicio = max(0, semanas_transcurridas - 4)
        suma_ias_mes = np.sum(ias_suma[idx_inicio:semanas_transcurridas])
        suma_dias_mes = np.sum(dias_con_datos[idx_inicio:semanas_transcurridas])
        promedio_ias_mes = int(suma_ias_mes / suma_dias_mes) if suma_dias_mes > 0 else 0

        # ==========================================
        # 3. HELPERS DE DISEÑO (Anidados)
        # ==========================================
        def generar_qr_eje(fig, x_pos=0.72):
            qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=1)
            qr.add_data("https://t.me/airegptcdmx_bot")
            qr.make(fit=True)
            qr_img = qr.make_image(image_factory=StyledPilImage, module_drawer=RoundedModuleDrawer(radius_ratio=1)).convert("RGBA")
            qr_ax = fig.add_axes([x_pos, 0.88, 0.15, 0.10]) 
            qr_ax.imshow(qr_img)
            qr_ax.axis('off')

        def obtener_color_cigarros(cantidad):
            if cantidad < 15: return '#08F7FE'      # Cyan (Bien)
            elif cantidad < 25: return '#00FF00'    # Verde (Regular)
            elif cantidad < 35: return '#FFFF00'    # Amarillo (Malo)
            elif cantidad < 45: return '#FF7F00'    # Naranja (Muy Malo)
            else: return '#FF00FF'                  # Magenta (Peligro)

        def dibujar_bloque_tetris(ax, x, y, color, is_empty=False):
            if is_empty:
                ax.add_patch(mpatches.Rectangle((x+0.1, y+0.1), 0.8, 0.8, fill=False, edgecolor='#333333', lw=1, ls=':'))
                return
            ax.add_patch(mpatches.Rectangle((x+0.02, y+0.02), 0.96, 0.96, facecolor='black'))
            ax.add_patch(mpatches.Rectangle((x+0.05, y+0.05), 0.9, 0.9, facecolor=color))
            ax.add_patch(mpatches.Polygon([(x+0.05, y+0.95), (x+0.95, y+0.95), (x+0.8, y+0.8), (x+0.2, y+0.8)], facecolor='white', alpha=0.4))
            ax.add_patch(mpatches.Polygon([(x+0.05, y+0.05), (x+0.05, y+0.95), (x+0.2, y+0.8), (x+0.2, y+0.2)], facecolor='white', alpha=0.2))
            ax.add_patch(mpatches.Polygon([(x+0.05, y+0.05), (x+0.95, y+0.05), (x+0.8, y+0.2), (x+0.2, y+0.2)], facecolor='black', alpha=0.3))
            ax.add_patch(mpatches.Polygon([(x+0.95, y+0.05), (x+0.95, y+0.95), (x+0.8, y+0.8), (x+0.8, y+0.2)], facecolor='black', alpha=0.4))
            ax.add_patch(mpatches.Rectangle((x+0.2, y+0.2), 0.6, 0.6, facecolor=color, alpha=0.8))

        # ==========================================
        # 4. RENDERIZADO VISUAL
        # ==========================================
        plt.style.use("cyberpunk")
        fig, ax = plt.subplots(figsize=(9, 16), dpi=120)
        fig.patch.set_facecolor('none')
        ax.set_facecolor('none')

        # Degradado de fondo
        ax_grad = fig.add_axes([0, 0, 1, 1], zorder=-1)
        gradient = np.linspace(0, 1, 256).reshape(-1, 1)
        cmap = mcolors.LinearSegmentedColormap.from_list("cyber_grad", ['#1a0525', '#000000'])
        ax_grad.imshow(gradient, aspect='auto', cmap=cmap, extent=[0, 1, 0, 1])
        ax_grad.axis('off')

        columnas = 4
        filas = 13 

        meses = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        for m_idx, mes in enumerate(meses):
            y_pos = m_idx * (13 / 12) + 0.5
            ax.text(-0.5, y_pos, mes, color='#aaaaaa', ha='right', va='center', fontsize=11, fontweight='bold', fontname='monospace')

        for i in range(semanas_totales):
            x = i % columnas
            y = i // columnas 
            
            if i < semanas_transcurridas:
                # --- FIX 1: Evaluamos <= 0.0 por ser float ---
                if cigarros_semana[i] <= 0.0:
                    dibujar_bloque_tetris(ax, x, y, None, is_empty=True)
                else:
                    color = obtener_color_cigarros(cigarros_semana[i])
                    dibujar_bloque_tetris(ax, x, y, color)
                    
                    texto_color = 'white' if color == '#FF00FF' else 'black'
                    # --- FIX 2: Formato .1f para garantizar 1 decimal visible ---
                    ax.text(x+0.5, y+0.5, f"{cigarros_semana[i]:.1f}", color=texto_color, ha='center', va='center', fontsize=10, fontweight='black')
                
                # Marco punteado de semana actual
                if i == semanas_transcurridas - 1:
                    ax.add_patch(mpatches.Rectangle((x, y), 1, 1, fill=False, edgecolor='white', lw=3, ls='--'))
            else:
                dibujar_bloque_tetris(ax, x, y, None, is_empty=True)

        ax.set_xlim(-1.0, 5.0) 
        ax.set_ylim(-1, 22) 
        ax.axis('off')

        ax.plot([-0.5, 4.5], [-0.1, -0.1], color='#08F7FE', linewidth=4)
        ax.text(2, -1.2, f"Año {now_mx.year} (52 Semanas)", color='#aaaaaa', ha='center', fontsize=14, fontname='monospace')

        # Leyenda centrada
        leyenda_y = 15
        ax.text(1.9, leyenda_y + 1, "Nivel de Toxicidad (Cigarros/Sem):", color='white', ha='center', fontsize=12, fontname='monospace')
        colores_leyenda = [('#08F7FE', '<15'), ('#00FF00', '15-24'), ('#FFFF00', '25-34'), ('#FF7F00', '35-44'), ('#FF00FF', '+45')]
        for idx, (col, txt) in enumerate(colores_leyenda):
            lx = -0.5 + (idx * 0.95)
            dibujar_bloque_tetris(ax, lx, leyenda_y - 0.5, col)
            ax.text(lx+0.4, leyenda_y - 1.2, txt, color='#aaaaaa', ha='center', fontsize=10, fontname='monospace')
            
        # --- SCORECARD (Tarjeta Central) ---
        card_x, card_y, card_w, card_h = 0.25, 0.60, 0.50, 0.25
        
        # Sombra de la tarjeta
        shadow = mpatches.FancyBboxPatch((card_x+0.01, card_y-0.01), card_w, card_h, boxstyle="round,pad=0.03", facecolor='black', alpha=0.5, transform=fig.transFigure, zorder=2)
        fig.patches.append(shadow)

        # Cuerpo de la tarjeta
        card_outer = mpatches.FancyBboxPatch((card_x, card_y), card_w, card_h, boxstyle="round,pad=0.03", facecolor='#0d0212', edgecolor='#08F7FE', lw=3.5, transform=fig.transFigure, zorder=4)
        fig.patches.append(card_outer)

        # Número Grande (Cigarros YTD)
        fig.text(0.5, 0.78, f"{total_cigarros_ytd}", fontsize=75, color='#08F7FE', ha='center', va='center', fontweight='heavy', fontname='monospace', zorder=5)
        fig.text(0.5, 0.72, "Cigarros acumulados en el año", fontsize=11, color='#aaaaaa', ha='center', fontname='monospace', zorder=5)

        # --- LÍNEA DIVISORIA Y FECHA (PUNTO NUEVO) ---
        line = plt.Line2D((0.35, 0.65), (0.70, 0.70), color='#FF00FF', lw=1.5, alpha=0.6, transform=fig.transFigure, zorder=5)
        fig.lines.append(line)

        # Fecha de hoy dentro de la card
        fecha_actual = now_mx.strftime("%d %b %Y")
        fig.text(0.5, 0.685, f"Corte al: {fecha_actual}", fontsize=10, color='#FF00FF', ha='center', va='center', fontname='monospace', fontweight='bold', zorder=5)

        # Microcopy de Salud
        fig.text(0.5, 0.65, f"{anios_edad_urbana} años más a tu edad urbana", fontsize=12, color='#FF9900', ha='center', va='center', fontweight='bold', zorder=5)
        fig.text(0.5, 0.625, f"{promedio_ias_mes} puntos de IAS promedio al mes", fontsize=12, color='#FFFF00', ha='center', va='center', fontweight='bold', fontname='monospace', zorder=5)

        # --- AJUSTE POSICIÓN DEL GRÁFICO (Para que no tape las redes) ---
        ax.set_position([0.2, 0.08, 0.6, 0.45])

        # --- REDES SOCIALES (FIX Z-ORDER Y POSICIÓN) ---
        fig.text(0.5, 0.61, "IG: @airegpt.ai  |  TikTok: @airegpt", 
                 color='#08F7FE', fontsize=11, ha='center', va='center', 
                 fontname='monospace', fontweight='bold', zorder=10,
                 bbox=dict(facecolor='#1c1c28', edgecolor='#FF00FF', boxstyle='round,pad=0.3', alpha=0.9, lw=1.5))

        # --- CINTILLO SUPERIOR ---
        texto_viral = "Mi partida de Tetris Tóxico en CDMX/EDOMEX\n¡Juega la tuya en AIreGPT!"
        fig.text(0.35, 0.94, texto_viral, fontsize=14, color='white', ha='center', va='center', fontweight='bold', bbox=dict(facecolor='#2b002b', edgecolor='#FF00FF', boxstyle='round,pad=0.8', alpha=0.9, lw=2.5), rotation=3, fontname='monospace', zorder=10)
        generar_qr_eje(fig)

        # --- FOOTER ---
        footer_text = f"AIreGPT | Smability.io | {now_mx.year}\nNota: Esto es un estimado algorítmico y no representa un diagnóstico médico oficial.\nEl número en el bloque representa los cigarros de esa semana."
        fig.text(0.5, 0.02, footer_text, color='#666666', fontsize=9, ha='center', linespacing=1.6, zorder=5)

        # ==========================================
        # 5. EXPORTAR Y SUBIR A S3
        # ==========================================
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0, transparent=False, facecolor='#000000', dpi=120)
        buf.seek(0)
        plt.close(fig)

        # Usando tu propia función subir_imagen_a_s3
        file_name = f"tetris_{user_id}_{now_mx.strftime('%H%M%S')}.png"
        url = subir_imagen_a_s3(buf, file_name)

        return {"status": "success", "url": url, "tipo": "tetris"}

    except Exception as e:
        print(f"❌ Error en generar_grafica_tetris: {e}")
        return {"status": "error", "error": str(e), "tipo": "tetris"}


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
