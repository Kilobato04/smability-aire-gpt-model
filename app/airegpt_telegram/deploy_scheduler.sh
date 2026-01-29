#!/bin/bash
set -e

# --- CONFIGURACIÓN ---
ECR_REPO="smability-scheduler"
LAMBDA_NAME="Smability-Scheduler"
REGION="us-east-1"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:latest"

# 📍 RUTA EXACTA DEL ARCHIVO MAESTRO (Desde donde estamos ejecutando el script)
# Estamos en: app/airegpt_telegram/
# Vamos a:    ../../api_light/lambda_function.py
SOURCE_FILE="../../api_light/lambda_function.py"
DEST_FILE="lambda_api_light.py"

echo "⏰ INICIANDO DESPLIEGUE DEL SCHEDULER..."

# 1. Login ECR
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $URI

# --- 🚀 PASO DE SINCRONIZACIÓN Y RENOMBRADO ---
echo "🔄 Buscando API Light en: $SOURCE_FILE"

if [ -f "$SOURCE_FILE" ]; then
    # Copiamos Y Renombramos al mismo tiempo
    cp "$SOURCE_FILE" "./$DEST_FILE"
    echo "✅ Archivo copiado y renombrado a $DEST_FILE exitosamente."
else
    echo "❌ ERROR CRÍTICO: No encuentro el archivo en $SOURCE_FILE"
    echo "   Verifica que la carpeta 'api_light' exista en la raíz del repo."
    exit 1
fi
# -----------------------------------------------

# 2. Build & Push
echo "🏗️  Construyendo Docker..."
docker build -t $ECR_REPO .

echo "⬆️  Subiendo a la nube..."
docker tag $ECR_REPO:latest $URI
docker push $URI

# 3. Actualizar Lambda
echo "🔄 Actualizando Lambda..."
aws lambda update-function-code --function-name $LAMBDA_NAME --image-uri $URI --publish > /dev/null

# Opcional: Limpiar el archivo copiado para no ensuciar tu carpeta local
# rm $DEST_FILE 

echo "✅ DESPLIEGUE COMPLETADO"
