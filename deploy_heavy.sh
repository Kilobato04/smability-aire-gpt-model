#!/bin/bash
# Despliegue del Predictor de Calidad del Aire (Heavy)

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="us-east-1"
PROJECT_NAME="smability-aire-predictor-builder"
BUCKET_NAME="smability-builds-$ACCOUNT_ID"
ZIP_FILE="heavy_source.zip"

echo "📦 Empaquetando proyecto desde raíz..."
# Comprimimos todo excepto lo que está en .dockerignore
zip -r -q $ZIP_FILE . -x ".git/*" "training/*" ".gitignore"

echo "📤 Subiendo a S3: s3://$BUCKET_NAME/$ZIP_FILE"
aws s3 cp $ZIP_FILE s3://$BUCKET_NAME/$ZIP_FILE

echo "🚀 Iniciando Build en CodeBuild..."
BUILD_INFO=$(aws codebuild start-build --project-name $PROJECT_NAME --query 'build.id' --output text)

echo "------------------------------------------------------------"
echo "✅ DESPLIEGUE INICIADO EXITOSAMENTE"
echo "🔗 MONITOREA TU BUILD AQUÍ:"
echo "https://$REGION.console.aws.amazon.com/codesuite/codebuild/projects/$PROJECT_NAME/build/$BUILD_INFO/?region=$REGION"
echo "------------------------------------------------------------"

# Limpieza local
rm $ZIP_FILE
