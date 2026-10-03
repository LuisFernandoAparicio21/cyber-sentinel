# keepalive_cf_apps.ps1
# Hace pings HTTP cada 20 minutos a las apps SAP BTP para evitar que se duerman.
# Correr en segundo plano: Start-Process powershell -ArgumentList "-File keepalive_cf_apps.ps1" -WindowStyle Hidden

$ENDPOINTS = @(
    "https://soc-dashboard.cfapps.us10-001.hana.ondemand.com/api/health",
    "https://soc-api.cfapps.us10-001.hana.ondemand.com/health",
    "https://soc-api-ingestor.cfapps.us10-001.hana.ondemand.com/health"
)

$INTERVAL_MIN = 20
$LOG_FILE = "$PSScriptRoot\keepalive.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LOG_FILE -Value $line
}

Log "Keep-alive iniciado. Intervalo: ${INTERVAL_MIN}min"

while ($true) {
    foreach ($url in $ENDPOINTS) {
        try {
            $resp = Invoke-WebRequest -Uri $url -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
            Log "OK [$($resp.StatusCode)] $url"
        } catch {
            Log "WARN ping fallido: $url — $($_.Exception.Message)"
        }
    }
    Start-Sleep -Seconds ($INTERVAL_MIN * 60)
}
