# Script to automatically add GitHub secrets for CI/CD

Write-Host "Setting up GitHub Secrets for Vavilon CI/CD..." -ForegroundColor Green
Write-Host ""

# Refresh PATH to include gh CLI
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

# Check if gh CLI is available
$ghExists = Get-Command gh -ErrorAction SilentlyContinue
if (-not $ghExists) {
    Write-Host "Error: GitHub CLI (gh) is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Please install it from: https://cli.github.com/" -ForegroundColor Yellow
    exit 1
}

Write-Host "OK - GitHub CLI found" -ForegroundColor Green

# Check if gh CLI is authenticated
$ghAuth = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: GitHub CLI is not authenticated." -ForegroundColor Red
    Write-Host "Please run: gh auth login --web" -ForegroundColor Yellow
    exit 1
}

Write-Host "OK - GitHub CLI is authenticated" -ForegroundColor Green
Write-Host ""

# 1. Get and set Azure Web App Publish Profile
Write-Host "1. Setting AZURE_WEBAPP_PUBLISH_PROFILE..." -ForegroundColor Cyan
$publishProfile = az webapp deployment list-publishing-profiles --name vavilon-backend --resource-group vavilon-rg --xml
if ($LASTEXITCODE -eq 0) {
    $publishProfile | gh secret set AZURE_WEBAPP_PUBLISH_PROFILE
    Write-Host "   OK - AZURE_WEBAPP_PUBLISH_PROFILE set successfully" -ForegroundColor Green
} else {
    Write-Host "   ERROR - Failed to get publish profile" -ForegroundColor Red
}
Write-Host ""

# 2. Get and set ACR credentials
Write-Host "2. Setting ACR_USERNAME and ACR_PASSWORD..." -ForegroundColor Cyan
$acrCreds = az acr credential show --name vavilonacr --resource-group vavilon-rg | ConvertFrom-Json
if ($LASTEXITCODE -eq 0) {
    # Set ACR_USERNAME
    $acrCreds.username | gh secret set ACR_USERNAME
    Write-Host "   OK - ACR_USERNAME set successfully" -ForegroundColor Green
    
    # Set ACR_PASSWORD
    $acrCreds.passwords[0].value | gh secret set ACR_PASSWORD
    Write-Host "   OK - ACR_PASSWORD set successfully" -ForegroundColor Green
} else {
    Write-Host "   ERROR - Failed to get ACR credentials" -ForegroundColor Red
}
Write-Host ""

Write-Host "================================" -ForegroundColor Green
Write-Host "GitHub Secrets Setup Complete!" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""
Write-Host "You can now trigger a deployment by pushing to main:" -ForegroundColor Yellow
Write-Host "  git commit --allow-empty -m `"Trigger deployment`"" -ForegroundColor White
Write-Host "  git push origin main" -ForegroundColor White
Write-Host ""
Write-Host "Or manually trigger the workflow at:" -ForegroundColor Yellow
Write-Host "  https://github.com/bantula/Vavilon_webapp/actions/workflows/deploy.yml" -ForegroundColor Cyan
