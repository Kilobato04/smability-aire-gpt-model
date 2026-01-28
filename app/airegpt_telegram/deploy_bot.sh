#!/bin/bash
# ---------------------------------------------------------
# SMABILITY BOT TRIGGER (CARRIL 3)
# ---------------------------------------------------------

# CONFIGURACIÓN
PROJECT_NAME="Smability-Bot-Builder"
REGION="us-east-1"

# Colores para output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 INICIANDO GESTIÓN DE DESPLIEGUE - AIRE GPT BOT${NC}"

# 1. VERIFICACIÓN DE SEGURIDAD (GIT)
# Como CodeBuild lee de GitHub, verificamos si tienes cambios sin subir.
echo "🔍 Verificando estado del repositorio..."
git fetch origin main > /dev/null 2>&1
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    echo -e "${YELLOW}⚠️  ADVERTENCIA: Tu código local es DISTINTO al de GitHub.${NC}"
    echo "   Si acabas de hacer cambios y no has hecho 'git push', CodeBuild construirá la versión VIEJA."
    echo "   ¿Deseas continuar de todos modos? (s/n)"
    read -r response
    if [[ "$response" != "s" ]]; then
        echo -e "${RED}🛑 Despliegue cancelado. Haz git push primero.${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✅ Sincronización correcta. GitHub tiene tu última versión.${NC}"
fi

# 2. DISPARAR CODEBUILD
echo "📡 Contactando a AWS CodeBuild..."
BUILD_ID=$(aws codebuild start-build --project-name $PROJECT_NAME --query 'build.id' --output text)

if [ -z "$BUILD_ID" ] || [ "$BUILD_ID" == "None" ]; then
    echo -e "${RED}❌ Error al iniciar el build. Verifica que el proyecto '$PROJECT_NAME' exista.${NC}"
    exit 1
fi

# 3. GENERAR LINK DE TRANSPARENCIA
LINK="https://$REGION.console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT_NAME/build/$BUILD_ID/?region=$REGION"

echo "------------------------------------------------------------"
echo -e "${GREEN}✅ ORDEN DE CONSTRUCCIÓN ENVIADA${NC}"
echo "🆔 Build ID: $BUILD_ID"
echo "------------------------------------------------------------"
echo "👇 HAZ CLIC AQUÍ PARA VER LOS LOGS EN VIVO:"
echo -e "${YELLOW}$LINK${NC}"
echo "------------------------------------------------------------"
