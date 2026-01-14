#!/bin/bash

# Colores para logs
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}🚀 INICIANDO DESPLIEGUE DE ENTRENAMIENTO (Smability AI)${NC}"

# 1. Verificación de seguridad
if [ ! -f "train_v5_grand_slam.py" ]; then
    echo "❌ Error: No encuentro train_v5_grand_slam.py"
    exit 1
fi

if [ ! -f "buildspec_train.yml" ]; then
    echo "❌ Error: No encuentro buildspec_train.yml"
    exit 1
fi

# 2. Git Workflow
echo -e "${YELLOW}📦 Empaquetando cambios para GitHub...${NC}"
git add train_v5_grand_slam.py buildspec_train.yml deploy_training.sh

echo -e "${YELLOW}📝 Escribe el mensaje del commit (ej. 'Ajuste CO y SO2'):${NC}"
read commit_msg

git commit -m "Training Update: $commit_msg"
git push origin main

# 3. Confirmación
echo -e "${GREEN}✅ Código enviado a GitHub.${NC}"
echo -e "${GREEN}📡 AWS CodeBuild debería detectar este cambio y comenzar el entrenamiento en breve.${NC}"
echo -e "${YELLOW}👉 Ve a la consola de AWS CodeBuild para monitorear el progreso.${NC}"
