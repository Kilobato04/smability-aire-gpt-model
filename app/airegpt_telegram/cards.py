import urllib.parse

BOT_VERSION = "v6.0"

# --- NUEVO FOOTER LIMPIO ---
BOT_FOOTER = """_Nota: Sugerencias de salud preventivas basadas en modelos ambientales. No sustituyen evaluación médica._

[AIreGPT](https://airegpt.ai) | [Canal](https://t.me/smability) | [IG](https://instagram.com/airegpt.ai) | [TikTok](https://tiktok.com/@airegpt)"""

IAS_INFO = {
    "Buena": {"msg": "Aire limpio.", "rec": "¡Disfruta el exterior!", "emoji": "🟢"},
    "Regular": {"msg": "Calidad aceptable.", "rec": "Sensibles: moderar esfuerzo.", "emoji": "🟡"},
    "Mala": {"msg": "Podría causar molestias.", "rec": "Evita ejercicio intenso fuera.", "emoji": "🟠"},
    "Muy Mala": {"msg": "Riesgo alto.", "rec": "No salgas. Cierra ventanas.", "emoji": "🔴"},
    "Extremadamente Mala": {"msg": "¡Peligro!", "rec": "Urgencia médica si hay síntomas.", "emoji": "🟣"}
}

# --- NUEVO HELPER (Requerido por v0.6.0) ---
def get_emoji_for_quality(calidad):
    return IAS_INFO.get(calidad, {}).get("emoji", "⚪")

def get_health_advice(calidad, user_condition=None, is_premium=False):
    advice = {
        "Buena": "Disfruta tus actividades al aire libre sin restricciones.",
        "Regular": "Reduce actividades intensas si eres muy sensible a la contaminación.",
        "Mala": "Evita el ejercicio vigoroso al aire libre. Grupos sensibles deben quedarse en interiores.",
        "Muy Mala": "Permanece en interiores con ventanas cerradas. No realices esfuerzo físico afuera.",
        "Extremadamente Mala": "¡Emergencia! Quédate en casa. Usa mascarilla N95/KN95 si necesitas salir."
    }
    
    # Tu normalización (¡No la tocamos porque es a prueba de balas!)
    cat = calidad.replace("Extremadamente Alta", "Extremadamente Mala").replace("Muy Alta", "Muy Mala").replace("Alta", "Mala")
    base_rec = advice.get(cat, "Toma precauciones al aire libre.")
    
    # Si el usuario no tiene perfil de salud, mandamos el texto limpio
    if not user_condition or user_condition.lower() == "ninguno": 
        return base_rec
        
    # 🔒 BLOQUEO PARA USUARIOS FREE (Le damos la recomendación general y le vendemos la suya)
    if not is_premium:
        return f"{base_rec}\n❤️ *Consejo para tu {user_condition.capitalize()}:* 🔒 Exclusivo Premium"
        
    # --- NUEVA LÓGICA DE HIPER-PERSONALIZACIÓN (SOLO PREMIUM) ---
    hc_lower = user_condition.lower()
    
    # Escenarios de Peligro (Mala, Muy Mala, Extrema)
    if cat in ["Mala", "Muy Mala", "Extremadamente Mala"]:
        if any(x in hc_lower for x in ["asma", "epoc", "bronquitis"]):
            return f"⚠️ **¡Alerta por tu {user_condition}!** El aire actual puede detonar una crisis. Ten a la mano tu medicación de rescate. {base_rec}"
        elif any(x in hc_lower for x in ["cardiac", "infarto", "hipertension", "presion"]):
            return f"⚠️ **¡Alerta Cardiovascular!** La contaminación eleva tu riesgo cardíaco. {base_rec}"
        elif any(x in hc_lower for x in ["alergia", "rinitis"]):
            return f"⚠️ **Precaución por tus alergias:** Las partículas pueden congestionarte más de lo normal. {base_rec}"
        else:
            # Paracaídas para otras condiciones
            return f"⚠️ **Por tu {user_condition}:** {base_rec}"
            
    # Escenario de Precaución (Regular)
    elif cat == "Regular":
        if any(x in hc_lower for x in ["asma", "epoc", "alergia", "rinitis"]):
            return f"ℹ️ **Atención a tu {user_condition}:** Reduce el esfuerzo físico al aire libre para no irritar tus vías respiratorias."
        else:
            return f"ℹ️ **Por tu {user_condition}:** Considera reducir el esfuerzo físico."
            
    # Escenario Seguro (Buena)
    else:
        return f"✅ **Buena noticia:** El aire es completamente seguro para tu {user_condition} hoy."

# --- PLANTILLAS DE TARJETAS ---

