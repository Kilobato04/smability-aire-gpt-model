import json
import os
import asyncio
import nest_asyncio
import requests
from playwright.async_api import async_playwright
from openai import OpenAI

nest_asyncio.apply()

# --- 🔑 CONFIGURACIÓN DE APIS ---
os.environ["OPENAI_API_KEY"] = "TU_API_KEY_AQUI"
META_ACCESS_TOKEN = "TU_TOKEN_DE_META"
INSTAGRAM_ACCOUNT_ID = "TU_IG_ACCOUNT_ID"
AD_ACCOUNT_ID = "act_TU_CUENTA_DE_ANUNCIOS"

# EL ENDPOINT OFICIAL DE TU SCHEDULER
MASTER_API_URL = "https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference"

def verificar_contingencia_oficial():
    """
    Consulta la API Maestra de Smability (igual que el Scheduler) 
    para detectar si alguna estación marca Contingencia Fase I o II.
    """
    print("🕵️‍♂️ [MARKETING ENGINE] Consultando API Maestra para contingencias...")
    try:
        r = requests.get(MASTER_API_URL, timeout=15)
        if r.status_code != 200: 
            print(f"❌ Error HTTP al consultar API Maestra: {r.status_code}")
            return False

        data = r.json()
        stations = data.get('stations', [])
        
        if stations:
            for st in stations:
                cont = st.get('contingency')
                if cont and isinstance(cont, dict):
                    raw_phase = cont.get('phase', '')
                    clean_phase = str(raw_phase).strip().upper()
                    
                    if clean_phase in ['FASE I', 'FASE 1', 'FASE II', 'FASE 2']:
                        print(f"🚨 ¡CONTINGENCIA DETECTADA! Estación: {st.get('station_name')} | Fase: {clean_phase}")
                        return True
                        
        print("🍃 Aire libre de contingencias según la API Maestra.")
        return False

    except Exception as e:
        print(f"🔥 Error en verificar_contingencia_oficial: {e}")
        return False

