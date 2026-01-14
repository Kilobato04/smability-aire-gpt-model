#!/bin/bash

# Colores para logs
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "\${YELLOW}🚀 INICIANDO DESPLIEGUE DE ENTRENAMIENTO (Smability AI)\${NC}"

# 1. Verificación de seguridad (Ruta corregida)
if [ ! -f "training/train_v5_grand_slam.py" ]; then
    echo "❌ Error: No encuentro training/train_v5_grand_slam.py"
    exit 1
fi

if [ ! -f "buildspec_train.yml" ]; then
    echo "❌ Error: No encuentro buildspec_train.yml"
    exit 1
fi

# 2. Git Workflow
echo -e "\${YELLOW}📦 Empaquetando cambios para GitHub...\${NC}"
git add training/train_v5_grand_slam.py buildspec_train.yml deploy_training.sh

echo -e "\${YELLOW}📝 Escribe el mensaje del commit (ej. 'Fix path logic'):\${NC}"
read commit_msg

git commit -m "Training Update: \$commit_msg"
git push origin main

# 3. Confirmación
echo -e "\${GREEN}✅ Código enviado a GitHub.\${NC}"
echo -e "\${GREEN}📡 CodeBuild detectará el cambio automáticamente.\${NC}"
