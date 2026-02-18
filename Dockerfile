# CAMBIO CLAVE: Usamos el espejo de AWS para evitar el error 429
FROM public.ecr.aws/docker/library/python:3.11-slim

# 1. Instalar dependencias del sistema (Vitales para SciPy y XGBoost)
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

# 4. Copiar TODO el código (incluyendo app y geográficos)
COPY . ${LAMBDA_TASK_ROOT}

# 5. Instalar RIC (Runtime Interface Client)
RUN pip install awslambdaric --target "${LAMBDA_TASK_ROOT}"

# 6. CMD apuntando a la Lambda Maestra
ENTRYPOINT [ "/usr/local/bin/python", "-m", "awslambdaric" ]
CMD [ "app.lambda_function.lambda_handler" ]
