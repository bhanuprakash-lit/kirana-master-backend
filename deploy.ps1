# deploy.ps1 - Kirana Backend re-deploy script
# Usage:
#   .\deploy.ps1 -Env dev              -> build, push, deploy to DEV  (auto version tag)
#   .\deploy.ps1 -Env uat              -> build, push, deploy to UAT
#   .\deploy.ps1 -Env uat -Tag v5      -> deploy UAT with a specific tag
#   .\deploy.ps1 -Env dev -SkipBuild -Tag uat-20260717-1648
#                                      -> deploy an already-pushed image (no rebuild)
#
# -Env is required so a deploy always names its target explicitly (dev vs uat).
# -SkipBuild reuses an existing tag in ACR - handy for promoting the *same*
#   image to another environment without rebuilding.
#
# Requirements: az CLI logged in, Docker Desktop running on 'default' context

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('dev', 'uat')]
    [string]$Env,

    [string]$Tag = "v$(Get-Date -Format 'yyyyMMdd-HHmm')",

    [switch]$SkipBuild
)

$ACR      = "crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io"
$IMAGE    = "$ACR/kirana-backend"
$ACR_NAME = "crlohiyakirana"

# Per-environment target (container app + resource group).
switch ($Env) {
    'dev' { $APP = "ca-lohiya-outlet";     $RG = "rg-lohiya-outlet-dev" }
    'uat' { $APP = "ca-lohiya-outlet-uat"; $RG = "rg-lohiya-outlet-UAT" }
}

Write-Host "`nTarget: $Env  ->  app '$APP'  (rg '$RG')" -ForegroundColor Magenta
Write-Host "Image:  ${IMAGE}:${Tag}" -ForegroundColor Magenta

if (-not $SkipBuild) {
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
}
else {
    Write-Host "`n[skip] -SkipBuild set - deploying existing tag ${IMAGE}:${Tag}" -ForegroundColor Yellow
}

Write-Host "`n[4/4] Updating Container App '$APP' to $Tag..." -ForegroundColor Cyan
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
