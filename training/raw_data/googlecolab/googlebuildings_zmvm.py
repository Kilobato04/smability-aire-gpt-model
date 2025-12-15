import ee
import json
from google.colab import files
import numpy as np

# Autenticación
try:
    ee.Initialize(project='smability-engine-4523') # Tu ID
    print("✅ Conectado")
except:
    ee.Authenticate()
    ee.Initialize(project='smability-engine-4523')

# --- LÍMITES V33 ---
LAT_MIN, LAT_MAX = 19.15, 19.777
LON_MIN, LON_MAX = -99.39, -98.8624
RESOLUTION = 0.01

print("⚙️ Generando Grid...")

# 1. Crear Malla
grid_features = []
lats = np.arange(LAT_MIN, LAT_MAX, RESOLUTION)
lons = np.arange(LON_MIN, LON_MAX, RESOLUTION)

for lat in lats:
    for lon in lons:
        geom = ee.Geometry.Rectangle([lon - 0.005, lat - 0.005, lon + 0.005, lat + 0.005])
        grid_features.append(ee.Feature(geom, {'lat': float(lat), 'lon': float(lon)}))

grid_fc = ee.FeatureCollection(grid_features)

# 2. Cargar Google Open Buildings V3 (Polígonos)
# Filtramos solo los que tienen confianza alta (>0.7)
buildings = ee.FeatureCollection('GOOGLE/Research/open-buildings/v3/polygons') \
    .filterBounds(ee.Geometry.Rectangle([LON_MIN, LAT_MIN, LON_MAX, LAT_MAX])) \
    .filter(ee.Filter.gt('confidence', 0.7))

print("🏗️ Calculando densidad urbana (Google Open Buildings)...")

# 3. Función de Reducción (Suma de Área)
# Como son vectores, usamos un map espacial. Esto es pesado pero preciso.
# Estrategia optimizada: Rasterizar los edificios primero.

# Crear imagen binaria de edificios (1 donde hay edificio, 0 donde no)
# Usamos la propiedad 'area_in_meters' para pintar
building_img = buildings.reduceToImage(
    properties=['area_in_meters'],
    reducer=ee.Reducer.first()
).unmask(0).gt(0).rename('built_up')
# Esto nos da un raster de 1s y 0s.

# Calculamos área construida sumando píxeles
# Pixel area depende de la latitud, approx 10m x 10m en resolución nativa
stats = building_img.multiply(ee.Image.pixelArea()).reduceRegions(
    collection=grid_fc,
    reducer=ee.Reducer.sum(),
    scale=10 # Alta resolución para captar casas pequeñas
)

# 4. Exportar
def format_output(f):
    return {
        'lat': f['properties']['lat'],
        'lon': f['properties']['lon'],
        # Usamos 'building_vol' como nombre para no romper tu código de Lambda actual
        # Aunque técnicamente es Area (m2), la relación física es la misma.
        'building_vol': round(f['properties']['sum'], 1)
    }

data_list = stats.getInfo()['features']
clean_data = [format_output(f) for f in data_list]

output_filename = 'capa_edificios_v2_google.json'
with open(output_filename, 'w') as f:
    json.dump(clean_data, f)

print(f"✅ Archivo creado: {len(clean_data)} celdas.")
files.download(output_filename)
