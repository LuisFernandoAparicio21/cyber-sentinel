import json

# Ruta del archivo final JSONL de Cyber Events (PyToshka)
file_path = r"D:\SAP_AI_Security\data_pipeline\raw\cyber_events\train.jsonl"

with open(file_path, 'r', encoding='utf-8') as f:
    first_line = json.loads(f.readline())

print(f"El dataset Cyber Events tiene {len(first_line.keys())} columnas nativas.")
print("\nLista de columnas:")
print(list(first_line.keys()))