CARD_IAS_INFO = """📊 *¿Qué es el IAS (Índice de Aire y Salud)?*

El *IAS* es el indicador oficial actual para medir la contaminación. Sustituyó al antiguo *IMECA* en 2019 y está diseñado para proteger tu salud.

*Escala Oficial (Puntos):*

🟢 *Buena (0 a 50 pts)*
Riesgo bajo. Excelente para cualquier actividad al aire libre.

🟡 *Regular (51 a 100 pts)*
Riesgo moderado. *Grupos sensibles* (niños, adultos mayores, personas con asma) deben reducir esfuerzos pesados.

🟠 *Mala (101 a 150 pts)*
Riesgo alto. *Grupos sensibles* no deben hacer actividades al aire libre. La población general debe reducir esfuerzos.

🔴 *Muy Mala (151 a 200 pts)*
Riesgo muy alto. *Nadie* debería realizar actividades al aire libre. Mantente en interiores.

🟣 *Extremadamente Mala (>200 pts)*
Riesgo extremo. Peligro sanitario. Cierra ventanas y no salgas de casa.

{footer}"""

CARD_RULES = """⚙️ *REGLAS DE OPERACIÓN Y ALCANCE*
Para mantener a AIreGPT rápido, preciso y sin hacer spam, opero bajo estas reglas:

🌃 *Horario de Descanso:* Solo envío alertas entre las 6:00 AM y las 11:00 PM. Las "Alertas" se envía 20 min después de cada hora.
📍 *Ubicaciones (Max 3):* Solo "Casa" y "Trabajo" se utilizan para calcular tu exposición.
🛑 *Filtro Anti-Spam:* Las alertas de emergencia requieren un mínimo de 100 pts IAS. Me silenciaré tras 3 avisos.
🗺️ *Cobertura:* Alertas de *Contingencia* y *Hoy No Circula* exclusivas para CDMX y ZMVM.
🧠 *Motor de IA:* Funciono con *GPT-4o-mini*. Por favor, verifica la información crítica.
🔬 *Ciencia de Exposición:* El cálculo de "cigarros invisibles" usa algoritmos que miden tu exposición al exterior (según tu transporte y tiempo) y asume que pasas el resto del día en interiores, donde se filtra el 60% de las partículas.
📡 *Fuente de Verdad:* Mis datos provienen del modelo científico: [Monitoreo de Calidad del Aire y Gemelo Digital en Tiempo Real 🚦🌎](https://airmodelcdmx.netlify.app/)
🏢 *Desarrollo:* Producto desarrollado por *Smability.io*.

{footer}"""

CARD_PROMPTS = """💡 *GUÍA DE USO: ¿QUÉ PUEDES PREGUNTARME?*
Puedes hablarme de forma natural. Aquí tienes los ejemplos más útiles para sacarme provecho:

💨 *Calidad del Aire y Clima:*
• *"¿Cómo está el aire en Casa?"*
• *"Dame el pronóstico para el Trabajo."*
• *"Soy asmático, ¿me recomiendas salir a correr hoy?"*

🚗 *Movilidad y Auto:*
• *"¿Circula mi auto hoy?"*
• *"¿Me toca verificar este mes?"*

🚬 *Salud y Exposición:*
• *"Calcula mi exposición: Viajo 2 horas en metro."*
• *"Hoy hice Home Office."*

⚙️ *Configuración:*
• *"Avisa si el aire supera los 100 puntos IAS en Casa."*
• *"Mándame un reporte todos los días a las 7:30 AM de Casa."*
• *"Dame mi resumen."*

¡Copia, pega y prueba cualquiera de estos mensajes ahora mismo! 👇

{footer}"""

CARD_MENU = """🛠️ *MENÚ DE CAPACIDADES*
Soy *AIreGPT*, tu asistente inteligente de salud urbana. Aquí tienes todo lo que podemos hacer juntos:

🚨 *Contingencias en Tiempo Real:* *(¡Nuestra especialidad!)* Te enviaré una alerta inmediata en el segundo exacto en que se *active o suspenda* una Contingencia Ambiental.
📍 *Reportes y Pronósticos:* Guarda hasta 3 ubicaciones (Casa, Trabajo, Escuela) y pídeme la calidad del aire actual, el pronóstico y recomendaciones de salud.
🚬 *Exposición:* Dime cómo y cuánto tiempo viajas para calcular cuántos *cigarros invisibles* respiras y tu Edad Urbana.
🚗 *Auto y Movilidad:* Registra tu placa y holograma. Te diré si circulas hoy y los meses que te toca **verificar**.
⏰ *Alertas Inteligentes:* Programa un reporte diario a la hora que sales o alertas automáticas si la contaminación supera tu límite.
📊 *Tu Resumen:* Escribe *"Dame mi resumen"* para ver toda tu configuración y estatus.

🤖 *Tip AIreGPT:* Próximamente también estaré disponible en *WhatsApp* 🟢 (Guarda este número: +52 55 1956 6483).

💡 _*Tip: Háblame de forma natural. Ej: "Avísame a las 8 am cómo está el aire en casa".*_

{footer}"""

