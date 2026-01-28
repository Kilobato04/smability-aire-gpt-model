#!/bin/bash

# CONFIGURACIÓN
ECR_REPO_NAME="smability-chatbot"
AWS_REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest"
LAMBDA_FUNC_NAME="Smability-Chatbot"

echo "🚀 Iniciando despliegue de AIreGPT Bot..."

# 1. Login ECR
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

# 2. Crear repo si no existe
aws ecr describe-repositories --repository-names ${ECR_REPO_NAME} || aws ecr create-repository --repository-name ${ECR_REPO_NAME}

# 3. Build & Push Docker
docker build -t ${ECR_REPO_NAME} .
docker tag ${ECR_REPO_NAME}:latest ${IMAGE_URI}
docker push ${IMAGE_URI}

# 4. Actualizar Lambda
echo "🔄 Actualizando función Lambda..."
aws lambda update-function-code --function-name ${LAMBDA_FUNC_NAME} --image-uri ${IMAGE_URI}

# 5. Esperar update
echo "⏳ Esperando update..."
aws lambda wait function-updated --function-name ${LAMBDA_FUNC_NAME}

echo "✅ ¡Despliegue completado! El Bot está vivo."
