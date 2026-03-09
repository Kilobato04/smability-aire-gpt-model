FROM public.ecr.aws/lambda/python:3.10

# 1. Instalar FFmpeg y librerías base del sistema para Chromium
RUN yum update -y && \
    yum install -y xz atk cups gtk3 libXcomposite alsa-lib \
    libXcursor libXdamage libXext libXi libXrandr libXScrnSaver \
    libXtst pango at-spi2-atk libXt xorg-x11-server-Xvfb \
    xorg-x11-xauth dbus-glib dbus-glib-devel nss mesa-libgbm \
    tar bzip2 wget

# 2. Descargar e instalar un binario estático de FFmpeg
RUN wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz && \
    tar xvf ffmpeg-release-amd64-static.tar.xz && \
    mv ffmpeg-*-static/ffmpeg /usr/local/bin/ && \
    mv ffmpeg-*-static/ffprobe /usr/local/bin/ && \
    rm -rf ffmpeg*

# 3. Instalar librerías de Python
COPY requirements.txt .
RUN pip install -r requirements.txt

# 4. Instalar Playwright y Chromium
RUN playwright install chromium
RUN playwright install-deps chromium

# 5. Copiar todo el código fuente y assets (audios, json) al contenedor
COPY . ${LAMBDA_TASK_ROOT}

# 6. Ejecutar el handler
CMD ["lambda_function.lambda_handler"]