CARD_ONBOARDING = """👋 *¡Hola, {user_name}! Bienvenido a AIreGPT tu Agente Ambiental.*

Conmigo podrás:
💨 Ver la calidad del aire y el pronóstico exacto de tus zonas.
🚬 Calcular cuántos "cigarros invisibles" respiras en el tráfico.
🚨 Recibir alertas de calidad del aire, Contingencia, Hoy No Circula y Multas.
⏰ Programar notificaciones si el aire se vuelve tóxico.

🤖 *Tip AIreGPT:* Próximamente también estaré disponible en *WhatsApp* 🟢 (Guarda este número para el futuro: +52 55 1956 6483).

Para protegerte, necesito saber dónde te mueves. 

👇 *Toca el botón de abajo para empezar a configurar tu CASA:*

{footer}"""

# Esta tarjeta se lanza cuando el usuario guarda su Casa (Callbacks)
CARD_ONBOARDING_WORK = """✅ *¡Tu Casa está protegida!*

🚀 *PASO 2: Tu destino principal.*
Para calcular cuánto humo te *"fumas"* en el tráfico, necesito saber a dónde vas todos los días.

👇 *Por favor, toca el clip 📎 (abajo a la izquierda), selecciona "Ubicación" y envíame la ubicación de tu TRABAJO o escuela.*

{footer}"""

# Esta tarjeta se lanza cuando el usuario guarda su Trabajo (Callbacks)
CARD_ONBOARDING_VEHICLE = """✅ *¡Ruta Casa ↔ Trabajo configurada!*

🚗 *PASO 3: Blindaje Anti-Multas (Opcional pero recomendado).*
Para avisarte exactamente qué días no circulas y cuándo te toca verificar, registra tu auto.

💬 *Escríbeme un mensaje normal como este:*
*"Mi placa termina en 5 y soy holograma 0"*

*(Si no tienes auto, simplemente ignora este paso y pídeme la "Calidad del aire" para empezar).*

{footer}"""


# ACTUALIZADA: Se agregó {trend_arrow} para aprovechar el dato de la nueva API

CARD_REPORT = """🌤️ *{greeting}, {user_name}!*
Tu *reporte* de 📍 **[{location_name}]({maps_url})**:
🗺️ {region} • 🕒 {report_time}

{risk_circle} *Calidad {risk_category} ({ias_value} pts)* | ☣️ {pollutant}

🌡️ {temp}°C | 💧 {humidity}% | 🌬️ {wind_speed} km/h
📊 *Tendencia:* {trend}

📈 *Pronóstico (Próximas hrs):*
{forecast_block}

🛡️ *Salud:* {health_recommendation}

{footer}"""

CARD_ALERT_IAS = """🚨 *¡ALERTA DE CALIDAD DEL AIRE!* 🚨
Hola {user_name}, la contaminación en 📍 **[{location_name}]({maps_url})** a las 🕒 {report_time} ha superado tu límite de seguridad.

{risk_circle} *Calidad {risk_category} ({ias_value} pts)* | ☣️ {pollutant}
*Tu umbral configurado es: {threshold} pts*

📊 *Tendencia:* {forecast_msg}

🛡️ *Acción inmediata:* {health_recommendation}

{footer}"""

CARD_REMINDER = """⏰ *{greeting}, {user_name}!*
Tu *reporte* de 📍 **[{location_name}]({maps_url})**:
🗺️ {region} • 🕒 {report_time}

{risk_circle} *Calidad {risk_category} ({ias_value} pts)* | ☣️ {pollutant}

🌡️ {temp}°C | 💧 {humidity}% | 🌬️ {wind_speed} km/h
📊 *Tendencia:* {trend}

📈 *Pronóstico (Próximas hrs):*
{forecast_block}

🛡️ *Salud:* {health_recommendation}
{footer}"""

CARD_CONTINGENCY = """🚨 *¡CONTINGENCIA AMBIENTAL!* 🚨
🌎 Zona Metropolitana del Valle de México
🕒 {report_time}

⚠️ *FASE ACTIVA:* {phase}
☣️ *Detalle:* {pollutant_info}
📍 *Estación Crítica:* {station_info}

🛑 *Restricciones Vehiculares:*
{restrictions_txt}

📄 [Leer Comunicado Oficial]({oficial_link})

🛡️ *Acción:*
• Cierra ventanas y evita salir.
• No realices ejercicio al aire libre.
📌 *La contingencia se mantiene vigente hasta que la CAMe emita el comunicado oficial de suspensión. No saques tu auto hasta confirmarlo.*

{footer}"""

