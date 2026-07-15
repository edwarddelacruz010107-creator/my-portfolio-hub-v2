param(
    [switch]$Start
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:FLASK_ENV = "development"

Write-Host "Initializing Portfolio Hub local databases..." -ForegroundColor Cyan
python -m flask --app run.py setup-local
if ($LASTEXITCODE -ne 0) {
    throw "Local database setup failed. Review the error above; no fallback schema was created."
}

python -m flask --app run.py db-status
if ($LASTEXITCODE -ne 0) {
    throw "Database status verification failed."
}

Write-Host "Local setup completed successfully." -ForegroundColor Green
if ($Start) {
    python run.py
} else {
    Write-Host "Start the application with: python run.py"
}
