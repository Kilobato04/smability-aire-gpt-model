import json
import boto3
import os
from decimal import Decimal

# CONFIGURACIÓN
TABLE_NAME = 'SmabilityUsers'
ADMIN_API_KEY = os.environ.get('CRM_API_KEY', 'smability-secret-admin') 

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)

# Clase para convertir decimales de DynamoDB a float de Python
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
            'Access-Control-Allow-Origin': '*' # Vital para dashboards web
        },
        'body': json.dumps(body, cls=DecimalEncoder)
    }

def lambda_handler(event, context):
    # 1. Seguridad
    qs = event.get('queryStringParameters') or {}
    headers = event.get('headers') or {}
    incoming_key = headers.get('x-admin-key') or qs.get('key')
    
    if incoming_key != ADMIN_API_KEY:
        return response(403, {'error': 'Unauthorized. Clave incorrecta.'})

    try:
        # 2. Determinar Acción
        action = qs.get('action')
        
        # --- LISTAR USUARIOS ---
        if action == 'list_users':
            res = table.scan()
            items = res.get('Items', [])
            
            summary = []
            for u in items:
                # Extraemos solo lo necesario para la tabla resumen
                summary.append({
                    'user_id': u.get('user_id'),
                    'name': u.get('first_name', 'Sin Nombre'),
                    'email': u.get('email', 'N/A'),
                    'status': u.get('subscription', {}).get('status', 'FREE'),
                    'created_at': u.get('created_at'),
                    'alerts_active': len(u.get('locations', {})),
                    'last_interaction': u.get('last_interaction')
                })
            
            # Ordenar por fecha (más recientes primero)
            summary.sort(key=lambda x: str(x.get('last_interaction', '')), reverse=True)
            return response(200, {'count': len(summary), 'users': summary})

        # --- DETALLE USUARIO ---
        elif action == 'get_user':
            uid = qs.get('user_id')
            if not uid: return response(400, {'error': 'Falta user_id'})
            
            res = table.get_item(Key={'user_id': str(uid)})
            item = res.get('Item')
            
            if not item: return response(404, {'error': 'Usuario no encontrado'})
            return response(200, item)

        else:
            return response(400, {'error': f'Acción desconocida: {action}'})

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return response(500, {'error': str(e)})
