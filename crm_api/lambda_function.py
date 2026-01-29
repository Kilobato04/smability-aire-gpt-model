import json
import boto3
import os
from decimal import Decimal
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# --- CONFIGURACIÓN ---
TABLE_NAME = 'SmabilityUsers'
ADMIN_API_KEY = os.environ.get('CRM_API_KEY', 'smability-secret-admin') 

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)

# --- 🧠 REGLAS DE NEGOCIO (QUOTAS) ---
# Deben coincidir con las del Bot para consistencia
BUSINESS_RULES = {
    "FREE": {
        "loc_limit": 2, 
        "alert_limit": 0, 
        "can_contingency": False,
        "price": {"amount": 0, "freq": "N/A", "name": "Básico"}
    },
    "PREMIUM_MONTHLY": {
        "loc_limit": 3, 
        "alert_limit": 10, 
        "can_contingency": True,
        "price": {"amount": 49.00, "freq": "Mensual", "name": "Premium Mensual"}
    },
    "PREMIUM_ANNUAL": {
        "loc_limit": 3, 
        "alert_limit": 10, 
        "can_contingency": True,
        "price": {"amount": 329.00, "freq": "Anual", "name": "Premium Anual"}
    }
}

# --- HELPERS DE TIEMPO ---
def to_mexico_time(iso_str):
    if not iso_str: return None
    try:
        dt = datetime.fromisoformat(str(iso_str).replace('Z', '+00:00'))
        return dt.astimezone(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d %I:%M %p")
    except: return str(iso_str)

def days_between(date_iso):
    """Calcula días pasados desde la fecha dada hasta hoy"""
    if not date_iso: return 0
    try:
        dt = datetime.fromisoformat(str(date_iso).replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        delta = now - dt
        return abs(delta.days)
    except: return 0

# --- LÓGICA DE ENRIQUECIMIENTO (EL CEREBRO DEL CRM) ---
def enrich_user_data(item):
    # 1. Extracción Segura
    sub_raw = item.get('subscription', {})
    profile_raw = item.get('profile', {})
    locs_raw = item.get('locations', {})
    alerts_raw = item.get('alerts', {})
    metrics_raw = item.get('metrics', {}) # Contadores históricos

    # 2. Identificar Plan y Reglas
    status = sub_raw.get('status', 'FREE')
    # Intentamos buscar el tier específico (ej. PREMIUM_ANNUAL), si no, fallback al status
    tier_key = sub_raw.get('tier', status)
    
    # Obtener reglas (Buscamos tier exacto -> Status general -> Fallback a Free)
    rules = BUSINESS_RULES.get(tier_key, BUSINESS_RULES.get(status, BUSINESS_RULES.get('FREE')))
    
    # Fallback de seguridad por si rules es None (raro)
    if not rules: rules = BUSINESS_RULES['FREE']
        
    pricing = rules.get('price', {"amount": 0, "freq": "N/A", "name": "Desconocido"})

    # 3. Calcular Uso (Quotas Used)
    locs_used = len(locs_raw)
    
    # Contar alertas activas reales (Schedule + Threshold)
    alerts_used = 0
    for place in locs_raw:
        if alerts_raw.get('schedule', {}).get(place, {}).get('active'): alerts_used += 1
        if alerts_raw.get('threshold', {}).get(place, {}).get('active'): alerts_used += 1

    # 4. Construcción de Locations Snapshot (Merge de Ubicación + Config)
    locations_snapshot = []
    for place_name, coords in locs_raw.items():
        # Extraer configs específicas para este lugar
        sched_cfg = alerts_raw.get('schedule', {}).get(place_name, {})
        thresh_cfg = alerts_raw.get('threshold', {}).get(place_name, {})
        
        # Formatear valor umbral para display
        thresh_val = thresh_cfg.get('umbral')
        thresh_display = f"> {thresh_val} IMA" if thresh_val else None

        locations_snapshot.append({
            "name": place_name,
            "coords": {"lat": coords.get('lat'), "lon": coords.get('lon')},
            "is_active": coords.get('active', True),
            "config": {
                "schedule_report": sched_cfg.get('time'),  # Ej: "07:30"
                "threshold_alert": thresh_display,         # Ej: "> 100 IMA"
                "consecutive_sent": thresh_cfg.get('consecutive_sent', 0) # Debug
            }
        })

    # 5. Cálculo de Renovación
    valid_until = sub_raw.get('valid_until')
    renewal_text = "N/A"
    if valid_until:
        # Aquí asumimos que valid_until es futuro, calculamos días faltantes
        # Si la fecha ya pasó, saldrá negativo (indicando vencido)
        days_left = -1 * days_between(valid_until) # Simplificado
        renewal_text = f"Vence: {valid_until}"

    # --- ENSAMBLAJE FINAL DEL JSON ---
    return {
        "user_id": item.get('user_id'),
        "name": item.get('first_name', 'Sin Nombre'),
        "email": item.get('email', None),
        "status": status,

        # 📊 Métricas de Retención
        "crm_metrics": {
            "first_seen": to_mexico_time(item.get('created_at')),
            "last_seen": to_mexico_time(item.get('last_interaction')),
            "days_inactive": days_between(item.get('last_interaction')), # 🚨 VITAL
            "total_requests": metrics_raw.get('total_requests', 0),
            "alerts_received": metrics_raw.get('alerts_received', 0)
        },

        # 💰 Suscripción
        "subscription": {
            "plan_name": pricing['name'],
            "tier_id": tier_key,
            "amount": pricing['amount'],
            "currency": "MXN",
            "frequency": pricing['freq'],
            "stripe_customer_id": sub_raw.get('stripe_customer_id'),
            "valid_until": valid_until,
            "auto_renew": sub_raw.get('auto_renew', False),
            "next_renewal_human": renewal_text
        },

        # 🚦 Semáforo de Permisos
        "permissions": {
            "can_chat_bot": True,
            "can_create_alerts": (rules['alert_limit'] > 0),
            "can_receive_contingency": rules['can_contingency'],
            "can_add_more_locations": (locs_used < rules['loc_limit'])
        },

        # 🔢 Cupos (Barras de Progreso)
        "quotas": {
            "locations": {
                "used": locs_used,
                "limit": rules['loc_limit'],
                "remaining": max(0, rules['loc_limit'] - locs_used)
            },
            "alerts": {
                "used": alerts_used,
                "limit": rules['alert_limit'],
                "remaining": max(0, rules['alert_limit'] - alerts_used)
            }
        },

        # 🩺 Perfil
        "profile": {
            "health_tags": profile_raw.get('tags', []),
            "device_os": profile_raw.get('device_os', 'Desconocido'),
            "language": profile_raw.get('language', 'es')
        },

        # 📍 Mapa Operativo
        "locations_snapshot": locations_snapshot,

        # 🔔 Config Global
        "global_config": {
            "contingency_enabled": alerts_raw.get('contingency', {}).get('enabled', False),
            "contingency_last_received": alerts_raw.get('contingency', {}).get('last_received')
        }
    }

# --- HANDLER Y SERIALIZACIÓN ---
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal): return float(obj)
        return super(DecimalEncoder, self).default(obj)

def response(status, body):
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
        'body': json.dumps(body, cls=DecimalEncoder)
    }

