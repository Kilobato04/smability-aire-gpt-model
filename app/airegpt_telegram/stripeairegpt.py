import time
from datetime import datetime, timedelta
import urllib.parse
import cards # <--- Importamos nuestro UI centralizado

# ==========================================
# 1. CONFIGURACIÓN DE STRIPE (SANDBOX)
# ==========================================
STRIPE_LINK_MONTHLY = "https://buy.stripe.com/6oUdRafiH4DY0o03j02Ji06"
STRIPE_LINK_SEMESTRAL = "https://buy.stripe.com/cNi9AU8Ujdau8Uwf1I2Ji07"
STRIPE_LINK_ANNUAL = "https://buy.stripe.com/aFa8wQ3zZ4DY8UwbPw2Ji08"

TRIAL_DAYS = 3

def get_mexico_time():
    return datetime.utcnow() - timedelta(hours=6)

# ==========================================
# 2. EL GATEKEEPER (Evaluador de Estatus con Trial)
# ==========================================
def evaluate_user_tier(user_item):
    """
    Evalúa al usuario y retorna:
    status: 'PREMIUM', 'TRIAL', o 'FREE'
    days_left: int (si está en trial)
    """
    sub = user_item.get('subscription', {})
    current_status = sub.get('status', 'FREE').upper()
    
    # 1. Si ya es Premium o admin, pase VIP
    if "PREMIUM" in current_status or "MANUAL" in current_status:
        return 'PREMIUM', 0
        
    # 2. Evaluar Trial Dinámico (Solo si es FREE)
    if "FREE" in current_status:
        # Buscamos la fecha de creación o su última interacción como salvavidas
        created_at_str = user_item.get('created_at', user_item.get('last_interaction'))
        
        if created_at_str:
            try:
                # Formato ISO
                created_dt = datetime.fromisoformat(created_at_str.split('.')[0]) 
                
                # 🚀 FIX: Usamos UTC directo para coincidir con la BD
                now = datetime.utcnow() 
                
                # Calculamos cuántos días han pasado
                delta_days = (now - created_dt).days
                
                if delta_days <= TRIAL_DAYS:
                    days_left = TRIAL_DAYS - delta_days
                    return 'TRIAL', max(1, days_left)
            except Exception as e:
                print(f"Error calculando Trial: {e}")
                pass
                
    # 3. Fallback: El usuario es FREE y ya se le acabó su prueba
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
# 5. GENERADOR DEL PORTAL DE GESTIÓN (RECOMENDADO)
# ==========================================
def get_management_portal_link():
    """
    Retorna el link directo al portal de Stripe configurado en el Dashboard.
    Stripe se encarga de la autenticación vía Email del usuario.
    """
    # Este es tu link oficial de Test Mode
    return "# ==========================================
# 5. GENERADOR DEL PORTAL DE GESTIÓN (RECOMENDADO)
# ==========================================
def get_management_portal_link():
    """
    Retorna el link directo al portal de Stripe configurado en el Dashboard.
    Stripe se encarga de la autenticación vía Email del usuario.
    """
    # 🚀 FIX: Este es tu link oficial de LIVE Mode (Producción)
    return "https://billing.stripe.com/p/login/3cI3cw8Uj1rM5Ikg5M2Ji00"
