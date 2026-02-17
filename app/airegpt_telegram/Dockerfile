# Usamos imagen base de AWS para Lambda Python 3.11
FROM public.ecr.aws/lambda/python:3.11

# 1. Instalar dependencias
COPY requirements.txt .
RUN pip3 install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

# 2. Copiar código fuente
COPY lambda_chatbot.py ${LAMBDA_TASK_ROOT}
COPY lambda_scheduler.py ${LAMBDA_TASK_ROOT}
COPY lambda_came_scraper.py ${LAMBDA_TASK_ROOT}
COPY cards.py ${LAMBDA_TASK_ROOT}
COPY bot_content.py ${LAMBDA_TASK_ROOT}
COPY prompts.py ${LAMBDA_TASK_ROOT}

# --- FIX CRÍTICO: Faltaba este archivo ---
COPY lambda_api_light.py ${LAMBDA_TASK_ROOT}

# 3. Comando por defecto
# NOTA: En la consola de AWS, para el Scheduler, deberás configurar el
# CMD Override a: lambda_scheduler.lambda_handler
CMD [ "lambda_chatbot.lambda_handler" ]
