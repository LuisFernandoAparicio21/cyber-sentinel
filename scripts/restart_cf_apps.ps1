# restart_cf_apps.ps1
# Reconecta CF y levanta las 3 apps del SOC si están stopped.
# Configurar en Task Scheduler para correr cada 12 horas.

param(
    [string]$Passcode = ""
)

$CF_API    = "https://api.cf.us10-001.hana.ondemand.com"
$CF_USER   = $env:CF_USER
$CF_ORG    = $env:CF_ORG
$CF_SPACE  = "dev"
$APPS      = @("soc-api", "soc-api-ingestor", "soc-dashboard")
$LOG_FILE  = "$PSScriptRoot\restart_cf_apps.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LOG_FILE -Value $line
}

Log "=== CF App Restart Script ==="

# --- Login si se pasa passcode ---
if ($Passcode -ne "") {
    Log "Logging in with passcode..."
    $loginOut = echo $Passcode | cf login -a $CF_API -u $CF_USER --sso 2>&1
    if ($LASTEXITCODE -ne 0) {
        Log "ERROR: Login failed: $loginOut"
        exit 1
    }
    Log "Login OK"
}

# --- Verificar sesion activa ---
$appsOut = cf apps 2>&1
if ($appsOut -match "Not logged in|token expired|FAILED") {
    Log "ERROR: Not logged in to CF. Run with -Passcode <code> or login manually first."
    Log "Get passcode at: https://login.cf.us10-001.hana.ondemand.com/passcode"
    exit 1
}

# --- Iniciar apps stopped ---
foreach ($app in $APPS) {
    $status = (cf app $app 2>&1 | Select-String "requested state").ToString()
    if ($status -match "stopped") {
        Log "Starting $app..."
        $startOut = cf start $app 2>&1
        if ($startOut -match "FAILED|error") {
            Log "ERROR starting ${app}: $startOut"
        } else {
            Log "$app started OK"
        }
    } else {
        Log "$app already running — skipping"
    }
}

Log "Done."
