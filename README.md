AIreGPT - Modelo Predictivo de Calidad del Aire para el Valle de México

Este proyecto implementa un sistema híbrido de Machine Learning (XGBoost) para modelar la calidad del aire en la Zona Metropolitana del Valle de México (ZMVM). Combina datos históricos oficiales de la red de monitoreo (RAMA) con datos en tiempo real de sensores Smability para generar un mapa de calor interpolado de alta resolución.

🏗️ Arquitectura del Proyecto

El sistema se divide en dos entornos:

1. Entorno de Entrenamiento (/training)

Aquí se descargan los datos históricos, se limpian y se entrena el modelo.

Input: Datos históricos de aire.cdmx.gob.mx (2023-2025).

Output: Archivo del modelo entrenado (model.json).

2. Entorno de Producción (/app)

Esta es la aplicación Serverless (AWS Lambda) que corre en tiempo real.

Input: API en tiempo real de Smability + model.json.

Output: Archivo GeoJSON (Mapa) y JSON (API para el Chatbot).

📂 Estructura de Carpetas

smability-aire-gpt-model/
│
├── training/               # 🧪 LABORATORIO DE DATA SCIENCE
│   ├── raw_data/           # CSVs descargados (Ignorados por git)
│   ├── scraper_cdmx.py     # Script ETL para bajar históricos
│   ├── train_model.py      # Script que entrena XGBoost y genera model.json
│   └── grid_generator.py   # Genera la malla de coordenadas de la CDMX
│
├── app/                    # 🚀 APLICACIÓN SERVERLESS
│   ├── lambda_function.py  # Lógica principal (ejecución cada hora)
│   ├── model.json          # El cerebro (copiado desde training)
│   └── grid_base.csv       # Metadatos fijos del mapa (altitud)
│
├── Dockerfile              # Configuración para AWS Lambda
└── requirements.txt        # Librerías de Python


🛠️ Instrucciones de Uso

Fase 1: Obtención de Datos Históricos

Ejecuta el scraper para descargar los datos de 2023, 2024 y 2025 (al corte).

cd training
python scraper_cdmx.py
# Resultado: Archivos CSV anuales en la carpeta /raw_data


Fase 2: Entrenamiento del Modelo

Unifica los CSVs y entrena el modelo XGBoost.

cd training
python train_model.py
# Resultado: Genera 'model.json' y lo mueve a la carpeta /app


Fase 3: Despliegue (AWS Lambda)

Construye la imagen Docker y súbela a ECR.

docker build -t airegpt-model .
# (Ver pasos de AWS CLI para push y deploy)

## Despliegue en AWS Lambda

Este proyecto utiliza una imagen Docker debido al tamaño de las librerías (XGBoost/Pandas).

**Nota sobre CloudShell:** El entorno gratuito de AWS CloudShell puede quedarse sin espacio al construir esta imagen. Se recomienda usar **AWS CodeBuild** o construir localmente si esto ocurre.

**Comandos de Build:**
```bash
docker build -t smability-aire-model .


📊 Fuentes de Datos

RAMA (Red Automática de Monitoreo Atmosférico): Datos oficiales de la CDMX.

Smability Network: Sensores IoT privados para hiper-localidad.
