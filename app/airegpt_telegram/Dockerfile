# Usamos imagen base de AWS para Lambda Python 3.11 (moderna y rápida)
FROM public.ecr.aws/lambda/python:3.11

# Instalar dependencias
COPY requirements.txt .
RUN pip3 install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

# Copiar código fuente
COPY lambda_chatbot.py ${LAMBDA_TASK_ROOT}
COPY lambda_scheduler.py ${LAMBDA_TASK_ROOT}
COPY cards.py ${LAMBDA_TASK_ROOT}
COPY bot_content.py ${LAMBDA_TASK_ROOT}
COPY prompts.py ${LAMBDA_TASK_ROOT}

# Comando por defecto (será sobreescrito por la config de la Lambda, 
# pero ponemos el chatbot como default)
CMD [ "lambda_chatbot.lambda_handler" ]
