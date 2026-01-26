# CAMBIO: Usamos el espejo de AWS (public.ecr.aws) para evitar el error 429 de Docker Hub
FROM public.ecr.aws/docker/library/python:3.11-slim

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

# 3. Copiar requirements e instalar (con boto3)
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt --target "${LAMBDA_TASK_ROOT}" && \
    pip install boto3 --target "${LAMBDA_TASK_ROOT}"

# 4. COPIAR PROYECTO
# Asegúrate de haber movido lambda_function.py a la raíz antes de esto
COPY . ${LAMBDA_TASK_ROOT}

# 5. Instalar RIC
RUN pip install awslambdaric --target "${LAMBDA_TASK_ROOT}"

# 6. CMD FINAL
ENTRYPOINT [ "/usr/local/bin/python", "-m", "awslambdaric" ]
CMD [ "lambda_function.lambda_handler" ]
