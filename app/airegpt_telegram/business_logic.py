import stripeairegpt  # 🚩 IMPORT NECESARIO
# business_logic.py - El Cerebro de AIreGPT
# Centraliza las reglas de suscripción para Chatbot y Scheduler

def get_user_tier(user_profile):
    """
    Determina si el usuario es PREMIUM o FREE basándose en su perfil de DynamoDB.
    """
    sub = user_profile.get('subscription', {})
    status = str(sub.get('status', 'FREE')).upper()
    
    # PREMIUM incluye: PREMIUM real, TRIAL activo o MANUAL (Devs)
    is_premium = any(x in status for x in ["PREMIUM", "TRIAL", "MANUAL"])
    return "PREMIUM" if is_premium else "FREE"

def get_tier_config(user_profile):
    """
    Retorna el diccionario de capacidades basado en el tier del usuario.
    """
    tier = get_user_tier(user_profile)
    
    if tier == "PREMIUM":
        return {
            "tier_name": "PREMIUM",
            "max_locations": 3,
            "can_custom_alerts": True,      # Alertas a cualquier hora
            "can_contingency": True,       # Recibe avisos de contingencia
            "can_gamification": True,      # Acceso a Serpiente y Tetris
            "can_mobility_active": True,   # ¿Circulo mañana?, Calendario mensual
            "can_custom_routine": True,    # Cambiar transporte y > 2 horas
            "show_locks": False,           # Tarjetas limpias
            "lock_emoji": ""
        }
    else:
        return {
            "tier_name": "FREE",
            "max_locations": 2,            # Solo Casa y Trabajo
            "fixed_reminder_hour": "09:00",# Única hora permitida para recordatorio
            "fixed_threshold": 100,        # Único umbral de emergencia permitido
            "can_custom_alerts": False,
            "can_contingency": False,
            "can_gamification": False,
            "can_mobility_active": False,
            "can_custom_routine": False,   # Bloqueado cambio de rutina profundo
            "show_locks": True,            # Activa los candados visuales
            "lock_emoji": "🔒"
        }

def is_action_allowed(user_profile, action_type):
    """
    Validador de seguridad para el Orquestador y Tools.
    Retorna (Allowed: bool, Reason: str)
    """
    # --- 💎 LA REGLA DE ORO (PREMIUM FIRST) ---
    # Evaluamos el tier real. Si es Premium o Trial, no hay restricciones.
    tier_real, _ = stripeairegpt.evaluate_user_tier(user_profile)
    if tier_real in ['PREMIUM', 'TRIAL']:
        return True, "Acceso total Premium."
    # ------------------------------------------

    # Si llegamos aquí, el usuario es FREE (o falló la detección)
    config = get_tier_config(user_profile)
    user_tier = config.get("tier_name", "FREE")
    
    # 1. Ubicaciones (Límite dinámico)
    if action_type == "add_location":
        locs = user_profile.get('locations', {})
        active_count = len([k for k, v in locs.items() if isinstance(v, dict) and v.get('active')])
        if active_count >= config["max_locations"]:
            return False, f"Límite de {config['max_locations']} ubicaciones alcanzado."
            
    # 2. Movilidad Activa (Calendario y Verificación)
    if action_type in ["movilidad_mensual", "get_monthly_calendar", "consultar_verificacion"]:
        if not config["can_mobility_active"]:
            return False, "Las consultas de calendario mensual y periodos de verificación son funciones Premium."    

    # 3. Gráficas y Gamificación
    if action_type in ["get_graphic", "get_tetris", "tetris", "serpiente"]:
        if not config["can_gamification"]:
            return False, "Las gráficas avanzadas de exposición son exclusivas para usuarios Premium."

    # 4. Personalización Pro (Salud, Transporte, Alertas y Rutina)
    # UNIFICADO: Si es FREE, todo esto rebota.
    pro_actions = [
        "guardar_salud", 
        "configurar_transporte", 
        "configure_routine", 
        "alertas", 
        "configurar_recordatorio",
        "configurar_alerta_contingencia",
        "calcular_exposicion_diaria"  # 🚩 Aseguramos que el cálculo de cigarros pase por aquí
    ]
    if action_type in pro_actions:
        if user_tier == 'FREE':
            return False, "La personalización de salud, transporte, alertas horarias y umbrales de IAS es exclusiva de AIreGPT Premium."

    return True, "OK"