def lambda_handler(event, context):
    qs = event.get('queryStringParameters') or {}
    headers = event.get('headers') or {}
    incoming_key = headers.get('x-admin-key') or qs.get('key')
    
    if incoming_key != ADMIN_API_KEY:
        return response(403, {'error': 'Unauthorized'})

    try:
        action = qs.get('action')
        body_data = {}
        if event.get('body'):
            body_data = json.loads(event.get('body'))
            action = body_data.get('action', action)
        
        # --- LISTAR (Tabla Resumen Enriquecida) ---
        if action == 'list_users':
            res = table.scan()
            items = res.get('Items', [])
            enriched_list = [enrich_user_data(u) for u in items]
            
            # Ordenar por el más activo recientemente
            enriched_list.sort(key=lambda x: str(x['crm_metrics'].get('last_seen', '')), reverse=True)
            return response(200, {'count': len(enriched_list), 'users': enriched_list})

        # --- DETALLE (Ficha Técnica Completa) ---
        elif action == 'get_user':
            uid = qs.get('user_id')
            if not uid: return response(400, {'error': 'Falta user_id'})
            res = table.get_item(Key={'user_id': str(uid)})
            item = res.get('Item')
            if not item: return response(404, {'error': 'Usuario no encontrado'})
            return response(200, enrich_user_data(item))
            
        # --- UPDATE (Gestión) ---
        elif action == 'update_user':
            uid = body_data.get('user_id')
            updates = body_data.get('updates', {})
            if not uid or not updates: return response(400, {'error': 'Faltan datos'})
            
            update_expr = "SET "; expr_vals = {}; expr_names = {}; parts = []
            for i, (k, v) in enumerate(updates.items()):
                kt = f"#k{i}"; vt = f":v{i}"
                parts.append(f"{kt} = {vt}"); expr_names[kt] = k; expr_vals[vt] = v
            update_expr += ", ".join(parts)
            
            table.update_item(Key={'user_id': str(uid)}, UpdateExpression=update_expr, ExpressionAttributeNames=expr_names, ExpressionAttributeValues=expr_vals)
            return response(200, {'status': 'updated'})

        else:
            return response(400, {'error': f'Accion desconocida: {action}'})

    except Exception as e:
        print(f"ERROR: {e}")
        return response(500, {'error': str(e)})