CARD_CONTINGENCY_LIFTED = """🟢 *CONTINGENCIA SUSPENDIDA*
🌎 Zona Metropolitana del Valle de México
🕒 {report_time}

🎉 *¡Buenas noticias!*
La CAMe informa que las condiciones del aire han mejorado.

🚗 *Movilidad:*
Se levantan las restricciones del Doble Hoy No Circula. Tu auto vuelve a su calendario normal.

📄 [Leer Comunicado Oficial]({oficial_link})

_Fuente: SIMAT / Smability_

{footer}"""

CARD_HNC_RESULT = """🚗 *HOY NO CIRCULA*
📅 *Fecha:* {fecha_str} ({dia_semana})
🚘 *Auto:* {plate_info} (Holo {hologram})

{status_emoji} *{status_title}*
{status_message}

⚠️ *Razón:* {reason}

{footer}"""

CARD_HNC_DETAILED = """🚗 *Reporte Mensual HNC: {mes_nombre}*

🚘 *Placa:* ...{plate} | *Engomado:* {color}
*Holograma:* {holo}

📅 *VERIFICACIÓN:* {verificacion_txt}

📅 *DÍAS QUE NO CIRCULAS:*
{dias_semana_txt}
{sabados_txt}
🕒 *Horario:* 05:00 - 22:00 hrs

📋 *Fechas específicas este mes:*
{lista_fechas}

👮 *RIESGO DE MULTA (Si omites):*
🏛️ *CDMX:* {multa_cdmx} + Corralón
🏛️ *Edomex:* {multa_edomex} + Retención

{footer}"""

CARD_SUMMARY = """📊 *RESUMEN DE CUENTA*
👤 {user_name} | Plan: {plan_status}

🚨 *Alerta Contingencia:* {contingency_status}

📍 *Tus Ubicaciones:*
{locations_list}

🏥 *Tu Salud:*
{health_display}

🚇 *Tu Rutina (Cálculo de Exposición):*
{transport_info}

🚗 *Tu Auto:*
{vehicle_info}

🔔 *Alertas Aire (Por Nivel/Umbral):*
{alerts_threshold}

⏰ *Reportes Aire (Programados):*
{alerts_schedule}

🚫 *Tu Auto Circula Hoy?:*
{hnc_reminder}

💡 _Tip: Dile al bot 'Cambia mi transporte a...' para ajustar tu rutina._

{footer}"""

CARD_VERIFICATION = """🚗 *ESTATUS DE VERIFICACIÓN*

🚘 *Auto:* {plate_info} | {engomado}

📅 *Tu Periodo:*
{period_txt}

⚠️ *Fecha Límite:* {deadline}

💰 *MULTA (Extemporánea):*
💸 *${fine_amount} MXN* (20 UMAS)
+ Corralón si eres detenido circulando.

💡 _Recuerda agendar tu cita una semana antes._

{footer}"""

CARD_MY_LOCATIONS = """📍 *MIS UBICACIONES GUARDADAS*
👤 {user_name}

{locations_list}

👇 *Usa los botones para consultar o eliminar.*

{footer}"""

# --- NUEVA TARJETA: EXPOSICIÓN (GAMIFICACIÓN) ---
CARD_EXPOSICION = """{emoji_alerta} *Reporte de Exposición*
👋 Hola {user_name}!

Ayer *{fecha_ayer}* te expusiste a una calidad del aire que le pasó factura a tu cuerpo. 👇

{rutina_str}
😷 *Aire que respiraste:* {calidad_ias} ({promedio_ias} pts IAS). Sin embargo...

{emoji_cigarro} Respiraste el equivalente a *{cigarros} cigarros invisibles* en tu rutina.
{emoji_edad} Esto sumó *{dias} días extra* de desgaste a tu Edad Urbana.

_*Promedio de exposición {promedio_riesgo} µg/m³ eq.*_

{footer}"""

# --- NUEVAS TARJETAS: SUSCRIPCIONES Y PAYWALL ---
CARD_TRIAL_ACTIVE = """💎 *Función Premium*
        
Estás intentando {action_text}. Como estás en tus primeros {trial_days} días, ¡puedes hacerlo gratis! Disfruta tu periodo de prueba (te quedan {days_left} días). 🚀"""

CARD_PAYWALL = """🔒 *Función Bloqueada*
        
Tu periodo de prueba ha concluido. Para {action_text}, necesitas activar *AIreGPT Premium*.

*Con Premium obtienes:*
🚨 Alertas instantáneas de Calidad del Aire, Contingencias y recordatorios de HNC.
😷 Cálculo diario de los "Cigarros Invisibles" que respiras.
⏰ Reportes programados (Ej. Dime la Calidad del Aire todos los días a las 8am en mi Casa).
📍 Soporte hasta para 3 ubicaciones.

👇 *Elige tu plan para desbloquear tu cuenta inmediatamente:*"""

