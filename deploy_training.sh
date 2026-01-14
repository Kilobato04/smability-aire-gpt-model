#!/bin/bash

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "\${YELLOW}🚀 INICIANDO DESPLIEGUE (Smability AI)\${NC}"

# Git Workflow
echo -e "\${YELLOW}📦 Empaquetando cambios... (git add .)\${NC}"
git add .

echo -e "\${YELLOW}📝 Mensaje del commit: 'Ready for Grand Slam Training'\${NC}"
git commit -m "Grand Slam Config: S3 Source + V5 Script"

echo -e "\${YELLOW}⬆️ Subiendo a GitHub...\${NC}"
git push origin main

echo -e "\${GREEN}✅ Código enviado. ¡Corre a ver CodeBuild!\${NC}"
