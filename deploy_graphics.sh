#!/bin/bash
# ---------------------------------------------------------
# SMABILITY GRAPHICS & NIGHTLY JOB DEPLOYER
# ---------------------------------------------------------

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="us-east-1"
PROJECT_NAME="airegpt-smability-graphics-builder"
BUCKET_NAME="smability-builds-$ACCOUNT_ID"
ZIP_FILE="graphics_source.zip"

echo "🎨 Iniciando Despliegue de GRAPHICS ENGINE..."

# 1. Empaquetado desde la carpeta smability_graphics
echo "📦 Comprimiendo código fuente..."
cd smability_graphics
# Comprimimos el contenido para que al descomprimir quede en la raíz
zip -r -q ../$ZIP_FILE . -x "__pycache__/*" "*.git*" "*.DS_Store*"
cd ..

# 2. Subida a S3
echo "📤 Subiendo source a S3..."
aws s3 cp $ZIP_FILE s3://$BUCKET_NAME/$ZIP_FILE

# 3. Disparo de CodeBuild
echo "🚀 Disparando CodeBuild: $PROJECT_NAME"
BUILD_ID=$(aws codebuild start-build --project-name $PROJECT_NAME --query 'build.id' --output text)

# 4. Limpieza y Links
echo "------------------------------------------------------------"
echo "✅ BUILD LANZADO"
echo "🔗 RASTREO: https://$REGION.console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT_NAME/build/$BUILD_ID/?region=$REGION"
echo "------------------------------------------------------------"
rm $ZIP_FILE
