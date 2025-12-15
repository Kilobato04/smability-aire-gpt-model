import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
import json
import numpy as np
from pyproj import Transformer
import os

# --- 1. CONFIGURACIÓN ---
# Nombre del archivo (Asegúrate que en la carpeta de la izquierda tenga extensión .tif)
ARCHIVO_ENTRADA = 'archivo_base_geografico_v2.tif'
ARCHIVO_SALIDA = 'malla_valle_mexico_final.geojson'

# LÍMITES DE CORTE (La "Tijera")
# Orden: (Oeste/MinLon, Sur/MinLat, Este/MaxLon, Norte/MaxLat)
# Se recortará todo lo que esté fuera de este cuadro.
LIMITES_VALLE = (-99.39, 19.15, -98.862, 19.777)

def tiff_to_geojson_clipped(input_path, output_path, bounds, target_cells=3000):
    # Verificación básica
    if not os.path.exists(input_path):
        print(f"❌ ERROR CRÍTICO: No se encuentra el archivo '{input_path}'.")
        print("   Verifica que el nombre en la carpeta de archivos coincida exactamente (mayúsculas/minúsculas).")
        return

    # Desempaquetar límites
    min_lon, min_lat, max_lon, max_lat = bounds

    with rasterio.open(input_path) as src:
        print(f"🔹 Archivo cargado: {input_path}")
        print(f"🔹 Dimensiones totales del mapa: {src.width}x{src.height} pixeles")

        # Detectar proyección
        source_crs = src.crs if src.crs else 'EPSG:3857'
        print(f"🔹 Proyección detectada: {source_crs}")

        # Preparar transformadores de coordenadas
        # to_native: Para convertir tus Lat/Lon a las coordenadas internas del TIFF (metros)
        to_native = Transformer.from_crs("EPSG:4326", source_crs, always_xy=True)
        # to_wgs84: Para convertir los puntos finales a Lat/Lon (GeoJSON)
        to_wgs84 = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)

        # 1. CALCULAR EL RECORTE (La magia ocurre aquí)
        # Convertimos las esquinas de tu cuadro Lat/Lon a coordenadas del archivo
        left, bottom = to_native.transform(min_lon, min_lat)
        right, top = to_native.transform(max_lon, max_lat)

        # Rasterio calcula qué pixeles corresponden a ese cuadro geográfico
        window = from_bounds(left, bottom, right, top, transform=src.transform)

        # Intersección de seguridad (por si el cuadro se sale un milímetro del mapa)
        window = window.intersection(rasterio.windows.Window(0, 0, src.width, src.height))

        win_width = int(window.width)
        win_height = int(window.height)

        if win_width <= 0 or win_height <= 0:
            print("❌ ERROR: Los límites geográficos definidos no caen dentro de este archivo TIFF.")
            return

        print(f"🔹 Zona de recorte detectada: {win_width}x{win_height} pixeles originales.")

        # 2. CALCULAR NUEVA RESOLUCIÓN (Para obtener ~3000 celdas)
        ratio = win_width / win_height
        new_height = int(np.sqrt(target_cells / ratio))
        new_width = int(new_height * ratio)

        print(f"🔹 Re-escalando recorte a malla de: {new_width}x{new_height} celdas (Total: {new_width*new_height})")

        # 3. LEER Y REDUCIR DATOS
        # Solo leemos la ventana (ahorra memoria RAM)
        elevation_data = src.read(
            1,
            window=window,
            out_shape=(new_height, new_width),
            resampling=Resampling.average
        )

        # 4. AJUSTAR GEORREFERENCIA
        # Obtenemos la transformación matemática de la ventana recortada
        win_transform = src.window_transform(window)
        # La escalamos a la nueva resolución (más pequeña)
        new_transform = win_transform * win_transform.scale(
            (window.width / new_width),
            (window.height / new_height)
        )

        # 5. GENERAR GEOJSON
        features = []
        rows, cols = elevation_data.shape

        print("🔹 Generando geometría...")
        for row in range(rows):
            for col in range(cols):
                val = elevation_data[row, col]

                # Filtros de datos vacíos o erróneos comunes en elevación
                if val < -500 or val > 10000:
                    continue

                # Calcular coordenada X,Y en el sistema del mapa
                x_native, y_native = rasterio.transform.xy(new_transform, row, col, offset='center')

                # Convertir a Latitud / Longitud
                lon, lat = to_wgs84.transform(x_native, y_native)

                # Doble validación para asegurar que estamos en el cuadro (opcional pero recomendada)
                if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                    continue

                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(lon, 5), round(lat, 5)]
                    },
                    "properties": {
                        "elevation": round(float(val), 1)
                    }
                })

    # Guardar
    geojson_output = {
        "type": "FeatureCollection",
        "features": features
    }

    with open(output_path, 'w') as f:
        # Separators compactos para reducir tamaño del archivo
        json.dump(geojson_output, f, separators=(',', ':'))

    print(f"✅ ¡LISTO! Archivo guardado: {output_path}")
    print(f"   Cantidad de puntos en la malla: {len(features)}")

# --- Ejecución del script ---
tiff_to_geojson_clipped(ARCHIVO_ENTRADA, ARCHIVO_SALIDA, LIMITES_VALLE)
