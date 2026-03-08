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
                
                # --- FIX AJUSTE 2: BIENVENIDA LIMPIA ---
                # Definimos una función rápida de limpieza para el nombre
                safe_name = str(first_name).replace("_", " ").replace("*", "")
                
                TEXTO_BIENVENIDA = (
                                    "🎉 *¡PAGO CONFIRMADO!* 💎\n\n"
                                    "Bienvenido a *AIreGPT Premium*, {user_name}. Tu cuenta ha sido desbloqueada exitosamente.\n\n"
                                    "*Tus nuevos superpoderes están listos:*\n"
                                    "✅ Alertas automáticas reactivadas.\n"
                                    "✅ Cálculo de exposición diario desbloqueado.\n"
                                    "✅ Soporte para 3 ubicaciones y reportes programados.\n\n"
                                    "Pídeme *ver mi resumen* o dime qué quieres configurar ahora. 🚀\n\n"
                                    "💎 *¡Gracias por apoyarnos!* Tu cuenta Premium ya está activa."
                                )
                
                mensaje = TEXTO_BIENVENIDA.format(user_name=safe_name)
                
                # 2. El payload incluye el botón (reply_markup)
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                payload = {
                    "chat_id": str(user_id), 
                    "text": mensaje, 
                    "parse_mode": "Markdown",
                    "link_preview_options": {"is_disabled": True},
                    "reply_markup": json.dumps({ # <--- AQUÍ SE AGREGA EL BOTÓN
                        "inline_keyboard": [
                            [
                                {"text": "👤 Ver mi Perfil Premium", "callback_data": "ver_resumen"}
                            ]
                        ]
                    })
                }
                
                # 3. Envío
                requests.post(url, json=payload)
                print(f"✅ Mensaje de bienvenida enviado a {safe_name} ({user_id})")
                
        return {'statusCode': 200, 'body': 'Webhook procesado con éxito'}
        
    except Exception as e:
        print(f"❌ Error en Webhook: {e}")
        return {'statusCode': 500, 'body': str(e)}
