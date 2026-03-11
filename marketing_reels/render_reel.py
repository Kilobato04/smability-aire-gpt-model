import os
import json
import asyncio
import re
import boto3
from playwright.async_api import async_playwright

# 1. RECIBIMOS LAS VARIABLES QUE MANDÓ LA LAMBDA
FLOW_ID = os.environ.get("FLOW_ID", "default_001")
TEMA = os.environ.get("TEMA_COLOR", "red_alert")
MESSAGES_JSON = os.environ.get("MESSAGES_JSON", "[]")
S3_BUCKET = os.environ.get("S3_BUCKET", "smability-marketing-reels")

print(f"🎬 Iniciando motor gráfico para: {FLOW_ID}")

# 2. DESCARGAMOS EL AUDIO DESDE EL BUCKET S3
s3 = boto3.client('s3')
# Extraemos el número del flujo (ej. circulacion_003 -> 003)
match = re.search(r'\d+', FLOW_ID)
num_str = match.group() if match else "001"
audio_filename = f"aire_{num_str}.mp4"  # <-- ASEGÚRATE DE QUE TUS AUDIOS SE LLAMEN ASÍ (ej. reel_001.mp4)
audio_local = "/tmp/audio.mp4"

try:
    print(f"🎵 Descargando {audio_filename} desde S3...")
    s3.download_file(S3_BUCKET, f"audios/{audio_filename}", audio_local)
except Exception as e:
    print(f"⚠️ No se encontró el audio. Fallback a genérico. Error: {e}")
    os.system(f"touch {audio_local}") # Archivo vacío temporal si falla

# 3. ARMAMOS EL HTML CON LOS ESTILOS
estilos_css = {
    "red_alert": {"bg": "#FF4444 0%,#C0392B 30%,#8B0000 60%,#3a0000 100%", "header": "#FF3B30,#c0392b"},
    "blue_calm": {"bg": "#4facfe 0%,#00f2fe 30%,#01476b 60%,#002033 100%", "header": "#2AABEE,#1a85c2"},
    "purple_health": {"bg": "#a18cd1 0%,#8e44ad 30%,#462b66 60%,#201033 100%", "header": "#8e44ad,#5e3370"},
    "green_safe": {"bg": "#43e97b 0%,#27ae60 30%,#196b34 60%,#0b3317 100%", "header": "#2ecc71,#1e824c"},
    "yellow_warning": {"bg": "#f6d365 0%,#f39c12 30%,#7d4201 60%,#3b1f00 100%", "header": "#f39c12,#b9770e"}
}
tema_actual = estilos_css.get(TEMA, estilos_css["red_alert"])

with open("template_base.html", "r", encoding="utf-8") as file:
    html_template = file.read()

html_final = html_template.replace("__BG_GRADIENT__", tema_actual["bg"])
html_final = html_final.replace("__HEADER_GRADIENT__", tema_actual["header"])
html_final = html_final.replace("__JSON_MESSAGES__", MESSAGES_JSON)

html_path = "/tmp/render_temp.html"
video_dir = "/tmp/videos/"
os.system(f"rm -rf {video_dir} && mkdir -p {video_dir}")

with open(html_path, "w", encoding="utf-8") as f:
    f.write(html_final)

# 4. GRABAMOS LA PANTALLA CON PLAYWRIGHT (15 SEGUNDOS NATIVOS IG)
async def grabar():
    print("🎥 Grabando navegador fantasma...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"])
        # 🔥 FIX: 540x960 * 2 = 1080x1920 EXACTOS (El tamaño nativo perfecto de Instagram)
        context = await browser.new_context(record_video_dir=video_dir, viewport={"width": 432, "height": 768}, device_scale_factor=2.5)
        page = await context.new_page()
        await page.goto(f"file://{html_path}")
        await page.wait_for_timeout(18000) # 18 segundos confirmados
        await page.close()
        await context.close()
        await browser.close()

asyncio.run(grabar())

# 5. UNIMOS AUDIO Y VIDEO CON FFMPEG (CALIDAD PREMIUM)
video_original = os.path.join(video_dir, os.listdir(video_dir)[0])
output_mp4 = "/tmp/reel_final.mp4"

print("🎞️ Uniendo pistas de video y audio...")
# 🔥 FIX: CRF de 18 a 14 (Más pesado/Mayor calidad), preset a 'slow' para mejor renderizado y audio a 320k.
comando_ffmpeg = f"""
ffmpeg -y -i {video_original} -stream_loop -1 -i "{audio_local}" \
-c:v libx264 -crf 14 -preset slow -profile:v high -pix_fmt yuv420p \
-c:a aac -b:a 320k -map 0:v:0 -map 1:a:0 -shortest -t 15 \
-af "afade=t=out:st=13:d=2" {output_mp4} -hide_banner -loglevel error
"""
os.system(comando_ffmpeg)
print("✅ Procesamiento FFmpeg finalizado.")
