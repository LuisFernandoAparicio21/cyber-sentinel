import pandas as pd
import pyarrow.parquet as pq

# Ruta de tu archivo
file_path = r"D:\SAP_AI_Security\data_pipeline\raw\UNSW-NB15\Network-Flows\UNSW_Flow.parquet"

# Leemos SOLO el esquema (metadatos) sin cargar los datos reales a la memoria
parquet_file = pq.ParquetFile(file_path)
columnas = parquet_file.schema.names

print(f"El dataset UNSW-NB15 tiene {len(columnas)} columnas.")
print("\nLista de columnas:")
print(columnas)