def lambda_handler(event, context):
    numero_de_flow_normal = event.get("flow_number", 1)
    
    # 1. LEER EL JSON MAESTRO
    with open("master_flows.json", "r", encoding="utf-8") as f:
        master_data = json.load(f)
    
    # 2. LÓGICA DE OVERRIDE (EL SWITCH INTELIGENTE BASADO EN TU API)
    hay_contingencia = verificar_contingencia_oficial()
    
    if hay_contingencia:
        print("⚡ Activando OVERRIDE: Se renderizará Reel de Contingencia.")
        flujos_contingencia = [flujo for flujo in master_data["flows"] if flujo.get("contingencia_override") == True]
        # Tomamos el primer flujo de contingencia para este ejemplo
        flujo_hoy = flujos_contingencia[0] 
    else:
        print(f"✅ Día normal. Usando flow secuencial #{numero_de_flow_normal}")
        flujo_hoy = master_data["flows"][numero_de_flow_normal - 1]

    flow_id = flujo_hoy['flow_id']
    print(f"🎯 Flujo seleccionado final: [{flow_id}] - {flujo_hoy['theme_label']}")
    
    # 3. ESTILOS
    theme = flujo_hoy.get("color_theme", "red_alert")
    estilos_css = {
        "red_alert": {"bg": "#FF4444 0%,#C0392B 30%,#8B0000 60%,#3a0000 100%", "header": "#FF3B30,#c0392b"},
        "blue_calm": {"bg": "#4facfe 0%,#00f2fe 30%,#01476b 60%,#002033 100%", "header": "#2AABEE,#1a85c2"},
        "purple_health": {"bg": "#a18cd1 0%,#8e44ad 30%,#462b66 60%,#201033 100%", "header": "#8e44ad,#5e3370"},
        "green_safe": {"bg": "#43e97b 0%,#27ae60 30%,#196b34 60%,#0b3317 100%", "header": "#2ecc71,#1e824c"},
        "yellow_warning": {"bg": "#f6d365 0%,#f39c12 30%,#7d4201 60%,#3b1f00 100%", "header": "#f39c12,#b9770e"}
    }
    tema_actual = estilos_css.get(theme, estilos_css["red_alert"])

    with open("template_base.html", "r", encoding="utf-8") as file:
        html_template = file.read()

    html_final = html_template.replace("__BG_GRADIENT__", tema_actual["bg"])
    html_final = html_final.replace("__HEADER_GRADIENT__", tema_actual["header"])
    html_final = html_final.replace("__JSON_MESSAGES__", json.dumps(flujo_hoy["messages"]))

    html_path = "/tmp/render_temp.html"
    video_dir = "/tmp/videos/"
    os.system(f"rm -rf {video_dir} && mkdir -p {video_dir}")
    
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_final)

    # 4. GRABAR CON PLAYWRIGHT
    async def grabar():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox", "--single-process"])
            context = await browser.new_context(record_video_dir=video_dir, viewport={"width": 432, "height": 768}, device_scale_factor=5)
            page = await context.new_page()
            await page.goto(f"file://{html_path}")
            await page.wait_for_timeout(15000)
            await page.close()
            await context.close()
            await browser.close()
            
    asyncio.run(grabar())

    # 5. FFMPEG Y AUDIO
    video_original = os.path.join(video_dir, os.listdir(video_dir)[0])
    
    # 🚀 FIX: Busca por ID (contingencia_override_001) o por secuencial (aire_012.mp4)
    # Por ahora, usamos el número formateado asumiendo que tus 40 audios son "aire_001.mp4"
    numero_formateado = f"{numero_de_flow_normal:03d}"
    audio_path = f"audios/aire_{numero_formateado}.mp4" 
    
    output_mp4 = f"/tmp/reel_{flow_id}.mp4"

    comando_ffmpeg = f"""
    ffmpeg -y -i {video_original} -stream_loop -1 -i "{audio_path}" \
    -c:v libx264 -crf 14 -preset veryfast -profile:v high -pix_fmt yuv420p \
    -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
    -vf "scale=1440:2560:flags=lanczos" \
    -c:a aac -b:a 256k -map 0:v:0 -map 1:a:0 -shortest -t 15 \
    -af "afade=t=out:st=11:d=4" {output_mp4} -hide_banner -loglevel error
    """
    os.system(comando_ffmpeg)

    # 6. OPENAI COPY
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    instr = flujo_hoy["llm_instructions"]
    prompt_armado = instr["prompt_template"].format(
        tone=instr["tone"], persona=instr["persona"], avoid=", ".join(instr["avoid"]),
        hook_style=instr["hook_style"], max_chars=instr["max_chars"], must_include=", ".join(instr["must_include"])
    ).replace("[FLOW_MESSAGES]", " | ".join([m["text"] for m in flujo_hoy["messages"]]))

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": "Experto en Reels."}, {"role": "user", "content": prompt_armado}]
    )
    caption_final = res.choices[0].message.content
    print(f"📝 Caption generado: {caption_final[:50]}...")

    # 7. PUBLICACIÓN ORGÁNICA (Graph API) - Concepto Fase 3
    print("📤 Subiendo a Instagram Reels...")
    # media_id = subir_a_instagram(output_mp4, caption_final)
    media_id = "TEST_MEDIA_ID_12345" # Simulación

    # 8. PAUTA AUTOMÁTICA DE $5,000 MXN (Meta Marketing API)
    if hay_contingencia and media_id:
        print("💸 Contingencia detectada: Disparando pauta de $5,000 MXN...")
        # pautar_reel_en_meta(media_id, presupuesto=5000, ubicacion="CDMX")

    return {
        "statusCode": 200,
        "body": json.dumps({"status": "success", "flow_id": flow_id, "pauta_lanzada": hay_contingencia})
    }
