# deploy.ps1 — Kirana Backend re-deploy script
# Usage:
#   .\deploy.ps1           → build, push, deploy (auto version tag)
#   .\deploy.ps1 -Tag v5   → build, push, deploy with specific tag
#
# Requirements: az CLI logged in, Docker Desktop running on 'default' context

param(
    [string]$Tag = "v$(Get-Date -Format 'yyyyMMdd-HHmm')"
)

$ACR      = "crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io"
$IMAGE    = "$ACR/kirana-backend"
$APP      = "ca-lohiya-outlet"
$RG       = "rg-lohiya-outlet-dev"
$ACR_NAME = "crlohiyakirana"

Write-Host "`n[1/4] Switching Docker to default context..." -ForegroundColor Cyan
docker context use default

Write-Host "`n[2/4] Logging into ACR..." -ForegroundColor Cyan
$creds    = az acr credential show --name $ACR_NAME | ConvertFrom-Json
$password = $creds.passwords[0].value
docker login $ACR -u $ACR_NAME -p $password
if ($LASTEXITCODE -ne 0) { Write-Host "ACR login failed" -ForegroundColor Red; exit 1 }

Write-Host "`n[3/4] Building and pushing image: ${IMAGE}:${Tag}..." -ForegroundColor Cyan
docker build -t "${IMAGE}:${Tag}" .
if ($LASTEXITCODE -ne 0) { Write-Host "Build failed" -ForegroundColor Red; exit 1 }

docker push "${IMAGE}:${Tag}"
if ($LASTEXITCODE -ne 0) { Write-Host "Push failed" -ForegroundColor Red; exit 1 }

Write-Host "`n[4/4] Updating Container App to $Tag..." -ForegroundColor Cyan
az containerapp update `
    --name $APP `
    --resource-group $RG `
    --image "${IMAGE}:${Tag}" `
    --output none

Write-Host "`nDone! Waiting 15s for startup..." -ForegroundColor Green
Start-Sleep -Seconds 15

$fqdn = az containerapp show --name $APP --resource-group $RG --query properties.configuration.ingress.fqdn --output tsv
Write-Host "`nLive URL: https://$fqdn" -ForegroundColor Green
Write-Host "Health:   https://$fqdn/health" -ForegroundColor Green
Write-Host "Docs:     https://$fqdn/docs" -ForegroundColor Green

try {
    $health = Invoke-RestMethod "https://$fqdn/health" -TimeoutSec 30
    Write-Host "`nHealth check: $($health.status.ToUpper())" -ForegroundColor Green
} catch {
    Write-Host "`nHealth endpoint not responding yet - check logs:" -ForegroundColor Yellow
    Write-Host "  az containerapp logs show -n $APP -g $RG" -ForegroundColor Yellow
}
