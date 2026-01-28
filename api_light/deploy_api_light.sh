#!/bin/bash
# ---------------------------------------------------------
# SMABILITY API LIGHT DEPLOYER (CARRIL RÁPIDO)
# ---------------------------------------------------------

REGION="us-east-1"
PROJECT_NAME="Smability-API-Light-Build" # El nombre que pusimos en AWS
REPO_BRANCH="main"

echo "🔵 Iniciando Despliegue de API LIGERA..."

# 1. Sincronización con GitHub 
# (Equivalente al ZIP+S3, pero para CodeBuild conectado a Git)
echo "📦 Sincronizando cambios con GitHub..."
git add .
git commit -m "Deploy automático: API Light Update" > /dev/null 2>&1 || echo "   (Sin cambios nuevos en local, forzando build con versión actual...)"
git push origin $REPO_BRANCH

if [ $? -ne 0 ]; then
    echo "❌ Error: Falló la subida a GitHub."
    exit 1
fi

# 2. Disparo de CodeBuild
echo "🚀 Disparando CodeBuild: $PROJECT_NAME"
BUILD_ID=$(aws codebuild start-build --project-name $PROJECT_NAME --region $REGION --query 'build.id' --output text)

# Validar que obtuvimos un ID
if [ -z "$BUILD_ID" ] || [ "$BUILD_ID" == "None" ]; then
    echo "❌ Error: No se pudo arrancar el Build. Verifica el nombre del proyecto."
    exit 1
fi

# 3. Limpieza y Links
echo "------------------------------------------------------------"
echo "✅ BUILD LANZADO EXITOSAMENTE"
echo "🔗 RASTREO: https://$REGION.console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT_NAME/build/$BUILD_ID/?region=$REGION"
echo "------------------------------------------------------------"
