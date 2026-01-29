#!/bin/bash
set -e

# --- CONFIGURACIÓN ---
ECR_REPO="smability-scheduler"  # Nombre del repo en ECR (se creará si no existe)
LAMBDA_NAME="Smability-Scheduler" # ⚠️ Nombre EXACTO de tu función en AWS Lambda
REGION="us-east-1"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:latest"

echo "⏰ INICIANDO DESPLIEGUE DEL SCHEDULER..."

# 1. Login ECR
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $URI

# 2. Crear Repo ECR si no existe (por si es la primera vez)
aws ecr describe-repositories --repository-names $ECR_REPO > /dev/null 2>&1 || \
    aws ecr create-repository --repository-name $ECR_REPO > /dev/null

# 3. Build & Push
echo "🏗️  Construyendo Docker..."
docker build -t $ECR_REPO .
echo "⬆️  Subiendo a la nube..."
docker tag $ECR_REPO:latest $URI
docker push $URI

# 4. Actualizar Lambda
echo "🔄 Actualizando función Lambda..."
aws lambda update-function-code --function-name $LAMBDA_NAME --image-uri $URI --publish > /dev/null

echo "✅ SCHEDULER ACTUALIZADO CORRECTAMENTE"
