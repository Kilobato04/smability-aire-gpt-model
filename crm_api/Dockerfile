FROM public.ecr.aws/lambda/python:3.11

# 1. Instalamos dependencias ligeras
COPY requirements.txt .
RUN pip3 install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

# 2. Copiamos SOLO el código de la función
COPY lambda_function.py ${LAMBDA_TASK_ROOT}

# 3. Comando de arranque
CMD [ "lambda_function.lambda_handler" ]
