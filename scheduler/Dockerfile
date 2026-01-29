FROM public.ecr.aws/lambda/python:3.11

# Copiar requirements y dependencias
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt

# Copiar código y archivos auxiliares
COPY lambda_function.py ${LAMBDA_TASK_ROOT}
COPY cards.py ${LAMBDA_TASK_ROOT}
COPY lambda_api_light.py ${LAMBDA_TASK_ROOT}

# Comando de arranque
CMD [ "lambda_function.lambda_handler" ]
