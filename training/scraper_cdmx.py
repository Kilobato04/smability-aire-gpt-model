import requests
import pandas as pd
from bs4 import BeautifulSoup
import os
import time
from datetime import datetime

# --- CONFIGURACIÓN ---
YEARS = ['2023', '2024']
PARAMETERS = {
    'pm10': 'pm10',
    'pm25': 'pm2',
    'o3': 'o3',
    'co': 'co',
    'no2': 'no2',
    'so2': 'so2',
    'tmp': 'tmp',
    'rh': 'rh',
    'wsp': 'wsp',
    'wdr': 'wdr'
}

OUTPUT_DIR = 'raw_data'
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def fetch_month_data(year, month, param_code):
    url = f"http://www.aire.cdmx.gob.mx/estadisticas-consultas/concentraciones/respuesta.php?qtipo=HORARIOS&parametro={param_code}&anio={year}&qmes={month}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        if response.status_code == 200:
            return response.content
    except Exception as e:
        print(f"⚠️ Error de red en {year}-{month}: {e}")
    return None

def parse_html_final(html_content, year, month, param_name):
    if not html_content: return []

    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')
    if not tables: return []

    # Tomamos la tabla más grande
    data_table = max(tables, key=lambda t: len(t.find_all('tr')))
    rows = data_table.find_all('tr')

    if len(rows) < 5: return []

    # --- 1. ENCONTRAR FILA DE ENCABEZADOS ---
    header_row_idx = -1
    headers = []

    for i, row in enumerate(rows[:10]):
        cols = [c.get_text(strip=True) for c in row.find_all(['td', 'th'])]
        # Tu log muestra que la fila buena empieza con 'Fecha', 'Hora'
        # Normalizamos a minúsculas para comparar sin miedo
        cols_lower = [c.lower() for c in cols]

        if 'fecha' in cols_lower and 'hora' in cols_lower:
            header_row_idx = i
            headers = cols # Guardamos los nombres originales (ACO, MER...)
            break

    if header_row_idx == -1:
        print(f"⚠️ No se encontró fila de encabezados con 'Fecha' y 'Hora' para {year}-{month}")
        return []

    # --- 2. MAPEAR COLUMNAS (Qué índice es qué estación) ---
    station_map = {}
    hora_idx = -1
    fecha_idx = -1

    for idx, col_name in enumerate(headers):
        c_upper = col_name.upper().strip()
        if 'HORA' in c_upper:
            hora_idx = idx
        elif 'FECHA' in c_upper:
            fecha_idx = idx
        # Guardamos si es clave de 3 letras (MER, PED, TLA...)
        elif len(c_upper) == 3 and c_upper.isalpha():
            station_map[idx] = c_upper

    if hora_idx == -1:
        print("⚠️ Columna HORA no encontrada")
        return []

    parsed_data = []

    # --- 3. EXTRAER DATOS ---
    for row in rows[header_row_idx + 1:]:
        cells = row.find_all('td')
        # Verificación laxa: si tiene menos celdas que headers, quizás faltan algunas al final, pero intentemos leer
        if len(cells) < hora_idx + 1: continue

        cell_values = [c.get_text(strip=True) for c in cells]

        # Extraer Hora
        try:
            hora_val = int(cell_values[hora_idx])
            if hora_val > 24: continue
        except:
            continue

        # Extraer Fecha
        # Tu log muestra formato '01-01-2024' en la columna Fecha
        date_str = f"{year}-{month}-01" # Default
        if fecha_idx != -1 and len(cell_values) > fecha_idx:
            raw_date = cell_values[fecha_idx]
            # Convertir '01-01-2024' -> '2024-01-01'
            try:
                if '-' in raw_date:
                    d, m, y = raw_date.split('-')
                    date_str = f"{y}-{m}-{d}"
                elif '/' in raw_date:
                    d, m, y = raw_date.split('/')
                    date_str = f"{y}-{m}-{d}"
            except:
                pass # Si falla, usamos el default o lógica previa

        # Extraer valores por estación
        for col_idx, station_id in station_map.items():
            if col_idx < len(cell_values):
                val_text = cell_values[col_idx]

                # Limpiar basura (nr, NR, vacíos)
                if val_text.lower() in ['nr', 'n/d', '-', '', 'sf', 'ma', 's/d']:
                    val = None
                else:
                    try:
                        val = float(val_text)
                    except:
                        val = None

                # Solo guardamos si hay dato válido (Ahorra espacio)
                if val is not None:
                    parsed_data.append({
                        'date': date_str,
                        'hour': hora_val,
                        'station_id': station_id,
                        'parameter': param_name,
                        'value': val
                    })

    return parsed_data

# --- EJECUCIÓN ---
print("🚀 Iniciando extracción FINAL...")

for year in YEARS:
    for param_name, param_code in PARAMETERS.items():
        print(f"\n📅 Procesando {year} - {param_name}...")
        year_data = []

        for month in range(1, 13):
            if year == str(datetime.now().year) and month > datetime.now().month:
                break

            month_str = f"{month:02d}"
            html = fetch_month_data(year, month_str, param_code)

            if html:
                data = parse_html_final(html, year, month_str, param_name)
                if data:
                    year_data.extend(data)
                    print(f"   ✅ Mes {month_str}: {len(data)} registros extraídos")
                else:
                    print(f"   ⚠️ Mes {month_str}: HTML ok pero 0 filas leídas (Revisar si mes está vacío)")
            else:
                print(f"   ❌ Mes {month_str}: Error conexión")

            time.sleep(0.1)

        if year_data:
            df = pd.DataFrame(year_data)
            filename = f"{OUTPUT_DIR}/{year}_{param_name}.csv"
            df.to_csv(filename, index=False)
            print(f"💾 ARCHIVO GUARDADO: {filename} ({len(year_data)} filas)")
        else:
            print(f"❌ ATENCIÓN: No se guardó nada para {year} {param_name}")

print("\n🏁 ¡MISIÓN CUMPLIDA! Descarga tu carpeta 'raw_data'.")
