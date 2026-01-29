#!/bin/bash
# ---------------------------------------------------------
# CARRIL 4: CRM API DEPLOYER
# ---------------------------------------------------------

# CONFIGURACIÓN
ECR_REPO_NAME="smability-crm"
LAMBDA_FUNC_NAME="Smability-CRM-API" # <--- ¡NUEVA LAMBDA!
AWS_REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:latest"

echo "📊 INICIANDO DESPLIEGUE DEL CRM API..."

# 1. Login
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

# 2. Crear Repo ECR si no existe
aws ecr describe-repositories --repository-names ${ECR_REPO_NAME} > /dev/null 2>&1 || aws ecr create-repository --repository-name ${ECR_REPO_NAME}

# 3. Build & Push
echo "🐳 Construyendo Docker..."
docker build -t ${ECR_REPO_NAME} .
docker tag ${ECR_REPO_NAME}:latest ${IMAGE_URI}
docker push ${IMAGE_URI}

# 4. Actualizar Lambda (O crearla si no existe - aquí solo actualizamos código)
# NOTA: La primera vez tendrás que crear la Lambda en la consola manualmente 
# o usar AWS CLI para crearla. Asumiremos que la creas en consola para asignar roles fácil.
echo "🔄 Actualizando Código Lambda..."
aws lambda update-function-code --function-name ${LAMBDA_FUNC_NAME} --image-uri ${IMAGE_URI}

echo "✅ CRM API Desplegada."
