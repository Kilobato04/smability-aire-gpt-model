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
