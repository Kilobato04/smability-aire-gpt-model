import os
import json
import asyncio
import re
import boto3
import requests
import time
from playwright.async_api import async_playwright

# 1. VARIABLES DE ENTORNO (El "Cerebro" manda esto)
FLOW_ID = os.environ.get("FLOW_ID", "default_001")
TEMA = os.environ.get("TEMA_COLOR", "red_alert")
MESSAGES_JSON = os.environ.get("MESSAGES_JSON", "[]")
S3_BUCKET = os.environ.get("S3_BUCKET", "smability-marketing-reels")

print(f"🎬 Iniciando motor gráfico para: {FLOW_ID}")

# 2. CONFIGURACIÓN DE RUTAS Y S3
s3 = boto3.client('s3')
match = re.search(r'\d+', FLOW_ID)
num_str = match.group() if match else "001"
audio_filename = f"aire_{num_str}.mp4" 
audio_local = "/tmp/audio.mp4"
output_mp4 = "/tmp/reel_final.mp4"
video_dir = "/tmp/videos/"

# Descarga de audio desde la bodega
try:
    print(f"🎵 Descargando {audio_filename} desde S3...")
    s3.download_file(S3_BUCKET, f"audios/{audio_filename}", audio_local)
except Exception as e:
    print(f"⚠️ Audio no encontrado. Generando pista de silencio...")
    os.system(f"ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t 15 {audio_local} -y")

# 3. PREPARACIÓN DEL TEMPLATE HTML
with open("template_base.html", "r", encoding="utf-8") as file:
    html_template = file.read()

estilos_css = {
    "red_alert": {"bg": "#FF4444 0%,#C0392B 30%,#8B0000 60%,#3a0000 100%", "header": "#FF3B30,#c0392b"},
    "blue_calm": {"bg": "#4facfe 0%,#00f2fe 30%,#01476b 60%,#002033 100%", "header": "#2AABEE,#1a85c2"},
    "purple_health": {"bg": "#a18cd1 0%,#8e44ad 30%,#462b66 60%,#201033 100%", "header": "#8e44ad,#5e3370"},
    "green_safe": {"bg": "#43e97b 0%,#27ae60 30%,#196b34 60%,#0b3317 100%", "header": "#2ecc71,#1e824c"},
    "yellow_warning": {"bg": "#f6d365 0%,#f39c12 30%,#7d4201 60%,#3b1f00 100%", "header": "#f39c12,#b9770e"}
}
tema_actual = estilos_css.get(TEMA, estilos_css["red_alert"])

html_final = html_template.replace("__BG_GRADIENT__", tema_actual["bg"]) \
                           .replace("__HEADER_GRADIENT__", tema_actual["header"]) \
                           .replace("__JSON_MESSAGES__", MESSAGES_JSON)

html_path = "/tmp/render_temp.html"
os.system(f"rm -rf {video_dir} && mkdir -p {video_dir}")
with open(html_path, "w", encoding="utf-8") as f:
    f.write(html_final)

# 4. GRABACIÓN CON PLAYWRIGHT (Navegador fantasma)
async def grabar():
    print("🎥 Grabando navegador...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        context = await browser.new_context(record_video_dir=video_dir, viewport={"width": 432, "height": 768}, device_scale_factor=2.5)
        page = await context.new_page()
        await page.goto(f"file://{html_path}")
        await page.wait_for_timeout(3000)
        await page.reload() # El truco del claquetazo 🎬
        await page.wait_for_timeout(19000)
        await page.close()
        await context.close()
        await browser.close()

asyncio.run(grabar())

# 5. UNIMOS AUDIO Y VIDEO CON FFMPEG (Receta Meta / Frankenstein 🤖)
video_original = os.path.join(video_dir, os.listdir(video_dir)[0])
output_mp4 = "/tmp/reel_final.mp4"

print("🎞️ Aplicando compresión ultra-optimizada para Meta...")

comando_ffmpeg = f"""
ffmpeg -y -i {video_original} -stream_loop -1 -i "{audio_local}" \
-c:v libx264 -profile:v high -level:v 4.0 -pix_fmt yuv420p \
-r 30 -b:v 8M -maxrate 10M -bufsize 16M -preset slow -tune animation \
-vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2" \
-color_range 1 -colorspace bt709 -color_trc bt709 -color_primaries bt709 \
-movflags +faststart \
-c:a aac -b:a 192k -ar 44100 -ac 2 -map 0:v:0 -map 1:a:0 -shortest -t 15 \
-af "afade=t=out:st=13:d=2" {output_mp4} -hide_banner -loglevel error
"""

os.system(comando_ffmpeg)
print(f"✅ Video optimizado para IG generado en {output_mp4}")

# 6. SUBIDA A S3 Y MODO DEBUG (Instagram Desactivado)
video_s3_key = f"reels_publicados/reel_{FLOW_ID}.mp4"
try:
    print(f"☁️ Subiendo a S3 forzando etiqueta ContentType: video/mp4...")
    s3.upload_file(
        output_mp4, 
        S3_BUCKET, 
        video_s3_key,
        ExtraArgs={'ContentType': 'video/mp4'} # 🔥 EL TRUCO DE META
    )
    print(f"✅ Video subido exitosamente a S3. Ruta: {video_s3_key}")
    print("🛑 MODO DEBUG: Publicación en Instagram desactivada por ahora.")
    
    """ 
    # ========================================================
    # 🔒 BLOQUE DE INSTAGRAM COMENTADO PARA PRUEBAS VISUALES
    # ========================================================
    video_url = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': video_s3_key}, ExpiresIn=3600)
    
    IG_TOKEN = os.environ.get("IG_ACCESS_TOKEN")
    IG_USER_ID = os.environ.get("IG_ACCOUNT_ID")
    CAPTION = os.environ.get("CAPTION_INSTAGRAM", f"Reporte de aire: {FLOW_ID} 😷 #AIreGPT")

    if IG_TOKEN and IG_USER_ID:
        print("🤖 Publicando en Instagram...")
        res_crear = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media", data={
            "media_type": "REELS", "video_url": video_url, "caption": CAPTION, "share_to_feed": "true", "access_token": IG_TOKEN
        }).json()
        
        if "id" in res_crear:
            creation_id = res_crear["id"]
            print(f"📦 Contenedor creado (ID: {creation_id}). Meta está procesando...")
            
            status_code = "IN_PROGRESS"
            intentos = 0
            
            while status_code != "FINISHED" and intentos < 12:
                time.sleep(10) 
                intentos += 1
                url_status = f"https://graph.facebook.com/v19.0/{creation_id}?fields=status_code&access_token={IG_TOKEN}"
                res_status = requests.get(url_status).json()
                status_code = res_status.get("status_code", "ERROR")
                print(f"⏳ Meta trabajando (Intento {intentos}/12): {status_code}")

            if status_code == "FINISHED":
                print("🚀 ¡Meta terminó de procesar! Disparando publicación...")
                res_pub = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={
                    "creation_id": creation_id, "access_token": IG_TOKEN
                }).json()
                
                if "id" in res_pub:
                    print(f"🎉 ¡PUBLICADO EXITOSAMENTE! ID: {res_pub['id']}")
                else:
                    print(f"❌ Fallo al publicar: {json.dumps(res_pub)}")
            else:
                print(f"❌ Meta tardó demasiado. Estado final: {status_code}")
        else:
            print(f"❌ Fallo al crear contenedor: {json.dumps(res_crear)}")
    # ========================================================
    """
except Exception as e:
    print(f"❌ Error en el flujo de salida: {e}")
