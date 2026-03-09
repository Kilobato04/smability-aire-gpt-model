#!/bin/bash
# Despliegue de Marketing Engine a AWS ECR y Lambda

AWS_REGION="us-east-1" # Cambia a tu región
ACCOUNT_ID="123456789012" # Tu cuenta AWS
ECR_REPO_NAME="airegpt-marketing-engine"
LAMBDA_FUNCTION_NAME="MarketingReelGenerator"

echo "🔐 Iniciando sesión en AWS ECR..."
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

echo "📦 Construyendo la imagen Docker..."
docker build -t $ECR_REPO_NAME ./marketing_engine

echo "🏷️ Etiquetando la imagen..."
docker tag $ECR_REPO_NAME:latest $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO_NAME:latest

echo "☁️ Subiendo imagen a ECR..."
docker push $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO_NAME:latest

echo "🔄 Actualizando AWS Lambda..."
aws lambda update-function-code --function-name $LAMBDA_FUNCTION_NAME --image-uri $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO_NAME:latest

echo "✅ ¡Despliegue de Marketing completado!"