CARD_WELCOME_PREMIUM = """🎉 *¡PAGO CONFIRMADO!* 💎
        
Bienvenido a *AIreGPT Premium*, {user_name}. Tu cuenta ha sido desbloqueada exitosamente.

*Tus nuevos superpoderes están listos:*
✅ Alertas automáticas reactivadas.
✅ Cálculo de exposición diario desbloqueado.
✅ Soporte para 3 ubicaciones y reportes programados.

Toca el botón de abajo para ver tu nuevo resumen o dime qué quieres configurar ahora. 🚀

{footer}"""

# --- BOTONES DE EXPOSICIÓN Y ONBOARDING ---
def get_exposure_button():
    # Usamos 💨🚬 como combo, o si prefieres solo la cajita 🚬
    return {"inline_keyboard": [[{"text": "💨🚬 ¿Cuántos cigarros respiré ayer?", "callback_data": "CHECK_EXPOSURE"}]]}

def get_transport_buttons():
    # UX Ajustada: Consolidamos Auto, agregamos Metrobús
    return {"inline_keyboard": [
        [{"text": "🚇 Metro / Tren", "callback_data": "SET_TRANS_metro"}, 
         {"text": "🚌 Metrobús", "callback_data": "SET_TRANS_metrobus"}],
        [{"text": "🚗 Automóvil", "callback_data": "SET_TRANS_auto_ac"}, # Asumimos AC/Cerrado para la matemática
         {"text": "🚐 Combi / Micro", "callback_data": "SET_TRANS_combi"}],
        [{"text": "🚲 Caminar / Bici", "callback_data": "SET_TRANS_caminar"},
         {"text": "🏠 Home Office", "callback_data": "SET_TRANS_home_office"}]
    ]}

def get_time_buttons():
    # UX Ajustada: Agregamos 30 mins (0.5 hrs)
    return {"inline_keyboard": [
        [{"text": "⏱️ ~30 min", "callback_data": "SET_TIME_0.5"}, 
         {"text": "⏱️ ~1 Hora", "callback_data": "SET_TIME_1"}],
        [{"text": "⏱️ ~2 Horas", "callback_data": "SET_TIME_2"}, 
         {"text": "⏱️ 3+ Horas", "callback_data": "SET_TIME_3"}]
    ]}

# --- 1. HELPER VISUAL DE DÍAS ---
def format_days_text(days_list):
    if not days_list or len(days_list) == 7: return "Diario"
    if days_list == [0,1,2,3,4]: return "Lun-Vie"
    if days_list == [5,6]: return "Fin de Semana"
    names = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
    return ",".join([names[i] for i in days_list])


