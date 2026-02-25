# Usamos la imagen base optimizada para Lambda Python 3.10
FROM public.ecr.aws/lambda/python:3.10

# 1. Copiar requirements.txt
COPY requirements.txt ${LAMBDA_TASK_ROOT}

# 2. Instalar dependencias (Matplotlib, etc.) en el directorio de la tarea Lambda
RUN pip install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

# 3. Copiar el código fuente de la función
COPY . ${LAMBDA_TASK_ROOT}

# 4. Definir el handler que manejará las peticiones
CMD ["app.lambda_handler"]
