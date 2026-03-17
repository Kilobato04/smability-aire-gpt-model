import json
import boto3
import os
import traceback
import base64
from decimal import Decimal
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# --- CONFIGURACIÓN ---
TABLE_NAME = 'SmabilityUsers'
ADMIN_API_KEY = os.environ.get('CRM_API_KEY', 'smability-secret-admin') 

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)

# --- 🧠 REGLAS DE NEGOCIO (QUOTAS) ---
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
    },
    # 👇 AGREGAMOS ESTOS DOS PARA SOPORTAR EL COMANDO /PROMO 👇
    "PREMIUM_MANUAL": {
        "loc_limit": 3, 
        "alert_limit": 10, 
        "can_contingency": True,
        "price": {"amount": 0, "freq": "Manual", "name": "Premium Dev (Gratis)"}
    },
    "PREMIUM": { # Fallback genérico
        "loc_limit": 3, 
        "alert_limit": 10, 
        "can_contingency": True,
        "price": {"amount": 0, "freq": "Genérico", "name": "Premium"}
    }
}

# --- 🛡️ HELPER DE SANITIZACIÓN (EL SALVAVIDAS) ---
def safe_dict(val):
    """Asegura que el valor sea un diccionario, aunque venga como string o null"""
    if val is None: return {}
    if isinstance(val, dict): return val
    if isinstance(val, str):
        try:
            # Intentamos reparar strings JSON
            return json.loads(val.replace("'", '"')) 
        except:
            return {} # Si es basura, devolvemos vacío para no romper
    return {}