# --- 2. ACTUALIZAR FUNCIÓN GENERADORA DE RESUMEN ---
def generate_summary_card(user_name, alerts, vehicle, locations, plan_status, transport_data=None):
    def clean(text):
        return str(text).replace("_", " ").replace("*", "").replace("[", "").replace("]", "")

    safe_plan = clean(plan_status)
    is_premium = "PREMIUM" in safe_plan.upper() or "TRIAL" in safe_plan.upper()
    
    if is_premium:
        is_active_db = alerts.get('contingency', False)
        contingency_status = "✅ **ACTIVA**" if is_active_db else "🔕 **DESACTIVADA**"
    else:
        contingency_status = "🔒 **BLOQUEADA** (Solo Premium)"
    
    locs = []
    if isinstance(locations, dict):
        for k, v in locations.items():
            safe_k = clean(k.capitalize())
            safe_name = clean(v.get('display_name','Ubicación'))
            locs.append(f"• **{safe_k}:** {safe_name}")
    loc_str = "\n".join(locs) if locs else "• *Sin ubicaciones guardadas*"

    # --- NUEVO: Procesar Transporte ---
    if transport_data and transport_data.get('medio'):
        medio_raw = transport_data.get('medio')
        horas = transport_data.get('horas', 0)
        
        nombres_medios = {
            "auto_ac": "🚗 Auto (A/C)", "suburbano": "🚆 Tren Suburbano", "cablebus": "🚡 Cablebús",
            "metro": "🚇 Metro/Tren", "metrobus": "🚌 Metrobús", "auto_ventana": "🚗 Auto (Ventanillas)",
            "combi": "🚐 Combi/Micro", "caminar": "🚶 Caminar", "bicicleta": "🚲 Bici", "home_office": "🏠 Home Office"
        }
        medio_str = nombres_medios.get(medio_raw, medio_raw.capitalize())
        
        if medio_raw == "home_office":
            trans_str = f"• Modalidad: **{medio_str}**"
        else:
            trans_str = f"• Ruta: **Casa ↔ Trabajo**\n• Modo: **{medio_str}**\n• Tiempo: **{horas} hrs/día**"
    else:
        trans_str = "• *Sin configurar (Escribe 'Viajo en metro 2 horas')*"

    veh_str = "• *Sin auto registrado*"
    if vehicle and vehicle.get('active'):
        digit = vehicle.get('plate_last_digit')
        holo = clean(vehicle.get('hologram'))
        veh_str = f"• Placa **{digit}** (Holo {holo})"

    threshold_list = []
    thresholds = alerts.get('threshold', {})
    for k, v in thresholds.items():
        if v.get('active') and k in locations: 
            safe_k = clean(k.capitalize())
            threshold_list.append(f"• {safe_k}: > {v.get('umbral')} pts")
    threshold_str = "\n".join(threshold_list) if threshold_list else "• *Sin alertas de umbral*"

    schedule_list = []
    schedules = alerts.get('schedule', {})
    for k, v in schedules.items():
        if v.get('active') and k in locations: 
            safe_k = clean(k.capitalize())
            days = v.get('days', [])
            days_txt = "Diario" if len(days)==7 else "Días selec."
            schedule_list.append(f"• {safe_k}: {v.get('time')} hrs ({days_txt})")
    schedule_str = "\n".join(schedule_list) if schedule_list else "• *Sin reportes programados*"

    # ====================================================
    # AQUÍ ESTÁ EL BLOQUE DEL HOY NO CIRCULA DINÁMICO
    # ====================================================
    if vehicle and vehicle.get('active'):
        plate = vehicle.get('plate_last_digit')
        holo = vehicle.get('hologram')
        # Calculamos al vuelo si circula HOY (asumimos Fase regular para el resumen rápido)
        can_drive, r_short, _ = check_driving_status(plate, holo, "hoy", "None")
        status_text = "🟢 CIRCULA" if can_drive else "🔴 NO CIRCULA"
        hnc_str = f"• Hoy: **{status_text}** ({r_short})"
    else:
        hnc_str = "• 🔕 Registra tu auto para ver restricciones." 
    # ====================================================

    tip = "Tip: Dile al bot 'Cambia mi transporte a...' para ajustar tu rutina."

    return CARD_SUMMARY.format(
        user_name=clean(user_name),
        plan_status=safe_plan,
        contingency_status=contingency_status,
        locations_list=loc_str,
        transport_info=trans_str,  # <--- SE INYECTA AQUÍ
        vehicle_info=veh_str,
        alerts_threshold=threshold_str,
        alerts_schedule=schedule_str,
        hnc_reminder=hnc_str,
        tip_footer=tip
    )

# --- 3. ACTUALIZAR BOTONES DE RESUMEN (UPSELLING) ---
def get_summary_buttons(locations_dict, is_premium=False):
    """
    Genera botones de consulta para TODAS las ubicaciones guardadas.
    Argumentos:
      - locations_dict: El diccionario 'locations' directo de DynamoDB.
      - is_premium: Booleano para mostrar/ocultar botón de pago.
    """
    keyboard = []
    
    # 1. Fila de Consultas (Dinámica)
    # Creamos botones para CADA ubicación en el diccionario
    row_locs = []
    for key, val in locations_dict.items():
        # Nombre bonito para el botón
        label = val.get('display_name', key.capitalize())
        # Llave segura para el callback (ej. "CHECK_AIR_casa")
        safe_key = str(key).replace(" ", "_")
        
        row_locs.append({"text": f"💨 {label}", "callback_data": f"CHECK_AIR_{safe_key}"})
    
    # Si son muchas, las dividimos en filas de 2 para que no se vea feo
    # (Chunking list into size 2)
    for i in range(0, len(row_locs), 2):
        keyboard.append(row_locs[i:i+2])
    
    # 2. Fila de Upselling / Menú Avanzado
    if not is_premium:
        # Aquí mutamos el botón viejo al nuevo "Go Premium"
        keyboard.append([{"text": "💎 Go Premium", "callback_data": "GO_PREMIUM"}])
    else:
        # Y si YA ES premium, le mostramos su portal avanzado
        keyboard.append([{"text": "⚙️ Configuración Avanzada", "callback_data": "CONFIG_ADVANCED"}])
        
    return {"inline_keyboard": keyboard}

# --- MODIFICADO: ELIMINAMOS BOTÓN DE VOLVER ---
def get_locations_buttons(locations_dict):
    keyboard = []
    # Fila de "Consultar Aire"
    row_check = []
    # Fila de "Eliminar"
    row_delete = []
    
    for key, val in locations_dict.items():
        label = key.capitalize()
        # Claves cortas para callback (evitar límite de bytes de Telegram)
        safe_key = key.upper().replace(" ", "_")[:15] 
        
        row_check.append({"text": f"💨 {label}", "callback_data": f"CHECK_AIR_{safe_key}"})
        row_delete.append({"text": f"🗑️ {label}", "callback_data": f"DELETE_LOC_{safe_key}"})
    
    if row_check: keyboard.append(row_check)
    if row_delete: keyboard.append(row_delete)
    
    return {"inline_keyboard": keyboard}

