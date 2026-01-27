FROM public.ecr.aws/lambda/python:3.11

# 1. Copiar e instalar dependencias
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt

# 2. Copiar el código de la función
COPY lambda_function.py ${LAMBDA_TASK_ROOT}

# 3. Definir el handler
CMD [ "lambda_function.lambda_handler" ]
