import json
import boto3
import os
from decimal import Decimal

# CONFIG
TABLE_NAME = 'SmabilityUsers'
# Clave simple para proteger tu endpoint (La definiremos en Variables de Entorno)
ADMIN_API_KEY = os.environ.get('CRM_API_KEY', 'smability-secret-admin') 

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)

# Helper para convertir Decimales de DynamoDB a JSON serializable
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def response(status, body):
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body, cls=DecimalEncoder)
    }

def lambda_handler(event, context):
    # 1. Seguridad Básica (API Key en Headers o QueryString)
    # Buscamos 'x-admin-key' en headers o '?key=' en la URL
    qs = event.get('queryStringParameters') or {}
    headers = event.get('headers') or {}
    
    incoming_key = headers.get('x-admin-key') or qs.get('key')
    
    if incoming_key != ADMIN_API_KEY:
        return response(403, {'error': 'Unauthorized. Acceso denegado.'})

    # 2. Enrutador de Acciones
    try:
        # Si es GET, leemos 'action' de la URL. Si es POST, del body.
        method = event.get('requestContext', {}).get('http', {}).get('method', 'GET')
        
        action = qs.get('action')
        body_data = {}
        
        if method == 'POST' and event.get('body'):
            body_data = json.loads(event.get('body'))
            action = body_data.get('action', action)

        print(f"🔍 CRM Request: {action}")

        # --- ACCIÓN 1: LISTAR USUARIOS (Resumen) ---
        if action == 'list_users':
            # Escanea la tabla (Ojo: Para < 2k usuarios está bien. Para más, se necesita paginación)
            res = table.scan()
            items = res.get('Items', [])
            
            # Limpiamos la data para que el Dashboard cargue rápido (solo lo vital)
            summary = []
            for u in items:
                summary.append({
                    'user_id': u.get('user_id'),
                    'name': u.get('first_name'),
                    'email': u.get('email', 'N/A'),
                    'status': u.get('subscription', {}).get('status', 'FREE'),
                    'created_at': u.get('created_at'),
                    'alerts_active': len(u.get('locations', {})), # Un indicador de actividad
                    'interaction': u.get('last_interaction')
                })
            
            # Ordenar por última interacción (los más activos primero)
            summary.sort(key=lambda x: str(x['interaction']), reverse=True)
            return response(200, {'count': len(summary), 'users': summary})

        # --- ACCIÓN 2: DETALLE DE UN USUARIO ---
        elif action == 'get_user':
            uid = qs.get('user_id')
            if not uid: return response(400, {'error': 'Falta user_id'})
            
            res = table.get_item(Key={'user_id': str(uid)})
            item = res.get('Item')
            if not item: return response(404, {'error': 'Usuario no encontrado'})
            
            return response(200, item)

        # --- ACCIÓN 3: ACTUALIZAR USUARIO (PATCH) ---
        elif action == 'update_user':
            # Espera: { "user_id": "123", "updates": { "subscription.status": "PREMIUM" } }
            uid = body_data.get('user_id')
            updates = body_data.get('updates', {})
            
            if not uid or not updates: return response(400, {'error': 'Faltan datos'})
            
            # Construimos la expresión de actualización dinámica
            update_expr = "SET "
            expr_vals = {}
            expr_names = {}
            
            # Truco para manejar claves anidadas o reservadas
            parts = []
            for i, (k, v) in enumerate(updates.items()):
                # Manejo simple: k="email", v="x@x.com"
                # Manejo complejo: k="subscription.status" (requiere lógica extra,
                # por simplicidad en MVP asumimos actualización de primer nivel o reemplazo de objeto)
                
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
            
            return response(200, {'status': 'updated', 'fields': list(updates.keys())})

        else:
            return response(400, {'error': f'Acción desconocida: {action}'})

    except Exception as e:
        print(f"❌ Error CRM: {e}")
        return response(500, {'error': str(e)})
