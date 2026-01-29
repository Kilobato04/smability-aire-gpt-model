#!/bin/bash
# ---------------------------------------------------------
# 🤖 SMABILITY BOT & SCHEDULER DEPLOYER (MODO PRO)
# ---------------------------------------------------------

# CONFIGURACIÓN
# ⚠️ IMPORTANTE: Usamos el nombre del proyecto UNIFICADO que creamos hoy
PROJECT_NAME="Smability-Telegram-Deployer"
REGION="us-east-1"
REPO_BRANCH="main"

# COLORES (Heredados de tu script anterior)
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 INICIANDO CICLO DE DESPLIEGUE - SMABILITY TELEGRAM CORE${NC}"

# 1. SINCRONIZACIÓN AUTOMÁTICA (GIT)
# En lugar de solo avisar, este script HACE el trabajo sucio por ti.
echo -e "📦 ${YELLOW}Sincronizando cambios con GitHub...${NC}"

git add .
# Hacemos commit solo si hay cambios, si no, seguimos.
git commit -m "Deploy automático: Bot & Scheduler Fix" > /dev/null 2>&1 || echo "   (Sin cambios pendientes de commit...)"

echo -e "⬆️  Subiendo cambios a rama ${REPO_BRANCH}..."
git push origin $REPO_BRANCH

# Verificamos si el push falló
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Error: Falló la subida a GitHub.${NC}"
    echo "   Verifica si tienes conflictos o necesitas un 'git pull' antes."
    exit 1
fi
echo -e "${GREEN}✅ GitHub actualizado correctamente.${NC}"

# 2. DISPARAR CODEBUILD
echo -e "📡 ${YELLOW}Contactando a AWS CodeBuild ($PROJECT_NAME)...${NC}"
BUILD_ID=$(aws codebuild start-build --project-name $PROJECT_NAME --region $REGION --query 'build.id' --output text)

# Validar que obtuvimos un ID
if [ -z "$BUILD_ID" ] || [ "$BUILD_ID" == "None" ]; then
    echo -e "${RED}❌ Error al iniciar el build.${NC}"
    echo "   Verifica que el proyecto '$PROJECT_NAME' exista en la consola de AWS."
    exit 1
fi

# 3. GENERAR LINK DE TRANSPARENCIA
LINK="https://$REGION.console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT_NAME/build/$BUILD_ID/?region=$REGION"

echo "------------------------------------------------------------"
echo -e "${GREEN}✅ ORDEN DE CONSTRUCCIÓN ENVIADA EXITOSAMENTE${NC}"
echo "🆔 Build ID: $BUILD_ID"
echo "------------------------------------------------------------"
echo "👇 HAZ CLIC AQUÍ PARA VER EL PROGRESO EN VIVO:"
echo -e "${YELLOW}$LINK${NC}"
echo "------------------------------------------------------------"
