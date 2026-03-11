#!/bin/bash
# Despliegue ultrarrápido del Manager de Marketing a AWS Lambda

LAMBDA_FUNCTION_NAME="Smability-Marketing-Engine"
AWS_REGION="us-east-1"

echo "📦 Empaquetando el código Python..."
# Solo empaquetamos el código y el JSON (sin librerías pesadas)
zip -r function.zip lambda_function.py master_flows.json

echo "☁️ Subiendo a AWS Lambda..."
aws lambda update-function-code \
    --function-name $LAMBDA_FUNCTION_NAME \
    --zip-file fileb://function.zip \
    --region $AWS_REGION

echo "🧹 Limpiando..."
rm function.zip

echo "✅ ¡Despliegue del Cerebro completado!"
