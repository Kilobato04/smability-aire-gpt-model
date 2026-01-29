#!/bin/bash
# Despliegue CRM API (Carril 4)

# Configuración
ECR_REPO="smability-crm"
LAMBDA_NAME="Smability-CRM-API"
REGION="us-east-1"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:latest"

echo "🏁 INICIANDO DESPLIEGUE LIMPIO DE CRM..."

# 1. Login ECR
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $URI

# 2. Build (Usando el Dockerfile ligero de esta carpeta)
echo "🏗️ Construyendo Imagen Docker..."
docker build -t $ECR_REPO .

# 3. Tag & Push
echo "⬆️ Subiendo a ECR..."
docker tag $ECR_REPO:latest $URI
docker push $URI

# 4. Actualizar Lambda
echo "🔄 Conectando Lambda con la nueva imagen..."
aws lambda update-function-code --function-name $LAMBDA_NAME --image-uri $URI --publish

echo "✅ ¡ÉXITO! CRM Desplegado."
