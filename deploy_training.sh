#!/bin/bash

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}🚀 INICIANDO DESPLIEGUE (Smability AI)${NC}"

# Git Workflow - Usamos "git add ." para que no se le escape nada
echo -e "${YELLOW}📦 Empaquetando cambios... (git add .)${NC}"
git add .

echo -e "${YELLOW}📝 Mensaje del commit: 'Force Update Buildspec'${NC}"
git commit -m "Force Update Buildspec and Dataset Logic"

echo -e "${YELLOW}⬆️ Subiendo a GitHub...${NC}"
git push origin main

echo -e "${GREEN}✅ Código enviado. Revisa CodeBuild.${NC}"
