# Usamos la imagen oficial de AWS para Lambda con Python 3.11
FROM public.ecr.aws/lambda/python:3.11

# 1. Copiar y instalar las dependencias
# Esto se hace antes de copiar el código para aprovechar la caché de Docker
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt

# 2. Copiar el código de la aplicación
COPY app/lambda_function.py ${LAMBDA_TASK_ROOT}

# 3. Copiar los archivos estáticos del modelo (estos los generaremos después)
# Por ahora, si no existen, el build fallará, así que crea archivos vacíos temporalmente
COPY app/model.json ${LAMBDA_TASK_ROOT}
COPY app/grid_base.csv ${LAMBDA_TASK_ROOT}

# 4. Configurar el comando de arranque (NombreArchivo.NombreFuncion)
CMD [ "lambda_function.lambda_handler" ]
