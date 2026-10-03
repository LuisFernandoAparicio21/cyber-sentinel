import pandas as pd
import glob
import os

# 1. Encontrar el archivo parquet más reciente en la carpeta
carpeta = r"c:\Users\HP\Documents\SAP proyecto\01_Cloud_Integration_Fernando\raw_api_batches"
archivos_parquet = glob.glob(os.path.join(carpeta, "*.parquet"))

if not archivos_parquet:
    print("❌ No se encontraron archivos Parquet en la carpeta.")
else:
    ultimo_archivo = max(archivos_parquet, key=os.path.getctime)
    nombre = os.path.basename(ultimo_archivo)
    
    # 2. Leer el Parquet
    df = pd.read_parquet(ultimo_archivo)
    
    # 3. Imprimir el resumen
    print(f"📊 Archivo: {nombre}")
    print(f"📐 Tamaño: {df.shape[0]:,} filas × {df.shape[1]} columnas\n")
    print("📋 COLUMNAS DISPONIBLES:")
    print("-" * 50)
    for col in df.columns:
        nulos = df[col].isna().sum()
        pct_nulos = (nulos / len(df)) * 100
        print(f"- {col}  ({pct_nulos:.1f}% Nulos)")
        
    print("\n👁️ PRIMERAS 3 FILAS (Ejemplo de datos):")
    print("-" * 50)
    # Mostramos las 5 columnas más revelantes para seguridad
    cols_clave = ['@timestamp', 'sap_function_log_type', 'sap_source_type', 'event_hash']
    cols_existentes = [c for c in cols_clave if c in df.columns]
    print(df[cols_existentes].head(3).to_markdown())
