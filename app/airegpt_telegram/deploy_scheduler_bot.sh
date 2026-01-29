#!/bin/bash
# ---------------------------------------------------------
# 🤖 SMABILITY BOT & SCHEDULER DEPLOYER (MODO PRO)
# ---------------------------------------------------------

REGION="us-east-1"
# Este es el nombre exacto de tu proyecto en CodeBuild
PROJECT_NAME="Smability-Telegram-Deployer" 
REPO_BRANCH="main"

echo "🔵 Iniciando Despliegue de BOT + SCHEDULER..."

# 1. Sincronización con GitHub
echo "📦 Sincronizando cambios con GitHub..."
git add .

# Intentamos commit. Si no hay cambios, no falla, solo avisa.
git commit -m "Deploy automático: Bot & Scheduler Update" > /dev/null 2>&1 || echo "   (Sin cambios nuevos en local, forzando build con versión actual...)"

git push origin $REPO_BRANCH

if [ $? -ne 0 ]; then
    echo "❌ Error: Falló la subida a GitHub. Verifica tus credenciales o conflictos."
    exit 1
fi

# 2. Disparo de CodeBuild
echo "🚀 Disparando CodeBuild: $PROJECT_NAME"
BUILD_ID=$(aws codebuild start-build --project-name $PROJECT_NAME --region $REGION --query 'build.id' --output text)

# Validar que obtuvimos un ID
if [ -z "$BUILD_ID" ] || [ "$BUILD_ID" == "None" ]; then
    echo "❌ Error: No se pudo arrancar el Build. Verifica que el proyecto '$PROJECT_NAME' exista en CodeBuild."
    exit 1
fi

# 3. Limpieza y Links
echo "------------------------------------------------------------"
echo "✅ ORDEN DE BUILD ENVIADA EXITOSAMENTE"
echo "🆔 Build ID: $BUILD_ID"
echo "🔗 RASTREA EL PROGRESO AQUÍ:"
echo "👉 https://$REGION.console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT_NAME/build/$BUILD_ID/?region=$REGION"
echo "------------------------------------------------------------"
