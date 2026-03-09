import json
import os
import asyncio
import nest_asyncio
from playwright.async_api import async_playwright
from openai import OpenAI

nest_asyncio.apply()

def lambda_handler(event, context):
    # En AWS Lambda, pasamos el número de flow a través del evento (ej. desde EventBridge o API Gateway)
    # Por defecto hará el 1 si no se le pasa nada.
    numero_de_flow = event.get("flow_number", 1)
    
    print(f"🚀 Iniciando generación para Flow #{numero_de_flow}")
    
    # 1. LEER EL JSON MAESTRO
    with open("master_flows.json", "r", encoding="utf-8") as f:
        master_data = json.load(f)
    
    flujo_hoy = master_data["flows"][numero_de_flow - 1]
    flow_id = flujo_hoy['flow_id']
    
    # 2. ESTILOS
    theme = flujo_hoy.get("color_theme", "red_alert")
    estilos_css = {
        "red_alert": {"bg": "#FF4444 0%,#C0392B 30%,#8B0000 60%,#3a0000 100%", "header": "#FF3B30,#c0392b"},
        "blue_calm": {"bg": "#4facfe 0%,#00f2fe 30%,#01476b 60%,#002033 100%", "header": "#2AABEE,#1a85c2"},
        "purple_health": {"bg": "#a18cd1 0%,#8e44ad 30%,#462b66 60%,#201033 100%", "header": "#8e44ad,#5e3370"},
        "green_safe": {"bg": "#43e97b 0%,#27ae60 30%,#196b34 60%,#0b3317 100%", "header": "#2ecc71,#1e824c"},
        "yellow_warning": {"bg": "#f6d365 0%,#f39c12 30%,#7d4201 60%,#3b1f00 100%", "header": "#f39c12,#b9770e"}
    }
    tema_actual = estilos_css.get(theme, estilos_css["red_alert"])

    # (Imagina que aquí pegas el string gigante de html_template que probamos en Colab)
    # html_template = """ ... """
    
    # Para el ejemplo en Github, asumo que guardas el template en un archivo separado para no ensuciar Python
    with open("template_base.html", "r", encoding="utf-8") as file:
        html_template = file.read()

    html_final = html_template.replace("__BG_GRADIENT__", tema_actual["bg"])
    html_final = html_final.replace("__HEADER_GRADIENT__", tema_actual["header"])
    html_final = html_final.replace("__JSON_MESSAGES__", json.dumps(flujo_hoy["messages"]))

    # ⚠️ GUARDAR EN /tmp/ (Obligatorio en AWS Lambda)
    html_path = "/tmp/render_temp.html"
    video_dir = "/tmp/videos/"
    os.system(f"rm -rf {video_dir} && mkdir -p {video_dir}")
    
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_final)

    # 3. GRABAR CON PLAYWRIGHT
    async def grabar():
        async with async_playwright() as p:
            # En AWS, Chromium necesita estos flags para correr sin interfaz gráfica de SO
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox", "--single-process"]
            )
            context = await browser.new_context(
                record_video_dir=video_dir,
                viewport={"width": 432, "height": 768},
                device_scale_factor=5
            )
            page = await context.new_page()
            await page.goto(f"file://{html_path}")
            await page.wait_for_timeout(15000)
            await page.close()
            await context.close()
            await browser.close()
            
    asyncio.run(grabar())

    # 4. FFMPEG
    video_original = os.path.join(video_dir, os.listdir(video_dir)[0])
    audio_path = f"audios/aire_{numero_de_flow:03d}.mp4"
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

    # 5. OPENAI COPY
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

    # Aquí en el futuro agregaremos la llamada a la API de Meta para subir el output_mp4 y el caption_final
    
    return {
        "statusCode": 200,
        "body": f"Reel {flow_id} generado en /tmp. Caption: {caption_final[:50]}..."
    }
