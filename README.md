# smability-aire-gpt-model - Modelo Predictivo de Calidad del Aire para el Valle de México

Este repositorio contiene los scripts de entrenamiento y los archivos de inferencia (modelo y grid base) para el sistema de predicción de calidad del aire en CDMX, utilizando AWS Lambda (Container Image) y XGBoost.

El código de inferencia se encuentra en la carpeta `app/`.

## 🏗️ Arquitectura del Proyecto

El sistema se divide en dos entornos:

### 1. Entorno de Entrenamiento (`/training`)

Aquí se descargan los datos históricos, se limpian y se entrena el modelo.

- **Input**: Datos históricos de `aire.cdmx.gob.mx` (2023-2025)
- **Output**: Archivo del modelo entrenado (`model.json`)

### 2. Entorno de Producción (`/app`)

Esta es la aplicación Serverless (AWS Lambda) que corre en tiempo real.

- **Input**: API en tiempo real de Smability + `model.json`
- **Output**: Archivo GeoJSON (Mapa) y JSON (API para el Chatbot)

## 📂 Estructura de Carpetas

```
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
```

## ⚙️ Guía de Navegación en CloudShell

Para facilitar el trabajo en el entorno de AWS CloudShell, consulta la guía de comandos de navegación para acceder rápidamente a la carpeta del proyecto:

[Guía Rápida de Navegación en AWS CloudShell](guiareadme.md)

## 🚀 Despliegue en AWS Lambda (Estrategia CodeBuild)

El despliegue de este proyecto se realiza mediante una imagen Docker.

**¡IMPORTANTE!** Debido a que las librerías de Machine Learning (`pandas`, `xgboost`) superan el límite de disco de AWS CloudShell (~1GB), la compilación no debe realizarse en la consola.

### Ruta de Despliegue Recomendada: AWS CodeBuild

Para asegurar compilaciones exitosas y sin restricciones de espacio, se recomienda usar AWS CodeBuild como el motor de compilación que subirá la imagen directamente a ECR.

#### 1. Preparación de Archivos
Asegúrate de que los archivos `requirements.txt` y `Dockerfile` estén optimizados (versión ligera sin `scikit-learn` ni `scipy`) y que todos los archivos de `app/` estén listos.

#### 2. Empaquetado para CodeBuild (Desde CloudShell)

```bash
# Comprime los archivos esenciales para CodeBuild
zip -r source_code.zip Dockerfile requirements.txt app/
```

#### 3. Subida a S3
Sube el `source_code.zip` a un bucket de S3, el cual actuará como fuente de CodeBuild.

```bash
aws s3 cp source_code.zip s3://<TU_BUCKET_DE_FUENTE>/
```

#### 4. Configuración de CodeBuild
Configura un proyecto en la consola de AWS CodeBuild que:
- Tome S3 como fuente de código
- Tenga activada la opción Privileged (para construir Docker)
- Use un `buildspec.yml` para construir y subir la imagen a ECR

#### 5. Despliegue Final en Lambda
Una vez que CodeBuild haya terminado, crea o actualiza la función Lambda con la opción Container image, seleccionando la imagen recién subida a ECR. Asegúrate de ajustar la memoria a 1024 MB y el timeout a 1 minuto.

## 📊 Fuentes de Datos

- **RAMA** (Red Automática de Monitoreo Atmosférico): Datos oficiales de la CDMX
- **Smability Network**: Sensores IoT privados para hiper-localidad

cat <<EOF > README.md
# 🌍 Smability AireGPT - Modelo de Inteligencia Atmosférica (V32)

Plataforma de predicción y monitoreo de calidad del aire para el Valle de México. Integra datos de estaciones oficiales (RAMA), red privada Smability, topografía satelital y modelos de Machine Learning para generar un mapa hiper-local de riesgo sanitario.

## 🚀 Características Principales

