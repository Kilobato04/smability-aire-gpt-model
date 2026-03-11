#!/bin/bash
# Despliegue del Manager de Marketing a AWS Lambda con dependencias

LAMBDA_FUNCTION_NAME="Smability-Marketing-Engine"
AWS_REGION="us-east-1"

echo "🧹 Limpiando builds anteriores..."
rm -rf package
rm -f function.zip

echo "📦 Instalando dependencias (OpenAI, Requests)..."
mkdir package
pip3 install -r requirements.txt -t package/

echo "📁 Copiando código fuente..."
cp lambda_function.py package/
cp master_flows.json package/

echo "🗜️ Empaquetando en ZIP..."
cd package
zip -r ../function.zip .
cd ..

echo "☁️ Subiendo a AWS Lambda..."
aws lambda update-function-code \
    --function-name $LAMBDA_FUNCTION_NAME \
    --zip-file fileb://function.zip \
    --region $AWS_REGION

echo "🧹 Limpiando archivos temporales..."
rm -rf package
rm function.zip

echo "✅ ¡Despliegue del Cerebro completado!"
