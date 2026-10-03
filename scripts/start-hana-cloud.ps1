# Arranca una instancia SAP HANA Cloud parada (sin usar Cockpit).
# Requisitos: Cloud Foundry CLI (cf) instalado y sesion iniciada en org/space correctos.
#
# Uso:
#   .\start-hana-cloud.ps1 -ServiceName "HC_HDB_CF"
#   .\start-hana-cloud.ps1   (si defines $env:HANA_CF_SERVICE_NAME)
#
# Antes (una vez por maquina):
#   cf login -a https://api.cf.us10.hana.ondemand.com   # ajusta region
#   cf target -o TU_ORG -s TU_SPACE
#   cf services    # copia el nombre exacto de la instancia hana-cloud

param(
  [string] $ServiceName = $env:HANA_CF_SERVICE_NAME
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg = Join-Path $here "hana_cloud_start.json"

if (-not $ServiceName -or $ServiceName.Trim() -eq "") {
  Write-Host "Falta el nombre de la instancia." -ForegroundColor Red
  Write-Host "  cf services" -ForegroundColor Yellow
  Write-Host "  .\start-hana-cloud.ps1 -ServiceName `"NOMBRE_EN_LA_LISTA`"" -ForegroundColor Yellow
  exit 1
}

$cf = Get-Command cf -ErrorAction SilentlyContinue
if (-not $cf) {
  Write-Host "Instala CF CLI: https://github.com/cloudfoundry/cli/releases" -ForegroundColor Red
  exit 1
}

Write-Host "Iniciando instancia HANA Cloud: $ServiceName" -ForegroundColor Cyan
& cf update-service $ServiceName -c $cfg
if ($LASTEXITCODE -ne 0) {
  Write-Host "Fallo cf update-service. Revisa: cf login, cf target, nombre del servicio." -ForegroundColor Red
  exit $LASTEXITCODE
}
Write-Host "Orden enviada. El estado pasa a Running en varios minutos: cf service `"$ServiceName`"" -ForegroundColor Green
