# Pega el código, Ctrl+O (Guardar), Enter, Ctrl+X (Salir)
import boto3
from datetime import datetime

# CONFIGURACIÓN
TABLE_NAME = 'SmabilityUsers'
REGION = 'us-east-1'

# Inicializar DynamoDB
dynamodb = boto3.resource('dynamodb', region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

def migrate_database():
    print(f"🔄 Iniciando Backfill Seguro en {TABLE_NAME}...")
    print("   (Esto NO borrará ubicaciones ni alertas existentes)")
    
    # 1. Traer todos los usuarios actuales
    response = table.scan()
    items = response['Items']
    
    updates = 0
    for user in items:
        user_id = user['user_id']
        needs_save = False
        
        # --- A. Email (Nuevo) ---
        if 'email' not in user:
            user['email'] = None  # Se pedirá más adelante
            needs_save = True

        # --- B. Suscripción (Stripe) ---
        if 'subscription' not in user:
            user['subscription'] = {
                'status': 'FREE',             # Por defecto entra gratis
                'tier': 'basic_v1',
                'stripe_customer_id': None,
                'valid_until': None,
                'auto_renew': False
            }
            needs_save = True
            
        # --- C. Perfil de Salud (Tags Respiratorios) ---
        if 'profile' not in user:
            user['profile'] = {
                'respiratory_tags': [],       # Lista vacía para llenar después (ej. 'asma')
                'device_os': 'unknown',
                'language': 'es'
            }
            needs_save = True
        else:
            # Si ya existía profile pero no los tags nuevos
            if 'respiratory_tags' not in user['profile']:
                user['profile']['respiratory_tags'] = []
                needs_save = True

        # --- D. Métricas (CRM) ---
        if 'metrics' not in user:
            user['metrics'] = {
                'total_requests': 0,
                'alerts_received': 0,
                'days_active': 0
            }
            needs_save = True

        # --- E. Config Contingencia ---
        if 'alerts' not in user: user['alerts'] = {}
        if 'contingency' not in user['alerts']:
            user['alerts']['contingency'] = {'enabled': True, 'last_received': ''}
            needs_save = True

        # --- F. Fecha Creación ---
        if 'created_at' not in user:
            # Si no tiene fecha, usamos hoy para no romper el CRM
            user['created_at'] = datetime.now().isoformat()
            needs_save = True

        # --- GUARDADO ---
        if needs_save:
            try:
                # put_item reemplaza el objeto, pero como 'user' ya contiene
                # toda la data vieja + la nueva, es seguro.
                table.put_item(Item=user)
                print(f"✅ Usuario {user.get('first_name', 'Unknown')} ({user_id}) -> Actualizado con campos CRM.")
                updates += 1
            except Exception as e:
                print(f"❌ Error guardando {user_id}: {e}")
        else:
            print(f"ℹ️  Usuario {user.get('first_name')} ya estaba actualizado.")

    print(f"\n🏁 Proceso terminado. {updates} usuarios migrados a V2.")

if __name__ == '__main__':
    migrate_database()
