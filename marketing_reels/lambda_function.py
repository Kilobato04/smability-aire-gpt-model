import json
import os
import random
import boto3
import requests

# --- 🔑 CONFIGURACIÓN DE APIS ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
CODEBUILD_PROJECT = os.environ.get('CODEBUILD_PROJECT', 'Smability-Marketing-Renderer')
MASTER_API_URL = "https://y4zwdmw7vf.execute-api.us-east-1.amazonaws.com/prod/api/air-quality/current?type=reference"

codebuild = boto3.client('codebuild')

def verificar_contingencia_oficial():
    """Consulta la API Maestra de Smability para detectar Contingencia."""
    print("🕵️‍♂️ [MANAGER] Consultando API Maestra para contingencias...")
    try:
        r = requests.get(MASTER_API_URL, timeout=10)
        if r.status_code != 200: return False, "None"
        
        for st in r.json().get('stations', []):
            cont = st.get('contingency')
            if cont and isinstance(cont, dict):
                phase = str(cont.get('phase', '')).strip().upper()
                if phase in ['FASE I', 'FASE 1', 'FASE II', 'FASE 2']:
                    print(f"🚨 ¡CONTINGENCIA! Estación: {st.get('station_name')} | Fase: {phase}")
                    return True, phase
        return False, "None"
    except Exception as e:
        print(f"🔥 Error API Maestra: {e}")
        return False, "None"

def lambda_handler(event, context):
    print("🎬 Iniciando Manager de Marketing (El Cerebro)...")
    
    # 1. ¿Día normal o Contingencia? (¡PRIORIDAD AL SCRAPER!)
    if event.get("contingencia_override") == True:
        hay_contingencia = True
        print("🚨 ORDEN DIRECTA DEL SCRAPER (CAMe). Ignorando API. Forzando Reel Rojo.")
    else:
        hay_contingencia, phase = verificar_contingencia_oficial()
        
    # 2. Leer el Master JSON para saber qué reel toca
    with open("master_flows.json", "r", encoding="utf-8") as f:
        master_data = json.load(f)
        
    if hay_contingencia:
        print("⚡ OVERRIDE: Modo Contingencia Activado.")
        flujos_contingencia = [f for f in master_data["flows"] if f.get("contingencia_override")]
        flujo_elegido = flujos_contingencia[0] # Tomamos el de emergencia
    else:
        # Día normal: Filtramos primero y elegimos uno al azar directamente
        flujos_normales = [f for f in master_data["flows"] if not f.get("contingencia_override")]
        flujo_elegido = random.choice(flujos_normales)
        print(f"✅ Día normal. Elegido el flow ID: {flujo_elegido.get('flow_id')}")

    flow_id = flujo_elegido['flow_id']
    tema = flujo_elegido.get("color_theme", "red_alert")
    print(f"🎯 Mandando a fabricar: [{flow_id}] | Tema: {tema}")

    # 3. OPENAI GENERANDO EL COPY JUGOSO (MODO LIGERO CON REQUESTS)
    instr = flujo_elegido["llm_instructions"]
    prompt_armado = instr["prompt_template"].format(
        tone=instr["tone"], persona=instr["persona"], avoid=", ".join(instr["avoid"]),
        hook_style=instr["hook_style"], max_chars=instr["max_chars"], must_include=", ".join(instr["must_include"])
    ).replace("[FLOW_MESSAGES]", " | ".join([m["text"] for m in flujo_elegido["messages"]]))

    try:
        print("🤖 Pidiendo copy a ChatGPT...")
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Experto en Reels virales."}, 
                {"role": "user", "content": prompt_armado}
            ],
            "temperature": 0.7
        }
        res = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=15)
        res.raise_for_status() # Lanza error si falla
        caption_final = res.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Error OpenAI: {e}")
        caption_final = "Descubre cuánto humo respiras al día. 😷 Entra al link en nuestra bio."

    print(f"📝 Caption generado:\n{caption_final}")

    # 4. 🔥 LE PASAMOS LA ESTAFETA AL OBRERO (CODEBUILD)
    payload_para_codebuild = {
        'FLOW_ID': flow_id,
        'TEMA_COLOR': tema,
        'MESSAGES_JSON': json.dumps(flujo_elegido["messages"]),
        'CAPTION_INSTAGRAM': caption_final,
        'ES_CONTINGENCIA': str(hay_contingencia).lower()
    }

    #--
    try:
        response = codebuild.start_build(
            projectName=CODEBUILD_PROJECT,
            environmentVariablesOverride=[ # <--- CORREGIDO (Sin "s")
                {'name': k, 'value': str(v), 'type': 'PLAINTEXT'} for k, v in payload_para_codebuild.items()
            ]
        )
        build_id = response['build']['id']
        print(f"🚀 ¡CodeBuild disparado con éxito! ID de Trabajo: {build_id}")
        
        return {'statusCode': 200, 'body': f'Manager terminó. Trabajo {build_id} enviado a CodeBuild.'}
        
    except Exception as e:
        print(f"❌ Error disparando CodeBuild: {e}")
        return {'statusCode': 500, 'body': str(e)}
