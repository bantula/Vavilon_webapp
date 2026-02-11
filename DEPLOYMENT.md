# Azure Deployment Guide

Step-by-step guide to deploy Vavilon to Azure.

## Architecture

```
Azure Static Web Apps (Frontend)
    ↓ HTTPS
Azure App Service (Node.js Backend)
    ↓ HTTPS
Azure Container Instance (Python AI)
    ↓
Azure Cognitive Services (Speech)
Azure Redis Cache (Sessions)
```

## Prerequisites

- Azure account with active subscription
- Azure CLI installed
- Docker installed (for AI service)
- GitHub account (for CI/CD)

### Install Azure CLI

Windows (PowerShell / winget):

```powershell
# Using winget (recommended)
winget install --id Microsoft.AzureCLI -e

# Or via MSI (run as Administrator)
Invoke-WebRequest -Uri https://aka.ms/installazurecliwindows -OutFile .\AzureCLI.msi
Start-Process msiexec.exe -Wait -ArgumentList '/I AzureCLI.msi /quiet'
Remove-Item .\AzureCLI.msi
```

macOS (Homebrew):

```bash
brew update
brew install azure-cli
```

Ubuntu / Debian:

```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
```

Verify installation:

```bash
az --version
```

### Login to Azure (Git Bash on Windows 11)

If using **Git Bash**, login with device code authentication:

```bash
# Open Git Bash and run:
az login --use-device-code

# Follow the prompt: copy the device code and visit https://microsoft.com/devicelogin
# Paste the code and authenticate in your browser
```

**Common Issues & Fixes:**

1. **"No subscription found" error**
   ```bash
   # List available subscriptions
   az account list --output table
   
   # Set default subscription
   az account set --subscription "YOUR_SUBSCRIPTION_ID"
   ```

2. **"az command not found" in Git Bash**
   - Ensure Azure CLI is in your PATH
   - Restart Git Bash after installation
   - Verify: `which az`

3. **PowerShell cmdlet compatibility in Bash**
   - Use lowercase `az` commands (not `Az-*` cmdlets)
   - Replace `$env:VAR_NAME` with `$VAR_NAME`
   - Replace `$()` with `` $(command) ``

4. **Authentication token issues**
   ```bash
   # Clear cached credentials
   az account clear
   
   # Login again
   az login --use-device-code
   ```

For official instructions see: https://docs.microsoft.com/cli/azure/install-azure-cli

### Install Docker

Windows (Docker Desktop / winget):

```powershell
# Install via winget (requires Windows 10/11)
winget install --id Docker.DockerDesktop -e

# After install: enable WSL2 and virtualization if required, then sign in to Docker Desktop.
```

macOS:

```bash
brew install --cask docker
```

Ubuntu / Debian:

```bash
sudo apt-get update
sudo apt-get install -y docker.io
sudo systemctl enable --now docker
# Optional: allow current user to run docker without sudo
sudo usermod -aG docker $USER
```

Verify installation:

```bash
docker --version
```

For official instructions see: https://docs.docker.com/get-docker/

## Step 0: Create Resource Group & Register Providers

### 0.1 Create Resource Group

Before creating any Azure resources, you must create a resource group to contain them:

```bash
# Create resource group
az group create \
  --name vavilon-rg \
  --location westeurope

# Verify it was created
az group list --output table
```

**Alternative regions:** `eastus`, `westus`, `eastus2`, `westus2`, `northeurope`, `westeurope` (using: `westeurope`)

### 0.2 Register Resource Providers

Register the required Azure resource providers (this can take a few minutes):

```bash
# Register providers required for this deployment
az provider register --namespace Microsoft.Cache
az provider register --namespace Microsoft.CognitiveServices
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.Web
az provider register --namespace Microsoft.ContainerInstance
az provider register --namespace Microsoft.App

# Check registration status (look for "RegistrationState: Registered")
az provider show --namespace Microsoft.Cache --query "registrationState"
az provider show --namespace Microsoft.CognitiveServices --query "registrationState"
az provider show --namespace Microsoft.ContainerRegistry --query "registrationState"
```

**If you see "Registering"**, wait 5-10 minutes and check again. You cannot proceed until all show `"Registered"`.

If subscription quota check is needed:

```bash
az account show --output table
```

## Step 1: Create Azure Resources

### 1.1 Speech Service

```bash
az cognitiveservices account create \
  --name vavilon-speech \
  --resource-group vavilon-rg \
  --kind SpeechServices \
  --sku S0 \
  --location westeurope

# Get credentials
az cognitiveservices account keys list \
  --name vavilon-speech \
  --resource-group vavilon-rg
```