#Helper para confirmación de borrado
def get_delete_confirmation_buttons(location_key):
    return {"inline_keyboard": [
        [
            {"text": "✅ Sí, borrar todo", "callback_data": f"CONFIRM_DEL_{location_key.upper()}"},
            {"text": "❌ Cancelar", "callback_data": "CANCEL_DELETE"}
        ]
    ]}

# --- BOTONES VIRALES (COMPARTIR) ---
def get_share_exposure_button(cigarros, dias):
    """Botón para compartir el desgaste celular (Gamificación)"""
    texto = f"😷 Ayer respiré el equivalente a {cigarros} cigarros invisibles en el tráfico de la ciudad y sumé {dias} días extra a mi Edad Urbana.\n\nDescubre tu exposición y protégete con AIreGPT 🏙️👇"
    url_segura = urllib.parse.quote(texto)
    link_share = f"https://t.me/share/url?url=https://t.me/airegptcdmx_bot&text={url_segura}"
    
    return {"inline_keyboard": [
        [{"text": "🚀 Compartir mi resultado", "url": link_share}]
    ]}

def get_share_contingency_button():
    """Botón para compartir la alerta de contingencia"""
    texto = "🚨 ¡Contingencia Ambiental Activa! 🚨\n\nCheca si tu auto circula hoy, evita multas y ve las medidas de salud actualizadas aquí: @airegptcdmx_bot 🚗💨"
    url_segura = urllib.parse.quote(texto)
    link_share = f"https://t.me/share/url?url=https://t.me/airegptcdmx_bot&text={url_segura}"
    
    return {"inline_keyboard": [
        [{"text": "📢 Avisar a mis contactos", "url": link_share}]
    ]}

# =====================================================================
# 🚗 MOTOR HNC V2, SALUD Y PRONÓSTICO (COMPARTIDO BOT Y SCHEDULER)
# =====================================================================
from datetime import datetime, timedelta

MATRIZ_SEMANAL = {5:0, 6:0, 7:1, 8:1, 3:2, 4:2, 1:3, 2:3, 9:4, 0:4}
ENGOMADOS = {5:"Amarillo", 6:"Amarillo", 7:"Rosa", 8:"Rosa", 3:"Rojo", 4:"Rojo", 1:"Verde", 2:"Verde", 9:"Azul", 0:"Azul"}

def format_forecast_block(timeline):
    if not timeline or not isinstance(timeline, list): return "➡️ Estable"
    
    # 1. Obtenemos la hora actual en CDMX
    current_hour = (datetime.utcnow() - timedelta(hours=6)).hour
    
    # 2. Lógica para cruzar la medianoche
    def sort_key(item):
        try:
            h = int(str(item.get('hora', '0')).split(':')[0])
        except ValueError:
            h = 0
        return h if h >= current_hour else h + 24

    # 3. Ordenamos cronológicamente de verdad
    sorted_timeline = sorted(timeline, key=sort_key)
    
    # 4. Armamos el bloque visual optimizado para móviles (Microcopy)
    block = ""
    def get_mini_emoji(r):
        r_lower = str(r).lower()
        if "bajo" in r_lower: return "🟢"
        if "moderado" in r_lower: return "🟡"
        if "muy alto" in r_lower: return "🔴"
        if "extrem" in r_lower: return "🟣"
        return "🟠" # Alto
    
    # Tomamos solo las próximas 4 horas ya ordenadas
    for t in sorted_timeline[:4]:
        riesgo = t.get('riesgo', 'Bajo')
        emoji = get_mini_emoji(riesgo)
        
        # MICROCOPY: Acortamos para que quepa en una línea de celular
        riesgo_corto = str(riesgo).replace("Extremadamente Alto", "Extremo").replace("Moderado", "Mod")
        contam = f" • {t.get('dominante')}" if t.get('dominante') else ""
        
        # Formato monospace para la hora y sin la palabra 'pts' ni separador '|'
        block += f"`{t.get('hora')}` {emoji} {riesgo_corto} ({t.get('ias')}){contam}\n"
        
    return block.strip()

def get_verification_period(plate_digit, hologram):
    if str(hologram).lower() in ['00', 'exento', 'hibrido']: return "🟢 EXENTO (No verifica)"
    try: d = int(plate_digit)
    except: return "⚠️ Revisar Placa"

    if d in [5, 6]: return "🟡 Ene-Feb / Jul-Ago"
    if d in [7, 8]: return "🌸 Feb-Mar / Ago-Sep"
    if d in [3, 4]: return "🔴 Mar-Abr / Sep-Oct"
    if d in [1, 2]: return "🟢 Abr-May / Oct-Nov"
    if d in [9, 0]: return "🔵 May-Jun / Nov-Dic"
    return "📅 Revisar Calendario"

