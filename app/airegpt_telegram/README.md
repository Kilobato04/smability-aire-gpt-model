# 🤖 AIreGPT Telegram Bot (V57)

Módulo de inteligencia artificial y notificaciones para Smability (Valle de México).
Este componente gestiona la interacción con usuarios vía Telegram y el motor de alertas programadas.

## 📋 Versión Actual: V0.5.7 (Timeline Integration)
**Fecha:** Enero 2026
**Feature Principal:** Lectura nativa de `pronostico_timeline` desde el API Light.

## 🛠️ Archivos del Módulo

| Archivo | Función |
| :--- | :--- |
| **`lambda_chatbot.py`** | **Orquestador:** Recibe mensajes, consulta a OpenAI y genera respuestas. |
| **`lambda_scheduler.py`** | **Motor Proactivo:** Cron (EventBridge) que dispara alertas y contingencias. |
| **`cards.py`** | **Frontend Visual:** Plantillas de tarjetas y lógica de colores/emojis. |
| **`prompts.py`** | **Cerebro:** Contexto de sistema e instrucciones para el LLM. |
| **`bot_content.py`** | **Herramientas:** Definición de esquemas (Function Calling) para OpenAI. |

## 🔄 Flujo de Datos (V57)
1. **Usuario/Cron** solicita datos.
2. Bot consulta API Light con `mode=live`.
3. API Light devuelve objeto `pronostico_timeline` (4 horas futuras).
4. Bot interpreta la tendencia localmente (`interpret_timeline`).
5. Se genera tarjeta con frase: *"⚠️ Sube a MALA a las 18:00"*.

## 🚀 Despliegue
Este módulo se empaqueta junto con `lambda_api_light` en una imagen Docker única.
