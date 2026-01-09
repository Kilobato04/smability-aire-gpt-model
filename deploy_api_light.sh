#!/bin/bash
# Despliegue de la API Light (Consumo de datos S3)

FUNCTION_NAME="Smability-API-Light"
ZIP_FILE="api_light.zip"

echo "📦 Empaquetando lambda_api_light.py..."
# Solo necesitamos el archivo de la API
cd app && zip -q ../$ZIP_FILE lambda_api_light.py && cd ..

echo "🚀 Actualizando código de la función $FUNCTION_NAME..."
aws lambda update-function-code \
    --function-name $FUNCTION_NAME \
    --zip-file fileb://$ZIP_FILE \
    --query 'LastUpdateStatus' --output text

echo "------------------------------------------------------------"
echo "✅ API LIGHT ACTUALIZADA"
echo "🌐 URL DE PRUEBA (Console):"
echo "https://us-east-1.console.aws.amazon.com/lambda/home?region=us-east-1#/functions/$FUNCTION_NAME"
echo "------------------------------------------------------------"

rm $ZIP_FILE
