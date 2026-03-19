import os
import asyncio
import datetime
import time
import requests
import boto3
import nest_asyncio
import random 
from playwright.async_api import async_playwright

nest_asyncio.apply()

print("🎬 INICIANDO MOTOR GRÁFICO: MAPA DINÁMICO AIREGPT")

# 1. CREDENCIALES Y VARIABLES DE ENTORNO (Inyectadas por AWS)
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "sk-TU_TOKEN_AQUI")
IG_TOKEN = os.environ.get("IG_ACCESS_TOKEN")
IG_USER_ID = os.environ.get("IG_ACCOUNT_ID")
S3_BUCKET = os.environ.get("S3_BUCKET", "smability-marketing-reels")

s3 = boto3.client('s3')

# Directorios de trabajo en AWS (/tmp/)
frames_dir = "/tmp/frames"
audio_local = "/tmp/aire_038.mp4"
video_output = "/tmp/noticiero_airegpt_final.mp4"
html_path = "/tmp/render_temp.html"

os.system(f"rm -rf {frames_dir} && mkdir -p {frames_dir}")

# ==========================================
# FASE 1: DESCARGAR AUDIO ALEATORIO DE S3
# ==========================================
# Escogemos un audio al azar del 1 al 40 y le damos formato (ej. aire_015.mp4)
num_audio = random.randint(1, 40)
nombre_audio_s3 = f"aire_{num_audio:03d}.mp4"

try:
    print(f"🎵 Ruleta musical: Descargando pista base ({nombre_audio_s3}) desde S3...")
    s3.download_file(S3_BUCKET, f"audios/{nombre_audio_s3}", audio_local)
except Exception as e:
    print(f"⚠️ Error descargando audio {nombre_audio_s3}, usando silencio. Error: {e}")
    os.system(f"ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t 10 {audio_local} -y")

# ==========================================
# FASE 2: CEREBRO OPENAI (CAPTION DIRECTO)
# ==========================================
def generar_caption_instagram():
    print("📡 Consultando el Gemelo Digital (API Live)...")
    url_api = "https://vuy3dprsp2udtuelnrb5leg6ay0ygsky.lambda-url.us-east-1.on.aws/?mode=map"
    
    try:
        datos = requests.get(url_api).json()
        max_ias = 0
        peor_estacion = "CDMX"
        nivel_riesgo = "REGULAR"
        
        for punto in datos:
            if "station" in punto and punto.get("ias", 0) > max_ias:
                max_ias = punto["ias"]
                peor_estacion = punto["station"]
                nivel_riesgo = punto.get("risk", "REGULAR")
                
        print(f"🚨 Alerta detectada: {peor_estacion.upper()} con {int(max_ias)} PTS ({nivel_riesgo})")
        print("🧠 Generando copy persuasivo con peticiones directas...")
        
        prompt = f"""
        Actúa como el Community Manager de AIreGPT. Escribe un caption corto y directo para un Instagram Reel (máximo 3 o 4 líneas). 
        Tono: Urgente pero informativo, como un noticiero tecnológico de última hora. 
        Datos en vivo a incluir: La zona con el aire más tóxico en CDMX en este momento es {peor_estacion.title()} con {int(max_ias)} puntos ({nivel_riesgo}). 
        Llamado a la acción (CTA): Invita a los usuarios a darle click al link en la bio para conectar su Telegram a @airegptcdmx_bot y obtener 5 días gratis.
        Cierre: Agrega exactamente 3 hashtags relevantes (ej. #CDMX #CalidadDelAire #AIreGPT #Contingencia). No uses emojis excesivos, mantenlo profesional.
        """

        # 🚀 FIX: Usamos `requests` igual que en la Lambda. A prueba de fallos de actualizaciones.
        headers = {
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Eres un experto en marketing de retención y alertas climáticas."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7 
        }
        
        res = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=20)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"].strip()
        
    except Exception as e:
        print(f"❌ Error en el proceso OpenAI: {e}")
        return "Reporte de Calidad del Aire. Conecta Telegram a @airegptcdmx_bot. #AIreGPT #CDMX"

caption_del_dia = generar_caption_instagram()
print(f"📱 CAPTION LISTO:\n{caption_del_dia}\n")

