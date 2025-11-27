FROM public.ecr.aws/lambda/python:3.11

# --- 1. Dependencias ---
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt

# --- 2. Código App ---
COPY app/lambda_function.py ${LAMBDA_TASK_ROOT}
COPY app/grid_base.csv ${LAMBDA_TASK_ROOT}

# --- 3. Archivos Entrenamiento ---
COPY training/raw_data/dataset_aire_zmcdmx.zip ${LAMBDA_TASK_ROOT}/training/raw_data/
COPY training/raw_data/stationssimat.csv ${LAMBDA_TASK_ROOT}/training/raw_data/
COPY training/train_model.py ${LAMBDA_TASK_ROOT}/training/

# --- 4. Descomprimir (Python Nativo) ---
RUN python -c "import zipfile, os; \
    zip_path = os.environ['LAMBDA_TASK_ROOT'] + '/training/raw_data/dataset_aire_zmcdmx.zip'; \
    output_dir = '/tmp/dataset_final'; \
    print(f'🔓 Descomprimiendo {zip_path}...'); \
    zipfile.ZipFile(zip_path, 'r').extractall(output_dir)"

# --- 5. Entrenar ---
RUN python ${LAMBDA_TASK_ROOT}/training/train_model.py

# --- 6. Verificar ---
RUN ls -la ${LAMBDA_TASK_ROOT}

CMD [ "lambda_function.lambda_handler" ]
