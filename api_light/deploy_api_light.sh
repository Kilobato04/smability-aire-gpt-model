#!/bin/bash

# --- CONFIGURACIÓN ---
# ⚠️ ASEGÚRATE QUE ESTE NOMBRE SEA IDENTICO AL DE TU CONSOLA DE CODEBUILD
PROJECT_NAME="Smability-API-Light-Build" 
REGION="us-east-1"
REPO_BRANCH="main"

# Colores
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}🚀 INICIANDO DESPLIEGUE AUTOMÁTICO (CLOUDSHELL -> GITHUB -> CODEBUILD)${NC}"

# 1. SINCRONIZAR CON GITHUB
echo -e "${YELLOW}📦 Paso 1: Enviando código a GitHub...${NC}"
git add .
git commit -m "Deploy automático desde CloudShell" > /dev/null 2>&1 || echo "   (No hay cambios nuevos para commitear, continuando...)"
git push origin $REPO_BRANCH

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Error al hacer git push. Verifica tus credenciales o que estés en la rama correcta.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ GitHub actualizado.${NC}"

# 2. DISPARAR CODEBUILD
echo -e "${YELLOW}⚡ Paso 2: Iniciando Build en AWS CodeBuild...${NC}"

# Intentamos arrancar el build y capturamos TODO el output para ver errores si falla
BUILD_JSON=$(aws codebuild start-build --project-name $PROJECT_NAME --region $REGION 2>&1)

# Verificamos si hubo error en el comando
if [[ $BUILD_JSON == *"An error occurred"* ]]; then
    echo -e "${RED}❌ NO SE PUDO INICIAR EL BUILD:${NC}"
    echo "$BUILD_JSON"
    echo -e "${YELLOW}💡 Tip: Verifica que PROJECT_NAME en este script coincida exactamente con el nombre en la consola de CodeBuild.${NC}"
    exit 1
fi

# Extraemos el ID limpio
BUILD_ID=$(echo $BUILD_JSON | grep -o '"id": "[^"]*' | cut -d'"' -f4)
CLEAN_ID=$(echo $BUILD_ID | tr -d '"')

echo -e "${GREEN}✅ Build iniciado con ID: $CLEAN_ID${NC}"
LOG_URL="https://$REGION.console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT_NAME/build/$CLEAN_ID/log?region=$REGION"
echo -e "🔗 Link de logs: $LOG_URL"

# 3. MONITOREO ACTIVO (Polling)
echo -e "${YELLOW}⏳ Paso 3: Esperando resultado (esto toma unos 2-3 min)...${NC}"
echo "   [Ctrl+C para salir y dejarlo corriendo en fondo]"

STATUS="IN_PROGRESS"
while [[ "$STATUS" == "IN_PROGRESS" ]]; do
    sleep 10
    STATUS=$(aws codebuild batch-get-builds --ids $CLEAN_ID --query 'builds[0].buildStatus' --output text --region $REGION)
    
    if [[ "$STATUS" == "SUCCEEDED" ]]; then
        echo -e ""
        echo -e "${GREEN}🎉 ¡ÉXITO! El despliegue terminó correctamente.${NC}"
        echo -e "   La API Ligera ya está actualizada en la Lambda."
        exit 0
    elif [[ "$STATUS" == "FAILED" || "$STATUS" == "STOPPED" ]]; then
        echo -e ""
        echo -e "${RED}❌ EL BUILD FALLÓ.${NC}"
        echo -e "   Revisa los logs aquí: $LOG_URL"
        exit 1
    else
        echo -n "."
    fi
done
