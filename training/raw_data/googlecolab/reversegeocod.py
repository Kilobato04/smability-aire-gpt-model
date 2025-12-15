# 1. Instalar librería ligera de geocodificación
!pip install reverse_geocoder pandas numpy -q

import pandas as pd
import numpy as np
import reverse_geocoder as rg
import json
from google.colab import files

print("✅ Librerías listas.")

# 2. DEFINIR TU GRID EXACTO (V33/V34)
# Deben ser los mismos límites que en tu Lambda para que los puntos coincidan
LAT_MIN, LAT_MAX = 19.15, 19.777
LON_MIN, LON_MAX = -99.39, -98.8624
RESOLUTION = 0.01

print("⚙️ Generando malla base...")
lats = np.arange(LAT_MIN, LAT_MAX, RESOLUTION)
lons = np.arange(LON_MIN, LON_MAX, RESOLUTION)
grid_points = []

# Creamos la lista de coordenadas para consultar
coords_to_query = []
for lat in lats:
    for lon in lons:
        grid_points.append({'lat': lat, 'lon': lon})
        coords_to_query.append((lat, lon))

print(f"📍 Puntos a geocodificar: {len(coords_to_query)}")

# 3. GEOCODIFICACIÓN MASIVA (Offline)
print("🌍 Buscando Alcaldías y Estados...")
results = rg.search(coords_to_query)

# 4. PROCESAR RESULTADOS
# Mapeamos los resultados al formato que queremos
enriched_data = []

for i, point in enumerate(grid_points):
    res = results[i]

    # Limpieza de nombres
    city = res.get('name', '')
    state = res.get('admin1', '')

    # Ajustes manuales comunes para CDMX/Edomex
    if state == 'Mexico City': state = 'CDMX'
    if state == 'Mexico': state = 'Edomex'

    enriched_data.append({
        'lat': point['lat'],
        'lon': point['lon'],
        'mun': city,   # Ej. Cuauhtémoc
        'edo': state   # Ej. CDMX
    })

# 5. EXPORTAR
output_filename = 'grid_admin_info.json'
with open(output_filename, 'w') as f:
    json.dump(enriched_data, f)

print(f"✅ Archivo '{output_filename}' generado.")
print(f"   Ejemplo: {enriched_data[100]}")

# Descargar
files.download(output_filename)