### 1.2 Redis Cache (for session storage)

```bash
az redis create \
  --name vavilon-redis \
  --resource-group vavilon-rg \
  --location westeurope \
  --sku Basic \
  --vm-size c0

# Get connection string
az redis list-keys \
  --name vavilon-redis \
  --resource-group vavilon-rg
```

## Step 2: Deploy AI Service (Python)

### 2.1 Build Docker Image

Create `ai-service/Dockerfile`:

```dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 5000

CMD ["python", "src/app.py"]
```

### 2.2 Build and Push

```bash
cd ai-service

# Build image
docker build -t vavilon-ai:latest .

# Tag for Azure Container Registry
docker tag vavilon-ai:latest vavilonacr.azurecr.io/vavilon-ai:latest

# Create ACR (if it does not exist yet)
az acr create \
  --name vavilonacr \
  --resource-group vavilon-rg \
  --sku Basic \
  --location westeurope

# Login to ACR
az acr login --name vavilonacr

# Push image
docker push vavilonacr.azurecr.io/vavilon-ai:latest
```

### 2.3 Deploy to Container Instance

```bash
# If ACR is private, provide registry credentials
# (Enable admin and fetch credentials once)
az acr update --name vavilonacr --admin-enabled true
az acr credential show --name vavilonacr

az container create \
  --name vavilon-ai \
  --resource-group vavilon-rg \
  --image vavilonacr.azurecr.io/vavilon-ai:latest \
  --registry-login-server vavilonacr.azurecr.io \
  --registry-username vavilonacr \
  --registry-password <ACR_PASSWORD> \
  --os-type Linux \
  --cpu 1 \
  --memory 1.5 \
  --dns-name-label vavilon-ai \
  --ports 5000 \
  --environment-variables \
    PORT=5000 \
    AZURE_SPEECH_KEY=<your-key> \
    AZURE_SPEECH_REGION=westeurope \
    NODE_BACKEND_URL=https://vavilon-backend.azurewebsites.net
```

## Step 3: Deploy Backend (Node.js)

### 3.1 Create App Service

```bash
# Create App Service plan
az appservice plan create \
  --name vavilon-plan \
  --resource-group vavilon-rg \
  --location westeurope \
  --sku B1 \
  --is-linux

az webapp create \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --plan vavilon-plan \
  --runtime "NODE|20-lts"
```

### 3.2 Configure Environment

```bash
az webapp config appsettings set \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --settings \
    PORT=8080 \
    AI_SERVICE_URL=https://vavilon-ai.westeurope.azurecontainer.io:5000 \
    FRONTEND_URL=https://vavilon-app.azurestaticapps.net \
    REDIS_URL=vavilon-redis.redis.cache.windows.net \
    REDIS_PASSWORD="<paste-primary-key-from-step-1.2>" \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true

# Set explicit startup command
az webapp config set \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --startup-file "node src/index.js"
```

**Important:** 
- `SCM_DO_BUILD_DURING_DEPLOYMENT=true` tells Azure's Oryx build system to run
`npm install` after extracting the zip. Without this, your zip has no `node_modules` and the
deployment will fail with a 400 error.
- Replace `<paste-primary-key-from-step-1.2>` with the primary key from `az redis list-keys` in Step 1.2
deployment will fail with a 400 error.

### 3.3 Deploy Code

```powershell
cd backend

# If using a monorepo workspace setup, generate backend-specific package-lock.json
# (Skip this if package-lock.json already exists in backend folder)
npm install --no-workspaces

# IMPORTANT: Do NOT use PowerShell's Compress-Archive for zips targeting Linux.
# It stores paths with Windows backslashes (src\index.js) which causes rsync failures
# on Azure's Linux containers. Use Python instead to create Linux-compatible zips:
python -c @"
import zipfile, os
if os.path.exists('deploy.zip'): os.remove('deploy.zip')
with zipfile.ZipFile('deploy.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.write('package.json', 'package.json')
    zf.write('package-lock.json', 'package-lock.json')
    for root, dirs, files in os.walk('src'):
        for f in files:
            fp = os.path.join(root, f)
            zf.write(fp, fp.replace(os.sep, '/'))
"@

# Deploy using zipdeploy (triggers Oryx build with SCM_DO_BUILD_DURING_DEPLOYMENT=true)
az webapp deployment source config-zip `
  --name vavilon-backend `
  --resource-group vavilon-rg `
  --src .\deploy.zip

