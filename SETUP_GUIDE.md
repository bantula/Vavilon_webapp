# Developer Setup Guide — Vavilon Real-Time Translation Platform
**Last Updated:** February 15, 2026  
**Purpose:** Complete setup guide for new developers to work on this project from scratch.

---

## Table of Contents

1. [Prerequisites & Installations](#1-prerequisites--installations)
2. [Repository Setup](#2-repository-setup)
3. [Local Development Environment](#3-local-development-environment)
4. [Testing Locally](#4-testing-locally)
5. [Azure Account & Resources Setup](#5-azure-account--resources-setup)
6. [GitHub CI/CD Configuration](#6-github-cicd-configuration)
7. [First Production Deployment](#7-first-production-deployment)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Prerequisites & Installations

Install the following tools on your development machine:

### 1.1 Node.js & npm

**Windows (using winget):**
```powershell
winget install --id OpenJS.NodeJS.LTS -e
```

**macOS (using Homebrew):**
```bash
brew install node@18
```

**Ubuntu/Debian:**
```bash
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs
```

**Verify installation:**
```bash
node --version  # Should be v18+ or v20+
npm --version   # Should be 9+ or 10+
```

---

### 1.2 Python 3.9+

**Windows (using winget):**
```powershell
winget install --id Python.Python.3.9 -e
```

**macOS (using Homebrew):**
```bash
brew install python@3.9
```

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y python3.9 python3-pip
```

**Verify installation:**
```bash
python --version  # Should be 3.9+
pip --version
```

---

### 1.3 Git

**Windows (using winget):**
```powershell
winget install --id Git.Git -e
```

**macOS:**
```bash
brew install git
```

**Ubuntu/Debian:**
```bash
sudo apt-get install -y git
```

**Configure Git:**
```bash
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
```

---

### 1.4 Azure CLI

**Windows (using winget):**
```powershell
winget install --id Microsoft.AzureCLI -e
```

**macOS:**
```bash
brew install azure-cli
```

**Ubuntu/Debian:**
```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
```

**Verify installation:**
```bash
az --version
```

**Login to Azure:**
```bash
# If using Git Bash on Windows, use device code flow
az login --use-device-code

# Otherwise (PowerShell, macOS Terminal):
az login
```

**Troubleshooting login issues:**
```bash
# If "No subscription found"
az account list --output table
az account set --subscription "YOUR_SUBSCRIPTION_ID"

# If authentication token issues
az account clear
az login --use-device-code
```

---

### 1.5 Docker

**Windows (Docker Desktop):**
```powershell
winget install --id Docker.DockerDesktop -e
```
- After install: Enable WSL2 and virtualization if required
- Sign in to Docker Desktop

**macOS:**
```bash
brew install --cask docker
```

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y docker.io
sudo systemctl enable --now docker
sudo usermod -aG docker $USER  # Allow non-root docker
```

**Verify installation:**
```bash
docker --version
docker ps  # Should not show permission errors
```

---

## 2. Repository Setup

### 2.1 Clone Repository

```bash
# Create workspace directory
mkdir -p ~/Projects
cd ~/Projects

# Clone repository (replace with your fork URL if needed)
git clone https://github.com/bantula/Vavilon_webapp.git
cd Vavilon_webapp
```

---

### 2.2 Install Dependencies

**Install all dependencies for all three services:**

```bash
# Root workspace
npm install

# Backend (Node.js)
cd backend
npm install
cd ..

# Frontend (React)
cd frontend
npm install
cd ..

# AI Service (Python)
cd ai-service
pip install -r requirements.txt
cd ..
```

**Troubleshooting:**
- If `npm install` fails: Try `npm ci` (clean install)
- If Python pip fails: Use `pip3 install -r requirements.txt`
- If permission errors on Windows: Run PowerShell as Administrator

---

## 3. Local Development Environment

### 3.1 Create Backend `.env` File

```bash
cd backend
```

Create `backend/.env`:
```env
PORT=3000
AI_SERVICE_URL=http://localhost:5000
FRONTEND_URL=http://localhost:5173
REDIS_URL=redis://localhost:6379
REDIS_PASSWORD=
```

**Note:** For local development without Redis, the app will use in-memory session storage (development mode).

---

### 3.2 Create AI Service `.env` File

```bash
cd ai-service
```

Create `ai-service/.env`:
```env
PORT=5000
NODE_BACKEND_URL=http://localhost:3000
AZURE_SPEECH_KEY=your_azure_speech_key_here
AZURE_SPEECH_REGION=westeurope
```

**Important:** You'll need an Azure Speech Service key. See [Section 5.2](#52-create-speech-service) to create one.

**Temporary workaround (optional):**
- You can use a colleague's key for initial local testing
- Replace with your own key before deploying to production

---

### 3.3 Create Frontend `.env` File

```bash
cd frontend
```

Create `frontend/.env`:
```env
VITE_BACKEND_URL=http://localhost:3000
```

---

## 4. Testing Locally

### 4.1 Start All Services

Open **three terminal windows** and run each service:

**Terminal 1 - AI Service:**
```bash
cd ai-service
python src/app.py
# Should show: * Running on http://0.0.0.0:5000
```

**Terminal 2 - Backend:**
```bash
cd backend
npm start
# Should show: Server running on port 3000
```

**Terminal 3 - Frontend:**
```bash
cd frontend
npm run dev
# Should show: Local: http://localhost:5173
```

---

### 4.2 Open Browser

1. Navigate to **http://localhost:5173**
2. Open browser console (F12)
3. Click **"Start Session"** (creates speaker session)
4. Select source language: **English**
5. Click **"Start Speaking"** → Grant microphone permissions
6. Speak: **"Hello, this is a test"**

**Expected behavior:**
- "Recording..." indicator appears
- No errors in browser console
- Backend logs show: `audio_chunk received`
- AI logs show: `process_audio` requests

---

### 4.3 Test Translation (Two Browsers)

**Browser 1 (Speaker):**
1. Create session, start speaking

**Browser 2 or Incognito (Listener):**
1. Go to http://localhost:5173
2. Click **"Join Session"**
3. Enter the 6-character session code
4. Select target language: **Spanish**
5. Click **"Join"**

**Browser 1:** Speak: "This is sentence number one"

**Browser 2 Expected:**
- Spanish subtitle appears within 2 seconds
- Spanish audio plays within 3-5 seconds (⚠️ **Known Issue:** Audio may not work due to P0 TTS bug, see [PLAN.md](PLAN.md))

---

## 5. Azure Account & Resources Setup

Follow these steps if you need to deploy to Azure (production) or create your own Azure resources.

### 5.1 Create Azure Account

1. Go to https://azure.microsoft.com/free/
2. Sign up for free trial (includes $200 credit for 30 days)
3. Activate subscription

---

### 5.2 Create Speech Service

```bash
# Set variables
RESOURCE_GROUP="vavilon-rg"
LOCATION="westeurope"
SPEECH_NAME="vavilon-speech"

# Create resource group
az group create --name $RESOURCE_GROUP --location $LOCATION

# Register Speech Services provider (if first time)
az provider register --namespace Microsoft.CognitiveServices

# Create Speech Service
az cognitiveservices account create \
  --name $SPEECH_NAME \
  --resource-group $RESOURCE_GROUP \
  --kind SpeechServices \
  --sku S0 \
  --location $LOCATION

# Get keys
az cognitiveservices account keys list \
  --name $SPEECH_NAME \
  --resource-group $RESOURCE_GROUP
```

**Copy Key1** and update your `ai-service/.env`:
```env
AZURE_SPEECH_KEY=<paste-key1-here>
AZURE_SPEECH_REGION=westeurope
```

---

### 5.3 Create Redis Cache (Optional for Local Dev)

For production or advanced local testing:

```bash
az redis create \
  --name vavilon-cache \
  --resource-group vavilon-rg \
  --location westeurope \
  --sku Basic \
  --vm-size C0

# Get connection string
az redis show \
  --name vavilon-cache \
  --resource-group vavilon-rg \
  --query "hostName" -o tsv

# Get access key
az redis list-keys \
  --name vavilon-cache \
  --resource-group vavilon-rg \
  --query "primaryKey" -o tsv
```

Update `backend/.env`:
```env
REDIS_URL=rediss://vavilon-cache.redis.cache.windows.net:6380
REDIS_PASSWORD=<paste-primary-key-here>
```

---

### 5.4 Create Container Registry (for Docker Images)

```bash
az acr create \
  --name vavilonacr \
  --resource-group vavilon-rg \
  --sku Basic \
  --location westeurope \
  --admin-enabled true

# Get credentials
az acr credential show --name vavilonacr --resource-group vavilon-rg
```

**Save these credentials** — you'll need them for GitHub Secrets.

---

### 5.5 Deploy AI Service Container

```bash
# Build and push Docker image
cd ai-service
az acr build --registry vavilonacr --image vavilon-ai:latest .

# Get ACR password
ACR_PASSWORD=$(az acr credential show --name vavilonacr --resource-group vavilon-rg --query "passwords[0].value" -o tsv)

# Create container instance
az container create \
  --name vavilon-ai \
  --resource-group vavilon-rg \
  --image vavilonacr.azurecr.io/vavilon-ai:latest \
  --registry-login-server vavilonacr.azurecr.io \
  --registry-username vavilonacr \
  --registry-password "$ACR_PASSWORD" \
  --os-type Linux \
  --cpu 1 \
  --memory 1.5 \
  --dns-name-label vavilon-ai \
  --ports 5000 \
  --environment-variables \
    PORT=5000 \
    AZURE_SPEECH_KEY=<your-speech-key> \
    AZURE_SPEECH_REGION=westeurope \
    NODE_BACKEND_URL=https://vavilon-backend.azurewebsites.net
```

**Verify container running:**
```bash
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView.state"
# Should show: "Running"

# Test health endpoint
curl http://vavilon-ai.westeurope.azurecontainer.io:5000/health
```

---

### 5.6 Deploy Backend (Node.js App Service)

```bash
# Create App Service Plan
az appservice plan create \
  --name vavilon-plan \
  --resource-group vavilon-rg \
  --location westeurope \
  --sku B1 \
  --is-linux

# Create Web App
az webapp create \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --plan vavilon-plan \
  --runtime "NODE:18-lts"

# Configure environment variables
az webapp config appsettings set \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --settings \
    PORT=3000 \
    AI_SERVICE_URL=http://vavilon-ai.westeurope.azurecontainer.io:5000 \
    FRONTEND_URL=https://vavilonapp.rs \
    REDIS_URL=rediss://vavilon-cache.redis.cache.windows.net:6380 \
    REDIS_PASSWORD=<your-redis-key>

# Deploy code (manual)
cd backend
python -c "import zipfile,os;z=zipfile.ZipFile('deploy.zip','w',zipfile.ZIP_DEFLATED);[z.write(os.path.join(r,f)) for r,_,fs in os.walk('src') for f in fs];[z.write(x) for x in ['package.json','package-lock.json']];z.close()"

az webapp deployment source config-zip \
  --resource-group vavilon-rg \
  --name vavilon-backend \
  --src deploy.zip

rm deploy.zip
cd ..
```

**Enable WebSockets:**
```bash
az webapp config set \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --web-sockets-enabled true
```

---

### 5.7 Deploy Frontend (Static Web App)

**Option A: Via Azure Portal (Recommended for first time)**

1. Go to https://portal.azure.com
2. Search for **"Static Web Apps"** → Click **"Create"**
3. Fill in:
   - Subscription: Your subscription
   - Resource Group: `vavilon-rg`
   - Name: `vavilon-frontend`
   - Region: `West Europe 2`
   - Source: **GitHub**
   - GitHub Account: Authorize and select your repository
   - Organization: `bantula` (or your username)
   - Repository: `Vavilon_webapp`
   - Branch: `main`
   - Build Presets: **React**
   - App location: `/frontend`
   - Api location: *(leave empty)*
   - Output location: `dist`
4. Click **"Review + Create"**
5. Wait for deployment (~2 minutes)

**Option B: Via Azure CLI**

```bash
az staticwebapp create \
  --name vavilon-frontend \
  --resource-group vavilon-rg \
  --location "West Europe 2" \
  --source https://github.com/bantula/Vavilon_webapp \
  --branch main \
  --app-location "/frontend" \
  --output-location "dist" \
  --token <github-personal-access-token>
```

**Note:** Azure automatically creates a GitHub Actions workflow file (`.github/workflows/azure-static-web-apps-*.yml`) in your repository.

---

## 6. GitHub CI/CD Configuration

### 6.1 Required GitHub Secrets

Go to your repository: https://github.com/bantula/Vavilon_webapp

Navigate to: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add the following **3 secrets**:

---

#### Secret 1: `AZURE_WEBAPP_PUBLISH_PROFILE`

**Description:** Allows GitHub Actions to deploy backend to Azure App Service.

**How to get the value:**
```bash
az webapp deployment list-publishing-profiles \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --xml > backend-publish-profile.xml
```

**Value:** Copy the entire contents of `backend-publish-profile.xml` file and paste as the secret value.

---

#### Secret 2: `ACR_USERNAME`

**Value:**
```
vavilonacr
```

---

#### Secret 3: `ACR_PASSWORD`

**How to get the value:**
```bash
az acr credential show --name vavilonacr --resource-group vavilon-rg --query "passwords[0].value" -o tsv
```

**Value:** Paste the output from the command above.

---

### 6.2 Verify Existing GitHub Workflows

Check that these workflow files exist in your repository:

```
.github/workflows/
├── azure-static-web-apps-*.yml  (Frontend - Auto-created by Azure)
└── deploy.yml                    (Backend + AI - May need to create)
```

**If `deploy.yml` doesn't exist**, create it:

```yaml
# .github/workflows/deploy.yml
name: Deploy Vavilon

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  deploy-backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: '18'
      - name: Install dependencies
        run: |
          cd backend
          npm ci
      - name: Deploy to Azure Web App
        uses: azure/webapps-deploy@v2
        with:
          app-name: vavilon-backend
          publish-profile: ${{ secrets.AZURE_WEBAPP_PUBLISH_PROFILE }}
          package: backend

  build-ai-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Login to Azure Container Registry
        uses: docker/login-action@v2
        with:
          registry: vavilonacr.azurecr.io
          username: ${{ secrets.ACR_USERNAME }}
          password: ${{ secrets.ACR_PASSWORD }}
      - name: Build and push Docker image
        run: |
          cd ai-service
          docker build -t vavilonacr.azurecr.io/vavilon-ai:latest .
          docker push vavilonacr.azurecr.io/vavilon-ai:latest
```

---

### 6.3 Test GitHub Actions

```bash
# Commit the workflow file (if you created it)
git add .github/workflows/deploy.yml
git commit -m "Add CI/CD workflow for backend and AI service"
git push origin main

# Monitor deployment
# Open: https://github.com/bantula/Vavilon_webapp/actions
```

**Expected:**
- ✅ Green checkmark after 5-8 minutes
- Backend deployed to Azure App Service
- AI Docker image pushed to Container Registry

**After AI deployment completes:**
```bash
# Restart container to pull latest image
az container restart --name vavilon-ai --resource-group vavilon-rg
```

---

## 7. First Production Deployment

### 7.1 Complete Deployment Checklist

Run through this checklist for your first production deployment:

```bash
# 1. Verify all Azure resources created
az group show --name vavilon-rg

# 2. Verify GitHub secrets added
# Check: https://github.com/bantula/Vavilon_webapp/settings/secrets/actions

# 3. Commit and push code
git add -A
git commit -m "Initial production deployment"
git push origin main

# 4. Monitor GitHub Actions
# Open: https://github.com/bantula/Vavilon_webapp/actions

# 5. Wait for workflows to complete (~5-8 minutes)

# 6. Restart AI container (CRITICAL)
az container restart --name vavilon-ai --resource-group vavilon-rg

# 7. Wait for container to start
sleep 30
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView.state"
# Should show: "Running"
```

---

### 7.2 Verify Production Deployment

**Check Frontend:**
```bash
# Open in browser
open https://vavilonapp.rs
# or
open https://green-pond-05766a403.1.azurestaticapps.net
```

**Check Backend:**
```bash
curl https://vavilon-backend.azurewebsites.net/health
# Should return: {"status":"ok"}
```

**Check AI Service:**
```bash
curl http://vavilon-ai.westeurope.azurecontainer.io:5000/health
# Should return: {"status":"healthy"}
```

---

### 7.3 End-to-End Production Test

1. Open https://vavilonapp.rs in two browser windows
2. **Window 1 (Speaker):**
   - Click "Start Session"
   - Select source language: English
   - Click "Start Speaking"
   - Speak: "This is a production test"
3. **Window 2 (Listener):**
   - Click "Join Session"
   - Enter session code from Window 1
   - Select target language: Spanish
   - Click "Join"
   - **Expected:** Spanish subtitle appears within 2 seconds
   - **Known Issue:** Audio may not play (see [PLAN.md](PLAN.md) for TTS bug)

---

### 7.4 Check Production Logs

**Backend logs:**
```bash
az webapp log tail --name vavilon-backend --resource-group vavilon-rg
```

**AI container logs:**
```bash
az container logs --name vavilon-ai --resource-group vavilon-rg
```

**Look for:**
- ✅ `segment_finalized_received` (recognition working)
- ✅ `tts_languages_requested` (Node requesting TTS)
- ⚠️ `missing_tts_for_active_language` (known TTS bug)
- ❌ Python tracebacks (errors)

---

## 8. Troubleshooting

### 8.1 Local Development Issues

**Problem:** `Cannot connect to AI service`

**Fix:**
```bash
# Check AI service is running
curl http://localhost:5000/health

# If not running, restart:
cd ai-service
python src/app.py
```

---

**Problem:** `AZURE_SPEECH_KEY not found`

**Fix:**
```bash
# Ensure .env file exists and has the correct key
cd ai-service
cat .env  # Should show AZURE_SPEECH_KEY=<your-key>

# If missing, add it:
echo "AZURE_SPEECH_KEY=your_actual_key_here" >> .env
echo "AZURE_SPEECH_REGION=westeurope" >> .env
```

---

**Problem:** `npm install` fails with permission errors

**Fix (Windows):**
```powershell
# Run PowerShell as Administrator, then:
npm cache clean --force
npm install
```

**Fix (macOS/Linux):**
```bash
sudo chown -R $USER ~/.npm
npm install
```

---

### 8.2 Azure Deployment Issues

**Problem:** `az command not found`

**Fix:**
```bash
# Reinstall Azure CLI
# Windows:
winget install --id Microsoft.AzureCLI -e

# macOS:
brew install azure-cli

# Then login:
az login --use-device-code
```

---

**Problem:** Backend deployment fails: "No subscription found"

**Fix:**
```bash
az account list --output table
az account set --subscription "<your-subscription-id>"
```

---

**Problem:** Container instance stuck in "Waiting" state

**Fix:**
```bash
# Check container logs for errors
az container logs --name vavilon-ai --resource-group vavilon-rg

# If image pull failed, verify ACR credentials:
az acr credential show --name vavilonacr --resource-group vavilon-rg

# Recreate container with correct credentials
az container delete --name vavilon-ai --resource-group vavilon-rg --yes
# Then re-run the create command from Section 5.5
```

---

**Problem:** GitHub Actions fails: "ACR authentication failed"

**Fix:**
```bash
# Regenerate ACR credentials
az acr credential show --name vavilonacr --resource-group vavilon-rg

# Update GitHub secret ACR_PASSWORD with new value
# Go to: https://github.com/bantula/Vavilon_webapp/settings/secrets/actions
```

---

**Problem:** Frontend shows "Cannot connect to backend"

**Fix:**
```bash
# Check CORS configuration
az webapp cors show --name vavilon-backend --resource-group vavilon-rg

# Add frontend URL to CORS
az webapp cors add \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --allowed-origins https://vavilonapp.rs https://green-pond-05766a403.1.azurestaticapps.net
```

---

### 8.3 Getting Help

**Resources:**
- [README.md](README.md) — Technical overview and API reference
- [PLAN.md](PLAN.md) — Current issues and fix plans
- [AZURE_DEPLOYMENT.md](AZURE_DEPLOYMENT.md) — Detailed Azure operational runbook
- Azure Documentation: https://docs.microsoft.com/azure/
- GitHub Issues: https://github.com/bantula/Vavilon_webapp/issues

**Logs to check:**
```bash
# Backend logs (real-time)
az webapp log tail --name vavilon-backend --resource-group vavilon-rg

# AI container logs
az container logs --name vavilon-ai --resource-group vavilon-rg

# Frontend logs
# Go to: Azure Portal → Static Web Apps → Logs
```

---

## Quick Reference Card

### Local Development
```bash
# Start all services (3 terminals)
cd ai-service && python src/app.py      # Terminal 1
cd backend && npm start                  # Terminal 2
cd frontend && npm run dev               # Terminal 3

# Open browser: http://localhost:5173
```

### Production Deployment
```bash
# Deploy via GitHub Actions
git add -A
git commit -m "deployment message"
git push origin main

# Restart AI container after deployment
az container restart --name vavilon-ai --resource-group vavilon-rg

# Verify: https://vavilonapp.rs
```

### Check Status
```bash
# Backend status
az webapp show --name vavilon-backend --resource-group vavilon-rg --query "state"

# AI container status
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView.state"

# View logs
az webapp log tail --name vavilon-backend --resource-group vavilon-rg
az container logs --name vavilon-ai --resource-group vavilon-rg
```

---

**Setup Complete!** You're now ready to develop and deploy Vavilon. 🚀
