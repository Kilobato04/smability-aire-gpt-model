import time
from datetime import datetime, timedelta
import urllib.parse
import cards # <--- Importamos nuestro UI centralizado

# ==========================================
# 1. CONFIGURACIÓN DE STRIPE (SANDBOX)
# ==========================================
STRIPE_LINK_MONTHLY = "https://buy.stripe.com/test_3cI3cw8Uj1rM5Ikg5M2Ji00"
STRIPE_LINK_SEMESTRAL = "https://buy.stripe.com/test_9B6bJ20nNb2meeQg5M2Ji02"
STRIPE_LINK_ANNUAL = "https://buy.stripe.com/test_fZuaEY3zZ8Uec6IbPw2Ji01"

TRIAL_DAYS = 5

def get_mexico_time():
    return datetime.utcnow() - timedelta(hours=6)

# ==========================================
# 2. EL GATEKEEPER (Evaluador de Estatus)
# ==========================================
def evaluate_user_tier(user_item):
    """
    Evalúa al usuario y retorna:
    status: 'PREMIUM', 'TRIAL', o 'FREE'
    days_left: int (si está en trial)
    """
    sub = user_item.get('subscription', {})
    current_status = sub.get('status', 'FREE').upper()
    
    # 1. Si ya es Premium, pase VIP
    if "PREMIUM" in current_status:
        return 'PREMIUM', 0
        
    # 2. Evaluar Trial
    created_at_str = user_item.get('created_at')
    if not created_at_str:
        return 'FREE', 0 # Fallback por si acaso
        
    try:
        # Formato ISO (maneja strings con o sin microsegundos)
        created_dt = datetime.fromisoformat(created_at_str.split('.')[0]) 
    except:
        return 'FREE', 0
        
    now = get_mexico_time()
    delta_days = (now - created_dt).days
    
    if delta_days <= TRIAL_DAYS:
        return 'TRIAL', (TRIAL_DAYS - delta_days)
    else:
        return 'FREE', 0

# ==========================================
# 3. GENERADOR DE CHECKOUT (Magia de Tracking)
# ==========================================
def get_checkout_buttons(user_id):
    """
    Genera los botones de pago inyectando el client_reference_id
    para que Stripe sepa quién está pagando.
    """
    # Inyectamos el ID de Telegram a las URLs de Stripe
    url_m = f"{STRIPE_LINK_MONTHLY}?client_reference_id={user_id}"
    url_s = f"{STRIPE_LINK_SEMESTRAL}?client_reference_id={user_id}"
    url_a = f"{STRIPE_LINK_ANNUAL}?client_reference_id={user_id}"
    
    return {
        "inline_keyboard": [
            [{"text": "🔥 Plan Anual ($399/año) - Ahorras 32%", "url": url_a}],
            [{"text": "⚡ Plan Semestral ($229/6m)", "url": url_s}],
            [{"text": "💧 Plan Mensual ($49/mes)", "url": url_m}]
        ]
    }

# ==========================================
# 4. ORQUESTADOR DE PAYWALL
# ==========================================
def get_paywall_response(tier, days_left, attempted_action, user_id):
    """
    Construye la respuesta usando las plantillas de cards.py
    Añadido: Manejo de estatus PREMIUM para evitar Paywalls redundantes.
    """
    action_text = {
        "salud": "añadir tu perfil de salud",
        "rutina": "calcular tu exposición y Edad Urbana",
        "ubicacion3": "guardar una tercera ubicación",
        "alertas": "programar alertas y contingencias automáticas"
    }.get(attempted_action, "usar funciones avanzadas")

    # 💎 CASO PREMIUM: El usuario ya pagó, no le pedimos más.
    if tier == 'PREMIUM':
        texto = ("💎 **Estatus: PREMIUM ACTIVO**\n\n"
                 "Ya tienes acceso total a esta función. Pídeme ver **'mi resumen'** "
                 "para ver tus datos actualizados o configurar más opciones. 🚀")
        return texto, None

    # 🎁 CASO TRIAL: Periodo de gracia.
    elif tier == 'TRIAL':
        texto = cards.CARD_TRIAL_ACTIVE.format(
            action_text=action_text,
            trial_days=TRIAL_DAYS,
            days_left=days_left
        )
        return texto, None

    # 💳 CASO FREE: Muro de pago (Checkout).
    else:
        texto = cards.CARD_PAYWALL.format(action_text=action_text)
        botones = get_checkout_buttons(user_id)
        return texto, botones

# ==========================================
# 5. GENERADOR DEL PORTAL DE GESTIÓN (NUEVO)
# ==========================================
def get_management_portal_link(user_item):
    """
    Retorna el link del portal de Stripe. 
    Si no tiene un customer_id de Stripe, mandamos al login general.
    """
    sub = user_item.get('subscription', {})
    stripe_customer_id = sub.get('stripe_customer_id') # Asegúrate de guardar esto al recibir el webhook de pago

    if stripe_customer_id:
        # Si tienes habilitado el Customer Portal en el Dashboard de Stripe:
        # Se puede generar un link dinámico, pero el "Direct Link" suele ser:
        return "https://billing.stripe.com/p/login/3cI3cw8Uj1rM5Ikg5M2Ji00" # <--- SUSTITUYE POR TU PORTAL ID REAL
    
    # Fallback si no hay ID
    return "https://billing.stripe.com/p/login/"