* **Multi-Contaminante:** Predicción simultánea de **Ozono (O3)**, **PM10** y **PM2.5**.
* **Cumplimiento Normativo:** Cálculo de IAS y Riesgo basado estrictamente en **NOM-172-SEMARNAT-2023 (Vigente 2024)**.
* **Física + IA:** Modelo híbrido que combina **XGBoost** (Patrones históricos) con **Interpolación Vectorial** (Viento/Física) y **Topografía de Alta Resolución** (INEGI GeoJSON).
* **Calibración en Tiempo Real:** Sistema de "Rescate de Estaciones" y corrección de bias espacial. El mapa se ajusta automáticamente a la realidad de los sensores cada hora.
* **Arquitectura Serverless:** 100% AWS Lambda + S3 + EventBridge.

## 📂 Estructura del Proyecto

\`\`\`text
/
├── app/
│   ├── lambda_function.py    # 🧠 MOTOR PRINCIPAL (Generación de Grid)
│   ├── lambda_api_light.py   # ⚡ API LIGERA (Consulta para WhatsApp/LLM)
│   └── grid_base.csv         # Cache de coordenadas (generado dinámicamente)
│
├── training/
│   ├── train_model.py        # 🏋️ SCRIPT DE ENTRENAMIENTO (Genera .json)
│   └── raw_data/             # Datasets históricos (2023-2025)
│
├── malla_valle_mexico_final.geojson # ⛰️ Topografía Oficial INEGI
├── index.html                # 🗺️ VISUALIZADOR WEB (Dashboard V20)
├── Dockerfile                # Entorno de ejecución (Python 3.11 + XGBoost)
└── requirements.txt          # Dependencias
\`\`\`

## ⚙️ Arquitectura de Servicios

### 1. Motor de Inferencia (Lambda Principal)
* **Trigger:** EventBridge (Cron: `20 * * * ? *` - Minuto 20 de cada hora).
* **Input:** API de Smability (Live Data).
* **Proceso:**
    1.  Descarga datos en vivo.
    2.  Carga 3 modelos XGBoost (`o3`, `pm10`, `pm25`).
    3.  Genera Grid 1km x 1km (Límites ajustados: AIFA a Chalco).
    4.  Inyecta Altitud real (GeoJSON).
    5.  Predice y Calibra (Residual Kriging).
    6.  Calcula IAS y Riesgo.
* **Output:** Guarda `live_grid/latest_grid.json` en S3.

### 2. API Ligera (Lambda Secundaria)
* **Trigger:** HTTP Request (Function URL / API Gateway).
* **Uso:** Backend para Chatbot AireGPT (WhatsApp).
* **Función:** Lee el JSON de S3, busca la coordenada del usuario (Nearest Neighbor) y responde en <500ms.

## 🛠️ Guía de Despliegue y Actualización

### Paso 1: Entrenamiento (Si hay nuevos datos históricos)
El `Dockerfile` está configurado para re-entrenar los modelos automáticamente en cada Build.
1.  Subir nuevos CSVs a `training/raw_data/`.
2.  Ejecutar Build en AWS CodeBuild.

### Paso 2: Despliegue de Código
Desde CloudShell:
\`\`\`bash
zip -r source_code.zip Dockerfile requirements.txt app/ training/ malla_valle_mexico_final.geojson
aws s3 cp source_code.zip s3://smability-build-source-temp-2025-25112025/source_code.zip
\`\`\`
Luego iniciar Build en consola AWS.

### Paso 3: Configuración de Lambdas
* **Lambda Grid (Pesada):**
    * Image CMD Override: `app.lambda_function.lambda_handler`
    * Memory: 1024 MB
    * Timeout: 1 min
* **Lambda API (Ligera):**
    * Image CMD Override: `app.lambda_api_light.lambda_handler`
    * Memory: 128 MB
    * Timeout: 5 seg

## 📊 Visualización
El archivo `index.html` es un dashboard Standalone.
* Arrastra el archivo `latest_grid.json` descargado de S3.
* Muestra capas de: O3, PM10, PM2.5, IAS, Riesgo, Clima y Altitud.

---
**Smability Technologies © 2025**
\`\`\`
EOF
