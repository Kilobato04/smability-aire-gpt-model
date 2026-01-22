FROM python:3.11-slim

# 1. Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    gfortran \
    libgomp1 \
    wget \
    tar \
    gzip \
    openssl \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. Configurar entorno Lambda
WORKDIR /var/task
ENV LAMBDA_TASK_ROOT=/var/task

# 3. Copiar requirements e instalar
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

# --- CAMBIO CRÍTICO AQUÍ ---
# 4. Copiar TODO el contenido de la carpeta actual al contenedor
# Esto asegura que 'app/geograficos/*.geojson' y los modelos .json se copien.
COPY . ${LAMBDA_TASK_ROOT}

# 5. Instalar RIC (Runtime Interface Client)
RUN pip install awslambdaric --target "${LAMBDA_TASK_ROOT}"

# 6. CMD
ENTRYPOINT [ "/usr/local/bin/python", "-m", "awslambdaric" ]
CMD [ "lambda_function_forecast.lambda_handler" ]
