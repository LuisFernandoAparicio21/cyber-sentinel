param(
    [Parameter(Mandatory = $true)]
    [string]$DbUser,

    [Parameter(Mandatory = $true)]
    [string]$DbPassword,

    [string]$ServiceInstance = "soc-security-db",
    [string]$ServiceKey = "key-luis"
)

$ErrorActionPreference = "Stop"

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Command '$Name' is required but not installed or not in PATH."
    }
}

Require-Command -Name "cf"
Require-Command -Name "hana-cli"

Write-Host "Reading host and port from CF service key '$ServiceKey'..."
$raw = cf service-key $ServiceInstance $ServiceKey
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read service key '$ServiceKey' for instance '$ServiceInstance'."
}

# Extract only the JSON payload from cf output.
$rawText = ($raw -join "`n")
$jsonStart = $rawText.IndexOf("{")
if ($jsonStart -lt 0) {
    throw "Service key output did not contain JSON credentials."
}
$jsonPayload = $rawText.Substring($jsonStart)
$parsed = $jsonPayload | ConvertFrom-Json

$dbHost = $parsed.credentials.host
$dbPort = $parsed.credentials.port
if (-not $dbHost -or -not $dbPort) {
    throw "Could not read host/port from service key credentials."
}

Write-Host "Connecting to HANA at ${dbHost}:$dbPort ..."
hana-cli connect --connection "$dbHost`:$dbPort" --user $DbUser --password $DbPassword --save true --encrypt true --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Connection failed. Check DB user/password and privileges."
}

Write-Host ""
Write-Host "=== Real storage usage (all disk volumes) ==="
hana-cli querySimple --query "SELECT ROUND(SUM(USED_SIZE) / 1024 / 1024 / 1024, 2) AS USED_GB, ROUND(SUM(TOTAL_SIZE) / 1024 / 1024 / 1024, 2) AS TOTAL_GB, ROUND((SUM(USED_SIZE) * 100.0) / NULLIF(SUM(TOTAL_SIZE), 0), 2) AS USED_PCT FROM M_DISKS" --output table --quiet

Write-Host ""
Write-Host "=== Breakdown by usage type ==="
hana-cli querySimple --query "SELECT USAGE_TYPE, ROUND(SUM(USED_SIZE) / 1024 / 1024 / 1024, 2) AS USED_GB, ROUND(SUM(TOTAL_SIZE) / 1024 / 1024 / 1024, 2) AS TOTAL_GB FROM M_DISKS GROUP BY USAGE_TYPE ORDER BY USED_GB DESC" --output table --quiet

Write-Host ""
Write-Host "=== Memory usage ==="
hana-cli querySimple --query "SELECT ROUND(SUM(USED_PHYSICAL_MEMORY) / 1024 / 1024 / 1024, 2) AS USED_MEMORY_GB, ROUND(SUM(ALLOCATION_LIMIT) / 1024 / 1024 / 1024, 2) AS LIMIT_MEMORY_GB, ROUND((SUM(USED_PHYSICAL_MEMORY) * 100.0) / NULLIF(SUM(ALLOCATION_LIMIT), 0), 2) AS USED_MEMORY_PCT FROM M_HOST_RESOURCE_UTILIZATION" --output table --quiet

Write-Host ""
Write-Host "Done."
