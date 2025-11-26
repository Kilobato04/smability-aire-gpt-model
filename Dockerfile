FROM public.ecr.aws/lambda/python:3.11

# 1. Copiar e instalar dependencias 
COPY requirements.txt \${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt

# 2. Copiar código de inferencia y grid
COPY app/lambda_function.py \${LAMBDA_TASK_ROOT}
COPY app/grid_base.csv \${LAMBDA_TASK_ROOT}

# 3. COPIAR ARCHIVOS NECESARIOS PARA EL ENTRENAMIENTO (ETL)
COPY training/raw_data/dataset_aire_cdmx.zip \${LAMBDA_TASK_ROOT}/training/raw_data/
COPY training/raw_data/stationssimat.csv \${LAMBDA_TASK_ROOT}/training/raw_data/
COPY training/train_model.py \${LAMBDA_TASK_ROOT}/training/

# --- PASO CRÍTICO: DESCOMPRIMIR Y ENTRENAR DENTRO DEL CONTENEDOR ---
RUN unzip \${LAMBDA_TASK_ROOT}/training/raw_data/dataset_aire_cdmx.zip -d /tmp/dataset_final

# 4. Ejecutar el entrenamiento. Esto CREA el archivo 'model_o3.json' en la raíz.
RUN python \${LAMBDA_TASK_ROOT}/training/train_model.py

# 5. COPIAR EL MODELO RECIÉN GENERADO (De la raíz a la raíz)
# El script Python guarda el modelo en model_o3.json, sin la carpeta 'app/'
COPY model_o3.json \${LAMBDA_TASK_ROOT}/model_o3.json

# 6. Configurar el comando de arranque
CMD [ "lambda_function.lambda_handler" ]
