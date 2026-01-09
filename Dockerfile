FROM public.ecr.aws/lambda/python:3.11

# 1. Dependencias de Sistema (Necesarias para que XGBoost no falle)
RUN yum update -y && yum install -y gcc gcc-c++ make cmake libgfortran && yum clean all

# 2. Instalación de Python Packages (Optimizada)
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt

# 3. Código y Estructura (Paradigma GitHub-First)
# Copiamos la carpeta app que ya tiene subcarpetas: artifacts, geograficos, airegpt_telegram
COPY app/ ${LAMBDA_TASK_ROOT}/app/

# 4. Punto de Entrada (Ubicamos la lambda principal donde AWS la espera)
RUN cp ${LAMBDA_TASK_ROOT}/app/lambda_function.py ${LAMBDA_TASK_ROOT}/lambda_function.py

# 5. Mapeo de Recursos (Para mantener compatibilidad con tus rutas del código V36)
# Esto asegura que los archivos existan tanto en la subcarpeta como en la raíz del task
RUN cp -r ${LAMBDA_TASK_ROOT}/app/geograficos/* ${LAMBDA_TASK_ROOT}/ 2>/dev/null || true
RUN mkdir -p ${LAMBDA_TASK_ROOT}/artifacts && cp -r ${LAMBDA_TASK_ROOT}/app/artifacts/* ${LAMBDA_TASK_ROOT}/artifacts/ 2>/dev/null || true

# 6. Verificación (Para que veas en los logs de CodeBuild que todo está en su lugar)
RUN ls -la ${LAMBDA_TASK_ROOT} && ls -la ${LAMBDA_TASK_ROOT}/artifacts/

CMD [ "lambda_function.lambda_handler" ]
