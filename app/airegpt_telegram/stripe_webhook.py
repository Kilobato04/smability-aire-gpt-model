import json
import os
import requests
import boto3
from datetime import datetime
import cards # <--- Importamos nuestro UI centralizado

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
DYNAMODB_TABLE = 'SmabilityUsers'
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

def lambda_handler(event, context):
    print("🔔 Webhook recibido de Stripe")
    
    try:
        body = json.loads(event.get('body', '{}'))
        
        if body.get('type') == 'checkout.session.completed':
            session = body['data']['object']
            
            user_id = session.get('client_reference_id')
            customer_id = session.get('customer')
            sub_id = session.get('subscription', 'pago_unico') 
            
            print(f"💰 Pago exitoso detectado. Telegram ID: {user_id}")
            
            if user_id:
                # Actualizamos y pedimos que nos devuelva el registro completo (ALL_NEW)
                response = table.update_item(
                    Key={'user_id': str(user_id)},
                    UpdateExpression="SET subscription = :sub",
                    ExpressionAttributeValues={
                        ':sub': {
                            'status': 'PREMIUM',
                            'tier': 'PREMIUM_STRIPE',
                            'stripe_customer_id': customer_id,
                            'stripe_subscription_id': sub_id,
                            'updated_at': datetime.utcnow().isoformat()
                        }
                    },
                    ReturnValues="ALL_NEW" 
                )
                print("✅ DynamoDB actualizado a PREMIUM.")
                
                # Extraemos el nombre del usuario para el mensaje personalizado
                user_data = response.get('Attributes', {})
                first_name = user_data.get('first_name', 'Usuario')
                
                # Usamos la tarjeta que guardaste en cards.py
                mensaje = cards.CARD_WELCOME_PREMIUM.format(
                    user_name=first_name,
                    footer=cards.BOT_FOOTER
                )
                
                # Enviamos el Handshake a Telegram
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                requests.post(url, json={"chat_id": str(user_id), "text": mensaje, "parse_mode": "Markdown"})
                print("✅ Mensaje de bienvenida enviado por Telegram.")
                
        return {'statusCode': 200, 'body': 'Webhook procesado con éxito'}
        
    except Exception as e:
        print(f"❌ Error en Webhook: {e}")
        return {'statusCode': 500, 'body': str(e)}