# Monitor build and deployment progress (wait 2-3 minutes for npm install)
az webapp log tail --name vavilon-backend --resource-group vavilon-rg
```

**Important notes:**
- **Do NOT use `Compress-Archive`** (PowerShell) to create the zip. It stores Windows-style
  backslash paths (`src\index.js`). Azure Linux containers use rsync to copy files, and rsync
  treats backslashes as literal characters, causing `Invalid argument (22)` errors and build failure.
  Python's `zipfile` module normalizes paths to forward slashes.
- Uses `az webapp deployment source config-zip` (NOT `az webapp deploy --type zip`).
  The older zipdeploy endpoint (`/api/zipdeploy`) works reliably with Oryx builds.
  The newer publish endpoint (`/api/publish` used by `az webapp deploy`) does stricter
  validation and often returns 400 for zips without `node_modules`.
- Small zip file (~24KB instead of 94MB with node_modules)
- Azure runs `npm install --production` after extracting (because of `SCM_DO_BUILD_DURING_DEPLOYMENT`)
- `--no-workspaces` flag ensures standalone package-lock.json in monorepo setups

### 3.4 Enable WebSockets

```bash
az webapp config set \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --web-sockets-enabled true
```

## Step 4: Deploy Frontend (React)

### 4.1 Create Static Web App

```bash
# Authenticate with GitHub (opens browser)
az staticwebapp create \
  --name vavilon-app \
  --resource-group vavilon-rg \
  --location westeurope \
  --source https://github.com/bantula/Vavilon_webapp \
  --branch main \
  --app-location "/frontend" \
  --output-location "dist" \
  --login-with-github
```

This will:
1. Open your browser to authenticate with GitHub
2. Azure will create a GitHub Actions workflow in your repo
3. Every push to `main` branch will automatically deploy

### 4.2 Configure Build

The `frontend/vite.config.js` is already configured to work for both local development and production.

**No changes needed** - the file uses environment variables that automatically switch between:
- **Local development**: `http://localhost:3000` 
- **Production**: Azure URLs (set by GitHub Actions during deployment)

### 4.3 Deploy (Automatic via GitHub Actions)

Once step 4.1 completes, Azure automatically creates a GitHub Actions workflow in your repo.

**Deployment is now automatic** - every time you push to the `main` branch, GitHub Actions will:
1. Build your frontend (`npm run build`)
2. Deploy to Azure Static Web Apps
3. You can monitor deployments at: https://github.com/bantula/Vavilon_webapp/actions

**No manual deployment needed!** 🎉

## Step 5: Update Session Storage (Redis)

✓ **Already completed!** The backend code has been updated to use Redis instead of in-memory storage.

**Changes made:**
- Added `redis` package to [backend/package.json](backend/package.json)
- Updated [backend/src/services/sessionService.js](backend/src/services/sessionService.js) to use Redis
- Updated all routes and websocket handlers to handle async Redis operations
- Sessions now persist across server restarts with 24-hour expiration

**Next step:** Redeploy your backend (repeat Step 3.3) to apply these changes.

## Step 6: Configure DNS and SSL

### 6.1 Custom Domain

Your domain: `vavilonapp.rs`

```bash
# Add custom domain to Static Web App (for www subdomain)
az staticwebapp hostname set \
  --name vavilon-app \
  --hostname www.vavilonapp.rs

# Or for root domain
az staticwebapp hostname set \
  --name vavilon-app \
  --hostname vavilonapp.rs

# SSL is automatic with Azure Static Web Apps
```

**Configure DNS at your domain registrar (.rs):**

1. Go to Azure Portal → Static Web Apps → vavilon-app → Custom domains
2. Click "Add" and enter your domain
3. Azure will show you the validation record
4. In your domain registrar's DNS settings, add:

   **For www subdomain:**
   - Type: CNAME
   - Name: `www`
   - Value: `<your-static-app>.azurestaticapps.net`

   **For root domain (vavilonapp.rs):**
   - Type: ALIAS or ANAME (if supported by .rs registrar)
   - Name: `@`
   - Value: `<your-static-app>.azurestaticapps.net`
   
   OR if ALIAS not supported:
   - Type: A
   - Name: `@`
   - Value: Get IP via `nslookup <your-static-app>.azurestaticapps.net`

5. Wait 10-60 minutes for DNS propagation
6. Verify with: `nslookup vavilonapp.rs`

### 6.2 CORS Configuration

```bash
az webapp cors add \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --allowed-origins https://vavilon-app.azurestaticapps.net

# After custom domain is configured, also add:
az webapp cors add \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --allowed-origins https://vavilonapp.rs
```