# --- HELPERS DE TIEMPO ---
def to_mexico_time(iso_str):
    if not iso_str: return None
    try:
        dt = datetime.fromisoformat(str(iso_str).replace('Z', '+00:00'))
        return dt.astimezone(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d %I:%M %p")
    except: return str(iso_str)

def days_between(date_iso):
    if not date_iso: return 0
    try:
        dt = datetime.fromisoformat(str(date_iso).replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        return abs((now - dt).days)
    except: return 0

# --- LÓGICA DE ENRIQUECIMIENTO ---
def enrich_user_data(item):
    user_id = item.get('user_id', 'unknown')
    try:
        # 1. Extracción Segura
        sub_raw = safe_dict(item.get('subscription'))
        profile_raw = safe_dict(item.get('profile'))
        locs_raw = safe_dict(item.get('locations'))
        alerts_raw = safe_dict(item.get('alerts'))
        metrics_raw = safe_dict(item.get('metrics'))

        # 2. Identificar Plan (Usando BUSINESS_RULES interno)
        status = sub_raw.get('status', 'FREE')
        tier_key = sub_raw.get('tier', status)
        
        rules = BUSINESS_RULES.get(tier_key, BUSINESS_RULES.get(status, BUSINESS_RULES.get('FREE')))
        if not rules: rules = BUSINESS_RULES['FREE']
        pricing = rules.get('price', {"amount": 0, "freq": "N/A", "name": "Desconocido"})

        # 3. Calcular Uso
        locs_used = len(locs_raw)
        alerts_used = 0
        
        schedule_alerts = safe_dict(alerts_raw.get('schedule'))
        threshold_alerts = safe_dict(alerts_raw.get('threshold'))

        for place in locs_raw:
            if safe_dict(schedule_alerts.get(place)).get('active'): alerts_used += 1
            if safe_dict(threshold_alerts.get(place)).get('active'): alerts_used += 1

        # 4. Snapshot de Ubicaciones
        locations_snapshot = []
        for place_name, coords in locs_raw.items():
            coords = safe_dict(coords) 
            sched_cfg = safe_dict(schedule_alerts.get(place_name))
            thresh_cfg = safe_dict(threshold_alerts.get(place_name))
            thresh_val = thresh_cfg.get('umbral')
            thresh_display = f"> {thresh_val} IMA" if thresh_val else None

            locations_snapshot.append({
                "name": place_name,
                "coords": {"lat": coords.get('lat'), "lon": coords.get('lon')},
                "is_active": coords.get('active', True),
                "config": {
                    "schedule_report": sched_cfg.get('time'),
                    "threshold_alert": thresh_display,
                    "consecutive_sent": thresh_cfg.get('consecutive_sent', 0)
                }
            })

        # 5. Renovación
        valid_until = sub_raw.get('valid_until')
        renewal_text = "N/A"
        if valid_until:
            days_left = -1 * days_between(valid_until)
            renewal_text = f"Vence: {valid_until}"

        # --- NUEVA EXTRACCIÓN: SALUD, AUTO Y TRANSPORTE ---
        veh_raw = safe_dict(item.get('vehicle'))
        health_prof_raw = safe_dict(item.get('health_profile'))
        health_stats_raw = safe_dict(item.get('health_stats'))
        transport_raw = safe_dict(item.get('profile_transport'))

        current_health = health_stats_raw.get('current_week', [{}])[0] if isinstance(health_stats_raw.get('current_week'), list) and len(health_stats_raw.get('current_week')) > 0 else {}
        if isinstance(current_health, dict) and 'M' in current_health: current_health = current_health['M'] 
            
        # --- RETURN FINAL ---
        return {
            "user_id": user_id,
            "name": item.get('first_name', 'Sin Nombre'),
            "email": item.get('email', None),
            "status": status,
            
            "crm_metrics": {
                "first_seen": to_mexico_time(item.get('created_at')),
                "last_seen": to_mexico_time(item.get('last_interaction')),
                "days_inactive": days_between(item.get('last_interaction')),
                "total_requests": metrics_raw.get('total_requests', 0),
                "alerts_received": metrics_raw.get('alerts_received', 0)
            },
            
            "subscription": {
                "plan_name": pricing['name'],
                "tier_id": tier_key,
                "amount": pricing['amount'],
                "currency": "MXN",
                "frequency": pricing['freq'],
                "stripe_customer_id": sub_raw.get('stripe_customer_id'),
                "stripe_subscription_id": sub_raw.get('stripe_subscription_id'), 
                "valid_until": valid_until,
                "auto_renew": sub_raw.get('auto_renew', False),
                "next_renewal_human": renewal_text
            },
            
            "permissions": {
                "can_chat_bot": True,
                "can_create_alerts": (rules['alert_limit'] > 0),
                "can_receive_contingency": rules['can_contingency'],
                "can_add_more_locations": (locs_used < rules['loc_limit'])
            },
            
            "quotas": {
                "locations": {"used": locs_used, "limit": rules['loc_limit'], "remaining": max(0, rules['loc_limit'] - locs_used)},
                "alerts": {"used": alerts_used, "limit": rules['alert_limit'], "remaining": max(0, rules['alert_limit'] - alerts_used)}
            },
            
            "profile": {
                "health_tags": profile_raw.get('tags', []),
                "device_os": profile_raw.get('device_os', 'Desconocido'),
                "language": profile_raw.get('language', 'es')
            },
            
            "locations_snapshot": locations_snapshot,
            
            "global_config": {
                "contingency_enabled": alerts_raw.get('contingency', {}).get('enabled', False) if isinstance(alerts_raw.get('contingency'), dict) else bool(alerts_raw.get('contingency')),
                "contingency_last_received": alerts_raw.get('contingency', {}).get('last_received') if isinstance(alerts_raw.get('contingency'), dict) else None
            },

            "vehicle": {
                "active": veh_raw.get('active', False),
                "plate_last_digit": veh_raw.get('plate_last_digit'),
                "hologram": veh_raw.get('hologram'),
                "engomado": veh_raw.get('engomado')
            },
            "health_and_transport": {
                "transport_mode": transport_raw.get('medio', 'No definido'),
                "transport_hours": transport_raw.get('horas', 0),
                "conditions": list(health_prof_raw.keys()), 
                "weekly_damage": {
                    "cigarros": safe_dict(current_health).get('cigarros', '0'),
                    "dias_edad_perdidos": safe_dict(current_health).get('dias_edad', '0')
                }
            }
        }
    except Exception as e:
        print(f"🔥 [CRITICAL] Failed to enrich user {user_id}: {str(e)}")
        traceback.print_exc() 
        return {"user_id": user_id, "error": "Data corrupta", "raw_error": str(e)}

# --- HANDLER ---
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
    print("📨 [INCOMING REQUEST]:", json.dumps(event.get('queryStringParameters')))
    
    qs = event.get('queryStringParameters') or {}
    headers = event.get('headers') or {}
    incoming_key = headers.get('x-admin-key') or qs.get('key')
    
    if incoming_key != ADMIN_API_KEY:
        print(f"⛔ [AUTH FAIL] Invalid Key: {incoming_key}")
        return response(403, {'error': 'Unauthorized'})

    try:
        action = qs.get('action')
        body_data = {}
        if event.get('body'):
            try: body_data = json.loads(event.get('body'))
            except: pass
            action = body_data.get('action', action)
        
        print(f"🚀 [ACTION] Executing: {action}")

        if action == 'list_users':
            limit = int(qs.get('limit', 50)) # Paquetes de 50
            last_key_str = qs.get('last_key')
            
            scan_kwargs = {'Limit': limit}
            if last_key_str:
                # Decodificamos el token de paginación
                scan_kwargs['ExclusiveStartKey'] = json.loads(base64.b64decode(last_key_str).decode('utf-8'))
                
            res = table.scan(**scan_kwargs)
            items = res.get('Items', [])
            next_key_raw = res.get('LastEvaluatedKey')
            
            # Codificamos el siguiente token para el frontend
            next_key_str = None
            if next_key_raw:
                next_key_str = base64.b64encode(json.dumps(next_key_raw).encode('utf-8')).decode('utf-8')
            
            enriched_list = [enrich_user_data(u) for u in items]
            
            # Ordenar seguro la página actual (evitando error si last_seen no existe)
            enriched_list.sort(key=lambda x: str(x.get('crm_metrics', {}).get('last_seen', '')), reverse=True)
            
            print(f"✅ [SUCCESS] Returning {len(enriched_list)} enriched users (Paginated)")
            return response(200, {
                'count': len(enriched_list), 
                'users': enriched_list,
                'next_key': next_key_str # El frontend usará esto para pedir la pág 2
            })

        elif action == 'get_user':
            uid = qs.get('user_id')
            if not uid: return response(400, {'error': 'Falta user_id'})
            res = table.get_item(Key={'user_id': str(uid)})
            item = res.get('Item')
            if not item: return response(404, {'error': 'Usuario no encontrado'})
            return response(200, enrich_user_data(item))
            
        elif action == 'update_user':
            uid = body_data.get('user_id')
            updates = body_data.get('updates', {})
            if not uid or not updates: return response(400, {'error': 'Faltan datos'})
            
            print(f"📝 [UPDATE] User: {uid} | Fields: {list(updates.keys())}")
            
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
        print(f"🔥 [FATAL ERROR]: {e}")
        traceback.print_exc()
        return response(500, {'error': str(e)})
