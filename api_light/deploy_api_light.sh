#!/bin/bash

# --- CONFIGURACIÓN NUEVA (CARRIL LIGERO) ---
REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REPO_NAME="smability-api-light"      # <--- OJO: Nuevo Repo Exclusivo
IMAGE_TAG="latest"
LAMBDA_FUNC_NAME="Smability-API-Light" # <--- Tu función Lambda actual

# Colores para logs
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}🚀 INICIANDO DESPLIEGUE DE API LIGERA (CARRIL RÁPIDO)...${NC}"
echo "📍 Cuenta: $ACCOUNT_ID | Región: $REGION"

# 1. Login en ECR
echo -e "${CYAN}🔑 Autenticando Docker con ECR...${NC}"
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

# 2. Crear Repositorio ECR (si no existe)
echo -e "${CYAN}📦 Verificando repositorio ECR ($REPO_NAME)...${NC}"
aws ecr create-repository --repository-name $REPO_NAME --region $REGION || true

# 3. Build Docker
echo -e "${CYAN}🔨 Construyendo imagen (Lightweight)...${NC}"
docker build -t $REPO_NAME .

# 4. Tag & Push
echo -e "${CYAN}⬆️  Subiendo imagen a ECR...${NC}"
docker tag $REPO_NAME:$IMAGE_TAG $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:$IMAGE_TAG
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:$IMAGE_TAG

# 5. Actualizar Lambda
echo -e "${CYAN}🔄 Conectando Lambda ($LAMBDA_FUNC_NAME) al nuevo motor...${NC}"
aws lambda update-function-code \
    --function-name $LAMBDA_FUNC_NAME \
    --image-uri $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:$IMAGE_TAG \
    --publish

echo -e "${GREEN}✅ ¡DESPLIEGUE FINALIZADO! La API Ligera está lista y optimizada.${NC}"
