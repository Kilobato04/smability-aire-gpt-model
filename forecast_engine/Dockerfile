# Usamos una imagen base de Python estándar (Debian Slim)
# Es más ligera y tiene repositorios más modernos que Amazon Linux.
FROM python:3.11-slim

# 1. Instalar dependencias del sistema (Compiladores)
# En Debian/Ubuntu usamos 'apt-get'. Instalamos 'build-essential' (gcc, make, etc.)
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    gfortran \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 2. Configurar el entorno para Lambda
# Lambda necesita que el código esté en /var/task
WORKDIR /var/task
ENV LAMBDA_TASK_ROOT=/var/task

# 3. Copiar requirements
COPY requirements.txt .

# 4. Actualizar pip e instalar
# Ahora pip encontrará un gcc 10+ y cmake moderno
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

# 5. Copiar código
COPY lambda_function_forecast.py ${LAMBDA_TASK_ROOT}

# 6. Instalar el "Lambda Runtime Interface Client" (RIC)
# Esto es OBLIGATORIO cuando usas una imagen base que no es de AWS.
# Le enseña a tu contenedor a hablar con el servicio Lambda.
RUN pip install awslambdaric --target "${LAMBDA_TASK_ROOT}"

# 7. CMD: Usamos el RIC para arrancar la función
ENTRYPOINT [ "/usr/local/bin/python", "-m", "awslambdaric" ]
CMD [ "lambda_function_forecast.lambda_handler" ]