# ==========================================
# FASE 3: CONSTRUIR HTML (El Set de Grabación)
# ==========================================
html_content = r"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AireGPT Reel Render</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css" />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=Roboto+Mono:wght@500;700&display=swap" rel="stylesheet">
    <style>
        body { margin: 0; padding: 0; background: #1a1a1a; font-family: 'Inter', sans-serif; color: #e2e8f0; overflow: hidden; }
        #map { height: 100vh; width: 100vw; background: #1a1a1a; z-index: 1; }
        .sidebar, .footer-controls { display: none !important; }

        .reel-overlay {
            position: absolute; bottom: 35px; left: 5%; width: 90%;
            background: rgba(15, 23, 42, 0.85); border-radius: 10px;
            border: 1px solid rgba(56, 189, 248, 0.25); box-shadow: 0 5px 20px rgba(0,0,0,0.6);
            backdrop-filter: blur(8px); z-index: 2000; padding: 12px 15px; box-sizing: border-box;
            display: flex; flex-direction: column; gap: 10px;
        }

        .header-reel { display: flex; flex-direction: row; justify-content: space-between; align-items: center; gap: 12px; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px; }
        
        .time-badge {
            width: 165px; justify-content: center; flex-shrink: 0; background: rgba(34, 197, 94, 0.15); 
            color: #ffffff !important; padding: 4px 10px;
            border-radius: 6px; font-family: 'Roboto Mono', monospace; font-size: 9px;
            font-weight: 800; border: 1px solid rgba(34, 197, 94, 0.3); display: flex; align-items: center; gap: 6px;
            white-space: nowrap; overflow: hidden;
        }
        .live-dot { height: 6px; width: 6px; background-color: #4ade80; border-radius: 50%; box-shadow: 0 0 6px #4ade80; flex-shrink: 0;}

        .timeline-container { flex-grow: 1; height: 4px; background: rgba(255,255,255,0.2); border-radius: 2px; position: relative; }
        .timeline-progress { position: absolute; left: 0; top: 0; height: 100%; background: #38bdf8; border-radius: 2px; width: 0%;}
        .timeline-dot { position: absolute; top: 50%; transform: translate(-50%, -50%); width: 10px; height: 10px; background: #fff; border: 2px solid #38bdf8; border-radius: 50%; box-shadow: 0 0 6px #38bdf8; left: 0%; transition: left 0.3s linear;}

        .legend-text-labels { display: flex; justify-content: space-between; font-size: 8px; font-weight: 800; letter-spacing: 0.5px; margin-bottom: 2px;}
        .legend-bar { height: 6px; border-radius: 3px; background: linear-gradient(to right, #00e400 16%, #ffff00 33%, #ff7e00 50%, #ff0000 66%, #8f3f97 83%, #7e0023 100%);}
        .legend-labels { display: flex; justify-content: space-between; font-size: 9px; color: #64748b; font-family: 'Roboto Mono'; margin-top: 2px; font-weight: bold;}

        .banner-led {
            width: 100%; height: 36px; background: #000; overflow: hidden; position: absolute; top: 45px; left: 0; z-index: 6000;
            background-image: linear-gradient(90deg, #333 2px, transparent 2px), linear-gradient(0deg, #333 2px, transparent 2px); 
            background-size: 4px 4px; box-shadow: 0 2px 10px rgba(0,0,0,0.8);
        }
        .banner-content { position: absolute; top: 0; left: 0; height: 100%; white-space: nowrap; display: flex; align-items: center; animation: scrollBanner 25s linear infinite; }
        .banner-text { font-family: 'Roboto Mono', monospace; font-size: 14px; font-weight: 700; color: #fff; letter-spacing: 2px; text-transform: uppercase; padding: 0 40px; }
        .banner-text.hl { background: #FFD700; color: #000; padding: 2px 12px; border-radius: 3px; }
        .banner-dots { color: #fff; font-size: 10px; margin: 0 16px; animation: dotsBlink 1s ease-in-out infinite; }
        @keyframes scrollBanner { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
        @keyframes dotsBlink { 0%, 100% { opacity: .2; } 50% { opacity: 1; } }

        .tg-logo { position: absolute; top: 45px; right: 12px; z-index: 7000; width: 36px; height: 36px; filter: drop-shadow(0px 2px 4px rgba(0,0,0,0.6)); }
        .leaflet-interactive { shape-rendering: crispEdges; stroke: none; }
        .leaflet-labels-pane { opacity: 0.95; filter: brightness(1.2) drop-shadow(0px 0px 2px rgba(0,0,0,0.8)); }
        .leaflet-base-pane { filter: brightness(2.0) contrast(1.6) saturate(1.2) !important; opacity: 0.9; }
    </style>
</head>
<body>
    <div class="tg-logo"><svg viewBox="0 0 24 24" fill="#fff"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12L7.19 13.98 4.23 13.07c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.836.944z"/></svg></div>
    <div class="banner-led"><div class="banner-content" id="led-content"></div></div>
    <div class="reel-overlay">
        <div class="header-reel"><div class="time-badge" id="reel-time"><span class="live-dot"></span> LIVE | --:--</div><div class="timeline-container"><div class="timeline-progress" id="progress-line"></div><div class="timeline-dot" id="progress-dot"></div></div></div>
        <div><div class="legend-text-labels"><span style="color:#4ade80">BUENA</span><span style="color:#fde047">REGULAR</span><span style="color:#fb923c">MALA</span><span style="color:#f87171">MUY MALA</span><span style="color:#c084fc">EXTREMA</span></div><div class="legend-bar"></div><div class="legend-labels"><span>0</span><span>50</span><span>100</span><span>150</span><span>200</span><span>300+</span></div></div>
    </div>
    <div id="map"></div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
    <script>
        const BASE_API_URL = "https://vuy3dprsp2udtuelnrb5leg6ay0ygsky.lambda-url.us-east-1.on.aws/";
        const map = L.map('map', { zoomControl: false, preferCanvas: true, zoomSnap: 0.1, renderer: L.canvas({ padding: 0.5 }) });
        map.setView([19.48, -99.13], 10.8);

        map.createPane('basePane');   map.getPane('basePane').style.zIndex = 200;
        map.createPane('gridPane');   map.getPane('gridPane').style.zIndex = 400;
        map.createPane('labelsPane'); map.getPane('labelsPane').style.zIndex = 600; 
        map.getPane('labelsPane').style.pointerEvents = 'none';
        
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', { pane: 'basePane', maxZoom: 19 }).addTo(map);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png', { pane: 'labelsPane', maxZoom: 19 }).addTo(map);

        const CONFIG = { ias: { min: 0, max: 200, stops: [0, 50, 100, 150, 200, 300], colors: ['#00e400', '#ffff00', '#ff7e00', '#ff0000', '#8f3f97', '#7e0023'] } };
        function getColor(val) {
            const conf = CONFIG.ias; let v = Math.max(conf.min, Math.min(Number(val || 0), conf.max));
            for (let i = 0; i < conf.stops.length - 1; i++) {
                if (v >= conf.stops[i] && v <= conf.stops[i+1]) {
                    const t = (v - conf.stops[i]) / (conf.stops[i+1] - conf.stops[i]);
                    const c1 = hexToRgb(conf.colors[i]), c2 = hexToRgb(conf.colors[i+1]);
                    return `rgb(${Math.round(c1.r + (c2.r - c1.r) * t)}, ${Math.round(c1.g + (c2.g - c1.g) * t)}, ${Math.round(c1.b + (c2.b - c1.b) * t)})`;
                }
            }
            return conf.colors[0];
        }
        function hexToRgb(hex) { const r = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex); return r ? { r: parseInt(r[1], 16), g: parseInt(r[2], 16), b: parseInt(r[3], 16) } : {r:0,g:0,b:0}; }

        window.isMapReady = false;

        function formatAMPM(hoursStr, minutesStr) {
            let hours = parseInt(hoursStr); const ampm = hours >= 12 ? 'PM' : 'AM'; hours = hours % 12; hours = hours ? hours : 12; 
            return hours + ':' + minutesStr + ' ' + ampm;
        }

        function actualizarBanner(data) {
            const ledContent = document.getElementById('led-content');
            if (ledContent.innerHTML.includes("REPORTE CALIDAD DEL AIRE")) return;
            ledContent.innerHTML = `
                <span class="banner-text hl">REPORTE CALIDAD DEL AIRE</span><span class="banner-dots">●●●</span>
                <span class="banner-text">MÁS RÁPIDO QUE LAS NOTICIAS</span><span class="banner-dots">●●●</span>
                <span class="banner-text hl">5 DÍAS GRATIS</span><span class="banner-dots">●●●</span>
                <span class="banner-text">@AIREGPTCDMX_BOT</span><span class="banner-dots">●●●</span>
            `.repeat(2); 
        }

        window.cargarHora = async function(modo, timestamp, pct) {
            window.isMapReady = false;
            document.getElementById('progress-line').style.width = pct + '%';
            document.getElementById('progress-dot').style.left = pct + '%';

            let url = `${BASE_API_URL}?mode=${modo}`; if(timestamp) url += `&timestamp=${timestamp}`; url += `&_t=${Date.now()}`;
            
            try {
                const res = await fetch(url); const data = await res.json();
                renderMap(data); actualizarBanner(data);
                
                let timeStr = ""; let dateBadge = ""; const months = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"];

                if(Array.isArray(data) && data.length > 0 && data[0].timestamp) {
                    const parts = data[0].timestamp.split(" "); const ymd = parts[0].split("-"); dateBadge = `${ymd[2]} ${months[parseInt(ymd[1])-1]} | `;
                    const hms = parts[1].substring(0, 5).split(":"); timeStr = formatAMPM(hms[0], hms[1]);
                } else if (timestamp) {
                     const parts = timestamp.split("_"); const ymd = parts[0].split("-"); dateBadge = `${ymd[2]} ${months[parseInt(ymd[1])-1]} | `;
                     const hms = parts[1].replace("-", ":").split(":"); timeStr = formatAMPM(hms[0], hms[1]);
                }

                let statusSymbol = "LIVE "; let color = "#4ade80"; 
                if(modo === 'history') { statusSymbol = "(-) PASADO "; color = "#38bdf8"; } else if(modo === 'forecast_data') { statusSymbol = "(+) FUTURO "; color = "#a855f7"; } 

                const timeBadgeString = `${dateBadge}${statusSymbol}${timeStr}`;
                const badgeEl = document.getElementById('reel-time');
                badgeEl.style.borderColor = color.replace(')', ', 0.4)').replace('rgb', 'rgba');
                badgeEl.style.background = color.replace(')', ', 0.15)').replace('rgb', 'rgba');
                badgeEl.innerHTML = `<span class="live-dot" style="background:${color}; box-shadow:0 0 6px ${color}"></span> ${timeBadgeString}`;
                
                window.isMapReady = true; 
            } catch(e) { window.isMapReady = true; }
        };

        let geoLayer = null;
        function renderMap(data) {
            if (geoLayer) map.removeLayer(geoLayer); if (!Array.isArray(data)) return;
            const rectangles = []; const OVERLAP = 1.0285; 
            
            data.forEach(p => {
                const bounds = [ [p.lat - (0.005 * OVERLAP), p.lon - (0.005 * OVERLAP)], [p.lat + (0.005 * OVERLAP), p.lon + (0.005 * OVERLAP)] ];
                const isStation = p.station ? true : false;
                const blockColor = getColor(p.ias);
                
                rectangles.push(L.rectangle(bounds, { pane: 'gridPane', stroke: false, fillOpacity: isStation ? 0.9 : 0.45, fillColor: blockColor, interactive: false }));
                if (isStation) rectangles.push(L.rectangle(bounds, { pane: 'gridPane', stroke: true, color: '#ffffff', weight: 1.5, fill: false, interactive: false })); 
            });
            geoLayer = L.layerGroup(rectangles).addTo(map);
        }
    </script>
</body>
</html>"""

with open(html_path, "w", encoding="utf-8") as f:
    f.write(html_content)

# ==========================================
# FASE 4: CAPTURA FOTOGRÁFICA (Playwright)
# ==========================================
async def grabar_video():
    print("🎥 Arrancando Chromium para captura de fotogramas...")
    now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=6)
    h_live = now.replace(minute=20, second=0, microsecond=0)
    if now.minute < 20: h_live = h_live - datetime.timedelta(hours=1)
    
    secuencia_frames = []
    for i in range(12, 0, -1):
        secuencia_frames.append(("history", (h_live - datetime.timedelta(hours=i)).strftime("%Y-%m-%d_%H-20")))
    for _ in range(6):
        secuencia_frames.append(("map", ""))
    for i in range(1, 13):
        secuencia_frames.append(("forecast_data", (h_live + datetime.timedelta(hours=i)).replace(minute=0).strftime("%Y-%m-%d_%H-00")))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        context = await browser.new_context(viewport={"width": 432, "height": 768}, device_scale_factor=2.5)
        page = await context.new_page()
        await page.goto(f"file://{html_path}")
        
        idx = 1
        total_frames = len(secuencia_frames)
        for modo, timestamp in secuencia_frames:
            pct = (idx / total_frames) * 100
            print(f"⏱️ Fotograma {idx}/{total_frames}: {modo}")
            await page.evaluate(f"window.cargarHora('{modo}', '{timestamp}', {pct})")
            await page.wait_for_function("window.isMapReady === true", timeout=15000)
            await page.wait_for_timeout(3500) 
            await page.screenshot(path=f"{frames_dir}/frame_{idx:03d}.png")
            idx += 1
        await browser.close()

asyncio.run(grabar_video())

# ==========================================
# FASE 5: RENDERIZADO FINAL FFMPEG
# ==========================================
print("🎞️ Cosiendo fotogramas a 3 FPS y mezclando audio...")
comando_ffmpeg = (
    f"ffmpeg -y -framerate 3.0 -i {frames_dir}/frame_%03d.png "
    f"-i {audio_local} -map 0:v:0 -map 1:a:0 "
    f"-c:v libx264 -profile:v high -level:v 4.0 -pix_fmt yuv420p "
    f"-c:a aac -b:a 192k -shortest "
    f"-vf 'scale=1080:1920:flags=lanczos' {video_output} -hide_banner -loglevel error"
)
os.system(comando_ffmpeg)

# ==========================================
# FASE 6: SUBIDA A S3 Y PUSH A INSTAGRAM (MODO TEST)
# ==========================================
timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")

# 🚀 Guardar el video en la nueva subcarpeta de mapas
video_s3_key = f"reels_maps_publicados/mapa_{timestamp_str}.mp4"

print(f"☁️ Subiendo Master a S3 ({video_s3_key})...")
s3.upload_file(video_output, S3_BUCKET, video_s3_key, ExtraArgs={'ContentType': 'video/mp4'})
video_url = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': video_s3_key}, ExpiresIn=3600)

print(f"✅ [MODO TEST] Video subido a S3 exitosamente en la carpeta: {video_s3_key}")
print("🛑 [MODO TEST] El código de Instagram está desactivado temporalmente para revisión.")

# --- INICIO BLOQUE COMENTADO (Descomentar para producción) ---
"""
if IG_TOKEN and IG_USER_ID:
    print("🤖 Creando contenedor en Meta...")
    res_crear = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media", data={
        "media_type": "REELS", "video_url": video_url, "caption": caption_del_dia, "share_to_feed": "true", "access_token": IG_TOKEN
    }).json()
    
    if "id" in res_crear:
        creation_id = res_crear["id"]
        status_code = "IN_PROGRESS"
        intentos = 0
        while status_code != "FINISHED" and intentos < 12:
            time.sleep(10) 
            intentos += 1
            res_status = requests.get(f"https://graph.facebook.com/v19.0/{creation_id}?fields=status_code&access_token={IG_TOKEN}").json()
            status_code = res_status.get("status_code", "ERROR")
            print(f"⏳ Meta procesando ({intentos}/12): {status_code}")

        if status_code == "FINISHED":
            res_pub = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={
                "creation_id": creation_id, "access_token": IG_TOKEN
            }).json()
            print(f"🎉 ¡PUBLICADO EXITOSAMENTE EN IG! ID: {res_pub.get('id', 'N/A')}")
        else:
            print(f"❌ Meta tardó demasiado. Estado: {status_code}")
else:
    print("⚠️ Faltan tokens de Instagram. El video está en S3, pero no se publicó.")
"""
# --- FIN BLOQUE COMENTADO ---
