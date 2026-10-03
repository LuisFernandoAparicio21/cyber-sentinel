import subprocess
import time
import datetime
import logging

# Configurar logs
logging.basicConfig(
    filename='recovery.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

APPS_TO_MONITOR = ["soc-api", "soc-dashboard", "soc-api-ingestor"]
HANA_SERVICE_NAME = "soc-security-db"
CHECK_INTERVAL = 180  # Segundos

def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)

def check_apps():
    code, out, err = run_cmd("cf apps")
    if code != 0:
        logging.error(f"Error comprobando apps: {err}")
        return

    # Parsear output de 'cf apps'
    lines = out.split('\n')
    for app in APPS_TO_MONITOR:
        for line in lines:
            if line.startswith(app) and len(line.split()) > 1:
                state = line.split()[1]
                if state in ["stopped", "crashed"]:
                    logging.warning(f"App {app} esta {state}. Intentando reiniciar...")
                    restart_app(app)
                elif state == "started":
                    # Imprimir solo en consola para no llenar el log, pero el script es silencioso
                    pass

def restart_app(app_name):
    print(f"[{datetime.datetime.now()}] Levantando app caida: {app_name}...")
    code, out, err = run_cmd(f"cf start {app_name}")
    if code == 0:
        logging.info(f"App {app_name} iniciada correctamente.")
    else:
        logging.error(f"Fallo al iniciar {app_name}: {err}")

def check_hana():
    # En CF, HANA a veces se muestra con `cf service <name>`
    # Como es dificil parsear el estado exacto de HANA por CLI de CF (requiere Cockpit o BTP CLI generalmente),
    # intentaremos mandar el comando de inicio de forma proactiva si detectamos que no hay conexion.
    # Por ahora enviamos el comando de start "ciegamente" si falla un curl a la API, o usamos 'cf update-service'
    
    code, out, err = run_cmd(f"cf service {HANA_SERVICE_NAME}")
    if code == 0:
        if "stopped" in out.lower() or "offline" in out.lower():
            logging.warning("HANA Cloud parece estar deteniendose o parada. Enviando comando de START...")
            print(f"[{datetime.datetime.now()}] Levantando HANA Database...")
            # Comando magico de BTP para encender HANA via CF
            run_cmd(f"cf update-service {HANA_SERVICE_NAME} -c '{{\"data\":{{\"serviceStopped\":false}}}}'")
            logging.info("Comando de START enviado a HANA Cloud.")

def main():
    print(f"[{datetime.datetime.now()}] Iniciando Watchdog de SAP BTP Auto-Recovery...")
    print(f"Monitoreando las apps: {', '.join(APPS_TO_MONITOR)}")
    print(f"Monitoreando base de datos: {HANA_SERVICE_NAME}")
    print(f"Frecuencia de chequeo: {CHECK_INTERVAL} segundos.")
    print("Manteniendo proceso en segundo plano... (Logs guardados en recovery.log)")
    
    while True:
        check_apps()
        check_hana()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
