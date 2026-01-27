#!/bin/bash

# --- CONFIGURACIÓN ---
PROJECT_NAME="Smability-API-Light-Build"  # Nombre exacto de tu proyecto CodeBuild
REGION="us-east-1"
REPO_BRANCH="main"

# Colores
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}🚀 INICIANDO DESPLIEGUE REMOTO (CodeBuild)...${NC}"

# 1. AUTOGUARDADO EN GITHUB (Vital para que CodeBuild vea los cambios)
echo -e "${YELLOW}📦 Sincronizando con GitHub...${NC}"
git add .
# Si no hay cambios, el commit fallará pero no importa, seguimos
git commit -m "Auto-deploy: Update API Light" || echo "⚠️ No hubo cambios nuevos para commitear."
git push origin $REPO_BRANCH

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ GitHub actualizado.${NC}"
else
    echo -e "${RED}❌ Error subiendo a GitHub. CodeBuild podría usar código viejo.${NC}"
    read -p "Presiona ENTER para continuar de todos modos o Ctrl+C para cancelar..."
fi

# 2. DISPARAR CODEBUILD
echo -e "${CYAN}⚡ Disparando CodeBuild en AWS...${NC}"

BUILD_ID=$(aws codebuild start-build \
    --project-name $PROJECT_NAME \
    --query 'build.id' \
    --output text)

if [ -z "$BUILD_ID" ]; then
    echo -e "${RED}❌ Error: No se pudo iniciar el Build. Revisa el nombre del proyecto.${NC}"
    exit 1
fi

# 3. GENERAR URL DE SEGUIMIENTO
# Limpiamos el ID para la URL (A veces trae comillas extra)
CLEAN_BUILD_ID=$(echo $BUILD_ID | tr -d '"')
LOG_URL="https://$REGION.console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT_NAME/build/$CLEAN_BUILD_ID/log?region=$REGION"

echo -e ""
echo -e "${GREEN}✅ Build Iniciado Exitosamente!${NC}"
echo -e "🆔 ID: $CLEAN_BUILD_ID"
echo -e "--------------------------------------------------------"
echo -e "🔗 SEGUIMIENTO EN VIVO (Click aquí):"
echo -e "${CYAN}$LOG_URL${NC}"
echo -e "--------------------------------------------------------"
echo -e "⏳ El despliegue tomará aprox 2-3 minutos."
