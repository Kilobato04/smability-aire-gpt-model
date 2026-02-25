# 🗺️ Guía Rápida de Navegación en AWS CloudShell

Cuando inicias sesión en AWS CloudShell, siempre aterrizas en tu directorio de inicio (~). Utiliza estos comandos para regresar rápidamente a la carpeta del proyecto `smability-aire-gpt-model`.

## 1. Comandos de Navegación Esenciales

| Tarea | Comando | Descripción |
|-------|---------|-------------|
| Volver al Directorio de Inicio | `cd` | Te lleva a la ruta `/home/cloudshell-user`. |
| Ir al Proyecto | `cd smability-aire-gpt-model` | Te mueve directamente al directorio principal del repositorio. |
| Verificar Contenido | `ls -l` | Lista los archivos principales del proyecto (Dockerfile, requirements.txt). |
| Verificar Rutas | `pwd` | Muestra la ruta de la carpeta actual (ej: `/home/cloudshell-user/smability-aire-gpt-model`). |

## 2. Acceso Rápido al Proyecto

Para ir directamente al directorio de trabajo en una sola línea (ideal al iniciar sesión):

```bash
cd smability-aire-gpt-model
# Aplicar cambios usando tu script de deploy rápido
git pull origin main - push despues de commit en github
git pull origin main --no-rebase     #por si hay mezcla
cd crm_api
./deploy_crm.sh
./deploy_forecast.sh - ajustes de forecast
./deploy_heavy.sh - ajustes a lambdas modelo pesado
cd api_light
./deploy_api_light.sh - ajustes a lamda-api ligera
./deploy_scheduler_bot.sh - ajustes a bot telegram y al scheduler
cd app/airegpt_telegram
./deploy_training.sh - correr el entrenamiento
./deploy_graphics.sh - corre updates de graficos de exposicion y cigarros
```

## 3. Borrar todos los zips y scripts viejos
```bash
rm -f *.zip *.sh
rm -rf __pycache__ app/__pycache__
rm -rf training/raw_data
```
## 4. Activación del Entorno (Solo para Pruebas Locales)

Si necesitas ejecutar scripts de Python directamente en la consola (no para Docker build), recuerda activar el entorno virtual:

```bash
# Navegar y activar en una línea
cd smability-aire-gpt-model && source env_temp/bin/activate

# Para desactivar:
deactivate
```
```bash
# Para guardar versions/revisar carpetas:
nano deploy_fix_v28.sh
bash deploy_fix_v28.sh
ls -lh training/raw_data/
```

# Workflow de Entrenamiento (MLOps)
Objetivo: Actualizar los modelos de IA cuando cambian los datos o el algoritmo.

Hacer cambios: Editar el código Python en training/ o actualizar el dataset en S3 (raw_data/).

Desplegar: Desde la terminal del proyecto, ejecutar:

Bash

./deploy_training.sh
Proceso Automático (AWS CodeBuild):

# Detecta el commit en GitHub.

Descarga el código y el Dataset maestro de S3.

Entrena los 5 modelos (O3, PM10, PM2.5, CO, SO2).

Calcula el error (RMSE).

Guarda los nuevos archivos .json en s3://smability-data-lake/models/.

Resultado: La Lambda de predicción comenzará a usar los nuevos modelos inmediatamente.
