FROM public.ecr.aws/lambda/python:3.11

COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt

COPY app/lambda_function.py ${LAMBDA_TASK_ROOT}
COPY app/grid_base.csv ${LAMBDA_TASK_ROOT}
# Importante: GeoJSON de topografía
COPY malla_valle_mexico_final.geojson ${LAMBDA_TASK_ROOT}

COPY training/raw_data/dataset_aire_zmcdmx.zip ${LAMBDA_TASK_ROOT}/training/raw_data/
COPY training/raw_data/stationssimat.csv ${LAMBDA_TASK_ROOT}/training/raw_data/
COPY training/train_model.py ${LAMBDA_TASK_ROOT}/training/

RUN python -c "import zipfile, os; \
    zip_path = os.environ['LAMBDA_TASK_ROOT'] + '/training/raw_data/dataset_aire_zmcdmx.zip'; \
    output_dir = '/tmp/dataset_final'; \
    print(f'🔓 Descomprimiendo {zip_path}...'); \
    zipfile.ZipFile(zip_path, 'r').extractall(output_dir)"

RUN python ${LAMBDA_TASK_ROOT}/training/train_model.py
RUN ls -la ${LAMBDA_TASK_ROOT}

CMD [ "lambda_function.lambda_handler" ]
