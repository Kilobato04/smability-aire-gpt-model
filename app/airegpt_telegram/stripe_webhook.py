import json
import os
import requests
import boto3
from datetime import datetime
import cards 

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
DYNAMODB_TABLE = 'SmabilityUsers'
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

def lambda_handler(event, context):
    print("🔔 Webhook recibido de Stripe")
    
    try:
        body = json.loads(event.get('body', '{}'))
        event_type = body.get('type')
        
        # --- CASO 1: PAGO COMPLETADO ---
        if event_type == 'checkout.session.completed':
            session = body['data']['object']
            user_id = session.get('client_reference_id')
            customer_id = session.get('customer')
            sub_id = session.get('subscription', 'pago_unico') 
            
            print(f"💰 Pago exitoso detectado. Telegram ID: {user_id}")
            
            if user_id:
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
                
                user_data = response.get('Attributes', {})
                first_name = user_data.get('first_name', 'Usuario')
                safe_name = str(first_name).replace("_", " ").replace("*", "")
                
                mensaje = cards.CARD_WELCOME_PREMIUM.format(user_name=safe_name)
                
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                payload = {
                    "chat_id": str(user_id), 
                    "text": mensaje, 
                    "parse_mode": "Markdown",
                    "link_preview_options": {"is_disabled": True},
                    "reply_markup": json.dumps({
                        "inline_keyboard": [
                            [{"text": "👤 Ver mi Perfil Premium", "callback_data": "ver_resumen"}]
                        ]
                    })
                }
                requests.post(url, json=payload)
                print(f"✅ Bienvenida enviada a {safe_name}")

        # --- CASO 2: SUSCRIPCIÓN CANCELADA ---
        elif event_type == 'customer.subscription.deleted':
            session = body['data']['object']
            customer_id = session.get('customer')
            
            print(f"📉 Evento de cancelación para customer: {customer_id}")
            
            # Buscamos al usuario por su stripe_customer_id
            scan_res = table.scan(
                FilterExpression=boto3.dynamodb.conditions.Attr('subscription.stripe_customer_id').eq(customer_id)
            )
            items = scan_res.get('Items', [])
            
            if items:
                user = items[0]
                user_id = user['user_id']
                safe_name = user.get('first_name', 'Usuario').replace("_", " ").replace("*", "")
                
                # Degradamos a FREE
                table.update_item(
                    Key={'user_id': user_id},
                    UpdateExpression="SET subscription.#s = :s, subscription.#t = :t",
                    ExpressionAttributeNames={
                        "#s": "status",
                        "#t": "tier"
                    },
                    ExpressionAttributeValues={
                        ":s": "FREE",
                        ":t": "FREE"
                    }
                )
                
                mensaje = cards.CARD_GOODBYE_PREMIUM.format(user_name=safe_name)
                
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                payload = {
                    "chat_id": str(user_id),
                    "text": mensaje,
                    "parse_mode": "Markdown",
                    "reply_markup": json.dumps({
                        "inline_keyboard": [[{"text": "💎 Reactivar Premium", "callback_data": "GO_PREMIUM"}]]
                    })
                }
                requests.post(url, json=payload)
                print(f"📉 Suscripción cancelada y mensaje enviado a {safe_name}")

        return {'statusCode': 200, 'body': 'Webhook procesado con éxito'}
        
    except Exception as e:
        print(f"❌ Error en Webhook: {e}")
        return {'statusCode': 500, 'body': str(e)}