def check_driving_status(plate_last_digit, hologram, date_str=None, contingency_phase="None"):
    """Retorna: (Puede_Circular: Bool, Razon_Corta: Str, Detalle_Visual: Str)"""
    try:
        if not date_str or date_str.lower() == "hoy":
            date_str = (datetime.utcnow() - timedelta(hours=6)).strftime("%Y-%m-%d")
            
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_week, day_month = dt.weekday(), dt.day
        
        holo = str(hologram).lower().replace("holograma", "").strip()
        plate = int(plate_last_digit)
        color = ENGOMADOS.get(plate, "Desconocido")

        if day_week == 6: return True, "Domingo libre", "🟢 CIRCULA (Es domingo)."
        
        if contingency_phase in ['Fase I', 'Fase 1', 'Fase II', 'Fase 2']:
            is_fase2 = 'II' in contingency_phase.upper() or '2' in contingency_phase
            if holo in ['2', 'foraneo']: return False, "Restricción Fase I/II", f"🔴 NO CIRCULA."
            if holo == '1':
                if is_fase2: return False, "Fase II Activa", "🔴 NO CIRCULA."
                if MATRIZ_SEMANAL.get(plate) == day_week: return False, "Día Habitual", f"🔴 NO CIRCULA."
                if (plate % 2 != 0): return False, "Fase I (Placas Impares)", "🔴 NO CIRCULA."
            if holo in ['0', '00', 'exento'] and not is_fase2:
                if MATRIZ_SEMANAL.get(plate) == day_week: return False, f"Fase I (Eng. {color})", f"🔴 NO CIRCULA."
            if holo in ['0', '00'] and is_fase2:
                if MATRIZ_SEMANAL.get(plate) == day_week: return False, f"Fase II (Eng. {color})", f"🔴 NO CIRCULA."

        if holo in ['0', '00', 'exento', 'hibrido', 'eléctrico']: return True, "Holograma Exento", "🟢 CIRCULA."
        
        if day_week < 5:
            if MATRIZ_SEMANAL.get(plate) == day_week: return False, f"Día Habitual", f"🔴 NO CIRCULA."
            return True, "Día Permitido", "🟢 CIRCULA."

        if day_week == 5:
            if holo in ['2', 'foraneo']: return False, "Sábado Holo 2", "🔴 NO CIRCULA."
            if holo == '1':
                sat_idx, is_impar = (day_month - 1) // 7 + 1, (plate % 2 != 0)
                if sat_idx == 5: return False, "5º Sábado", "🔴 NO CIRCULA."
                if is_impar and sat_idx in [1, 3]: return False, f"{sat_idx}º Sábado (Impar)", f"🔴 NO CIRCULA."
                if not is_impar and sat_idx in [2, 4]: return False, f"{sat_idx}º Sábado (Par)", f"🔴 NO CIRCULA."
                return True, "Sábado Permitido", "🟢 CIRCULA."
        return True, "Sin Restricción", "🟢 CIRCULA."
    except Exception: return True, "Error", "⚠️ Error al calcular."

def build_hnc_pill(vehicle, contingency_phase="None", is_premium=False):
    if not vehicle or not vehicle.get('active'): return ""
    
    # 🔒 BLOQUEO PARA USUARIOS FREE
    if not is_premium:
        return "\n🚗 **Tu Auto Hoy:** 🔒 Exclusivo Premium"
    
    plate = vehicle.get('plate_last_digit')
    holo = vehicle.get('hologram')
    color_auto = ENGOMADOS.get(int(plate), "Desconocido")
    
    can_drive, r_short, _ = check_driving_status(plate, holo, "hoy", contingency_phase)
    hnc_status = "🟢 CIRCULA" if can_drive else f"⛔ NO CIRCULA ({r_short})"
    
    pill = f"\n🚗 **Tu Auto Hoy:** {hnc_status} \n*(Placa term. {plate} | Holo {holo} | Eng. {color_auto})*"

    periodo_verif = get_verification_period(plate, holo)
    mes_actual_txt = {1:"Ene", 2:"Feb", 3:"Mar", 4:"Abr", 5:"May", 6:"Jun", 7:"Jul", 8:"Ago", 9:"Sep", 10:"Oct", 11:"Nov", 12:"Dic"}[(datetime.utcnow() - timedelta(hours=6)).month]
    if mes_actual_txt in periodo_verif and "EXENTO" not in periodo_verif.upper():
        pill += f"\n⚠️ **RECORDATORIO:** Estás en periodo de Verificación ({periodo_verif})."
        
    return pill

# --- BOTONES PARA HOY NO CIRCULA ---
def get_hnc_buttons():
    """Agrega el botón interactivo a las consultas de movilidad"""
    return {
        "inline_keyboard": [
            [{"text": "📊 Mi Resumen", "callback_data": "ver_resumen"}]
        ]
    }
