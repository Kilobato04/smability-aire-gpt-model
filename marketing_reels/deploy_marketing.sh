#!/bin/bash
# ---------------------------------------------------------
# 🎬 MARKETING ENGINE DEPLOYER (CEREBRO)
# ---------------------------------------------------------

LAMBDA_FUNCTION_NAME="Smability-Marketing-Engine"
AWS_REGION="us-east-1"
REPO_BRANCH="main"

# COLORES
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 INICIANDO DESPLIEGUE - MARKETING ENGINE${NC}"

# 1. SINCRONIZACIÓN AUTOMÁTICA (GIT)
echo -e "📦 ${YELLOW}Sincronizando cambios con GitHub...${NC}"
git add .
git commit -m "Deploy automático: Update Marketing Engine" > /dev/null 2>&1 || echo "   (Sin cambios pendientes de commit...)"
echo -e "⬆️  Subiendo cambios a rama ${REPO_BRANCH}..."
git push origin $REPO_BRANCH

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Error: Falló la subida a GitHub.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ GitHub actualizado correctamente.${NC}"

# 2. EMPAQUETADO Y DESPLIEGUE DIRECTO A LAMBDA
echo -e "📦 ${YELLOW}Empaquetando dependencias (OpenAI) y código...${NC}"
rm -rf package function.zip
mkdir package
pip3 install -r requirements.txt -t package/ > /dev/null 2>&1
cp lambda_function.py package/
cp master_flows.json package/

cd package
zip -rq ../function.zip .
cd ..

echo -e "☁️ ${YELLOW}Actualizando AWS Lambda ($LAMBDA_FUNCTION_NAME)...${NC}"
aws lambda update-function-code \
    --function-name $LAMBDA_FUNCTION_NAME \
    --zip-file fileb://function.zip \
    --region $AWS_REGION > /dev/null 2>&1

# Limpieza
rm -rf package function.zip

# 3. GENERAR LINK DE TRANSPARENCIA
LINK="https://$AWS_REGION.console.aws.amazon.com/lambda/home?region=$AWS_REGION#/functions/$LAMBDA_FUNCTION_NAME?tab=code"

echo "------------------------------------------------------------"
echo -e "${GREEN}✅ CEREBRO DE MARKETING ACTUALIZADO EXITOSAMENTE${NC}"
echo "------------------------------------------------------------"
echo "👇 HAZ CLIC AQUÍ PARA IR A LA LAMBDA Y HACER EL TEST:"
echo -e "${YELLOW}$LINK${NC}"
echo "------------------------------------------------------------"