## Step 7: Monitoring and Logging

### 7.1 Enable Application Insights

```bash
az monitor app-insights component create \
  --app vavilon-insights \
  --location westeurope \
  --resource-group vavilon-rg

# Get instrumentation key
az monitor app-insights component show \
  --app vavilon-insights \
  --resource-group vavilon-rg \
  --query instrumentationKey
```

### 7.2 Configure Logging

Add to backend:

```javascript
const appInsights = require('applicationinsights');
appInsights.setup(process.env.APPINSIGHTS_INSTRUMENTATIONKEY)
  .setAutoDependencyCorrelation(true)
  .setAutoCollectRequests(true)
  .setAutoCollectPerformance(true)
  .setAutoCollectExceptions(true)
  .start();
```

## Step 8: CI/CD with GitHub Actions

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy Vavilon

on:
  push:
    branches: [main]

jobs:
  deploy-backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: 18
      - run: cd backend && npm install
      - uses: azure/webapps-deploy@v2
        with:
          app-name: vavilon-backend
          publish-profile: ${{ secrets.AZURE_WEBAPP_PUBLISH_PROFILE }}
          package: backend

  deploy-ai:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: docker/login-action@v2
        with:
          registry: vavilonacr.azurecr.io
          username: ${{ secrets.ACR_USERNAME }}
          password: ${{ secrets.ACR_PASSWORD }}
      - run: |
          cd ai-service
          docker build -t vavilonacr.azurecr.io/vavilon-ai:latest .
          docker push vavilonacr.azurecr.io/vavilon-ai:latest

  deploy-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
      - run: cd frontend && npm install && npm run build
      - uses: Azure/static-web-apps-deploy@v1
        with:
          azure_static_web_apps_api_token: ${{ secrets.AZURE_STATIC_WEB_APPS_TOKEN }}
          repo_token: ${{ secrets.GITHUB_TOKEN }}
          action: "upload"
          app_location: "frontend"
          output_location: "dist"
```

## Step 9: Security Hardening

### 9.1 Environment Variables

Store secrets in Azure Key Vault:

```bash
az keyvault create \
  --name vavilon-keyvault \
  --resource-group vavilon-rg \
  --location westeurope

az keyvault secret set \
  --vault-name vavilon-keyvault \
  --name "AzureSpeechKey" \
  --value "<your-key>"
```

### 9.2 Managed Identity

Enable managed identity for App Service:

```bash
az webapp identity assign \
  --name vavilon-backend \
  --resource-group vavilon-rg

# Grant access to Key Vault
az keyvault set-policy \
  --name vavilon-keyvault \
  --object-id <identity-principal-id> \
  --secret-permissions get list
```

## Step 10: Testing Production

### 10.1 Health Checks

```bash
curl https://vavilon-backend.azurewebsites.net/health
curl https://vavilon-ai.eastus.azurecontainer.io:5000/health
```

### 10.2 Load Testing

Use Azure Load Testing:

```bash
az load test create \
  --name vavilon-loadtest \
  --resource-group vavilon-rg \
  --test-file loadtest.yaml
```

## Cost Estimation

Monthly costs (USD):

- App Service (Basic B1): $13
- Container Instance (1 core, 1.5GB): $35
- Redis Cache (Basic C0): $16
- Speech Service (pay-as-you-go): ~$1-5 per hour of audio
- Static Web Apps (Free tier): $0
- **Total: ~$65-70/month + usage**

## Scaling

### Auto-scaling Backend

```bash
az monitor autoscale create \
  --name vavilon-autoscale \
  --resource-group vavilon-rg \
  --resource vavilon-backend \
  --resource-type Microsoft.Web/serverfarms \
  --min-count 1 \
  --max-count 10 \
  --count 2
```

### WebSocket Scaling (Azure SignalR)

For 200+ concurrent connections:

```bash
az signalr create \
  --name vavilon-signalr \
  --resource-group vavilon-rg \
  --sku Standard_S1
```

## Rollback

```bash
# List deployment slots
az webapp deployment slot list \
  --name vavilon-backend \
  --resource-group vavilon-rg

# Swap slots (rollback)
az webapp deployment slot swap \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --slot staging \
  --target-slot production
```

## Support

For production issues:
- Check Azure Portal → Diagnose and solve problems
- Review Application Insights logs
- Monitor Azure Service Health

---

**Deployment complete!** Your app is now live at:
- Frontend: https://vavilon-app.azurestaticapps.net
- Backend: https://vavilon-backend.azurewebsites.net