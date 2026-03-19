#!/bin/bash
# ---------------------------------------------------------
# 🎬 MARKETING ENGINE DEPLOYER (CEREBRO)
# ---------------------------------------------------------

LAMBDA_FUNCTION_NAME="Smability-Marketing-Engine"
AWS_REGION="us-east-1"
REPO_BRANCH="main"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}🚀 INICIANDO DESPLIEGUE - MARKETING ENGINE${NC}"

echo -e "📦 ${YELLOW}Sincronizando cambios con GitHub (La fuente de la verdad)...${NC}"
git add .
git commit -m "Deploy automático: Fix del Cerebro y Mapa" > /dev/null 2>&1 || echo "   (Sin cambios pendientes...)"
git push origin $REPO_BRANCH

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Error: Falló la subida a GitHub.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ GitHub actualizado correctamente.${NC}"

echo -e "📦 ${YELLOW}Empaquetando dependencias y código...${NC}"
rm -rf package function.zip
mkdir package
pip3 install -r requirements.txt -t package/ > /dev/null 2>&1

cp lambda_function.py package/
cp master_flows.json package/
cp render_reel.py package/ 2>/dev/null || :
cp render_map_reel.py package/ 2>/dev/null || :
cp template_base.html package/ 2>/dev/null || :
cp buildspec.yml package/ 2>/dev/null || :

cd package
zip -rq ../function.zip .
cd ..

echo -e "☁️ ${YELLOW}Enviando paquete a AWS Lambda ($LAMBDA_FUNCTION_NAME)...${NC}"
# 🚀 FIX: Quitamos el silenciador para ver por qué AWS rechaza la actualización
aws lambda update-function-code \
    --function-name $LAMBDA_FUNCTION_NAME \
    --zip-file fileb://function.zip \
    --region $AWS_REGION

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ ERROR DE AWS DETECTADO: Lee el mensaje de arriba. AWS rechazó el ZIP.${NC}"
    exit 1
fi

rm -rf package function.zip

echo "------------------------------------------------------------"
echo -e "${GREEN}✅ CEREBRO DE MARKETING ACTUALIZADO EXITOSAMENTE${NC}"
echo "------------------------------------------------------------"
