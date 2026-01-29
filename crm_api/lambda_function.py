import json
import boto3
import os
from decimal import Decimal

# --- CONFIGURACIÓN ---
TABLE_NAME = 'SmabilityUsers'
ADMIN_API_KEY = os.environ.get('CRM_API_KEY', 'smability-secret-admin') 

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)

# --- PLANTILLA MAESTRA (Homologación) ---
# Esto define qué campos se verán SIEMPRE, aunque no existan en la DB.
def normalize_user(db_item):
    # 1. Estructura Base (Defaults)
    user = {
        "user_id": db_item.get('user_id'),
        "first_name": db_item.get('first_name', 'Usuario Sin Nombre'),
        "email": db_item.get('email', None),  # ✅ Aparecerá como null si no existe
        "created_at": db_item.get('created_at', None),
        "last_interaction": db_item.get('last_interaction', None),
        
        # 💰 MONETIZACIÓN (STRIPE)
        # Si existe en DB lo usa, si no, pone defaults
        "subscription": {
            "status": "FREE",
            "tier": "basic_v1",
            "stripe_customer_id": None,
            "valid_until": None,
            "auto_renew": False
        },
        
        # 🩺 PERFIL & TAGS
        "profile": {
            "tags": [],          # Lista vacía para agregar tags después
            "device_os": None,
            "language": "es"
        },
        
        # 📊 MÉTRICAS CRM
        "metrics": {
            "total_requests": 0,
            "alerts_received": 0,
            "days_active": 0
        },

        # 📍 LOCACIONES (Tal cual vienen de la DB o vacío)
        "locations": db_item.get('locations', {}),
        
        # 🔔 ALERTAS (Tal cual vienen de la DB o vacío)
        "alerts": db_item.get('alerts', {})
    }

    # 2. Fusión Inteligente (Overlay)
    # Si la DB tiene datos reales para subscription/profile/metrics, sobrescribimos los defaults
    if 'subscription' in db_item:
        user['subscription'].update(db_item['subscription'])
        
    if 'profile' in db_item:
        user['profile'].update(db_item['profile'])
        
    if 'metrics' in db_item:
        user['metrics'].update(db_item['metrics'])

    return user

# Helper Decimales
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def response(status, body):
    return {
        'statusCode': status,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(body, cls=DecimalEncoder)
    }

def lambda_handler(event, context):
    qs = event.get('queryStringParameters') or {}
    headers = event.get('headers') or {}
    incoming_key = headers.get('x-admin-key') or qs.get('key')
    
    if incoming_key != ADMIN_API_KEY:
        return response(403, {'error': 'Unauthorized'})

    try:
        method = event.get('requestContext', {}).get('http', {}).get('method', 'GET')
        action = qs.get('action')
        body_data = {}
        
        if method == 'POST' and event.get('body'):
            body_data = json.loads(event.get('body'))
            action = body_data.get('action', action)

        # --- LISTAR (Tabla Resumen) ---
        if action == 'list_users':
            res = table.scan()
            raw_items = res.get('Items', [])
            
            summary_list = []
            for item in raw_items:
                # Normalizamos primero
                u = normalize_user(item)
                
                # Creamos la fila resumen
                summary_list.append({
                    'user_id': u['user_id'],
                    'name': u['first_name'],
                    'email': u['email'],               # Ahora saldrá null en vez de nada
                    'status': u['subscription']['status'],
                    'alerts': len(u['locations']),     # Contador simple
                    'last_seen': u['last_interaction']
                })
            
            # Ordenar
            summary_list.sort(key=lambda x: str(x['last_seen']), reverse=True)
            return response(200, {'count': len(summary_list), 'users': summary_list})

        # --- DETALLE (JSON COMPLETO) ---
        elif action == 'get_user':
            uid = qs.get('user_id')
            if not uid: return response(400, {'error': 'Falta user_id'})
            
            res = table.get_item(Key={'user_id': str(uid)})
            item = res.get('Item')
            
            if not item: return response(404, {'error': 'Usuario no encontrado'})
            
            # ¡AQUI ESTÁ LA MAGIA! 👇
            # Devolvemos el usuario "Normalizado" con todos los campos (incluyendo nulos)
            full_user = normalize_user(item)
            
            return response(200, full_user)

        # --- ACTUALIZAR ---
        elif action == 'update_user':
            uid = body_data.get('user_id')
            updates = body_data.get('updates', {})
            
            if not uid or not updates: return response(400, {'error': 'Faltan datos'})
            
            # Lógica de actualización dinámica DynamoDB
            update_expr = "SET "
            expr_vals = {}
            expr_names = {}
            parts = []
            
            for i, (k, v) in enumerate(updates.items()):
                key_token = f"#k{i}"
                val_token = f":v{i}"
                parts.append(f"{key_token} = {val_token}")
                expr_names[key_token] = k
                expr_vals[val_token] = v
            
            update_expr += ", ".join(parts)
            
            table.update_item(
                Key={'user_id': str(uid)},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_vals
            )
            return response(200, {'status': 'updated'})

        else:
            return response(400, {'error': f'Accion desconocida: {action}'})

    except Exception as e:
        print(f"❌ Error: {e}")
        return response(500, {'error': str(e)})
