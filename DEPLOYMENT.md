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

## Step 1: Create Azure Resources

### 1.1 Speech Service

```bash
az cognitiveservices account create \
  --name vavilon-speech \
  --resource-group vavilon-rg \
  --kind SpeechServices \
  --sku S0 \
  --location eastus

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
  --location eastus \
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

# Login to ACR
az acr login --name vavilonacr

# Push image
docker push vavilonacr.azurecr.io/vavilon-ai:latest
```

### 2.3 Deploy to Container Instance

```bash
az container create \
  --name vavilon-ai \
  --resource-group vavilon-rg \
  --image vavilonacr.azurecr.io/vavilon-ai:latest \
  --dns-name-label vavilon-ai \
  --ports 5000 \
  --environment-variables \
    PORT=5000 \
    AZURE_SPEECH_KEY=<your-key> \
    AZURE_SPEECH_REGION=eastus \
    NODE_BACKEND_URL=https://vavilon-backend.azurewebsites.net
```

## Step 3: Deploy Backend (Node.js)

### 3.1 Create App Service

```bash
az webapp create \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --plan vavilon-plan \
  --runtime "NODE|18-lts"
```

### 3.2 Configure Environment

```bash
az webapp config appsettings set \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --settings \
    PORT=8080 \
    AI_SERVICE_URL=https://vavilon-ai.eastus.azurecontainer.io:5000 \
    FRONTEND_URL=https://vavilon-app.azurestaticapps.net \
    REDIS_URL=<your-redis-connection-string>
```

### 3.3 Deploy Code

```bash
cd backend

# Build
npm install --production

# Deploy via ZIP
zip -r deploy.zip .
az webapp deployment source config-zip \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --src deploy.zip
```

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
az staticwebapp create \
  --name vavilon-app \
  --resource-group vavilon-rg \
  --location eastus2 \
  --source https://github.com/yourorg/vavilon \
  --branch main \
  --app-location "/frontend" \
  --output-location "dist"
```

### 4.2 Configure Build

Update `frontend/vite.config.js`:

```javascript
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist'
  },
  server: {
    proxy: {
      '/api': {
        target: 'https://vavilon-backend.azurewebsites.net',
        changeOrigin: true
      },
      '/ws': {
        target: 'wss://vavilon-backend.azurewebsites.net',
        ws: true
      }
    }
  }
})
```

### 4.3 Build and Deploy

```bash
cd frontend
npm run build

# Deploy via Azure Static Web Apps CLI
npm install -g @azure/static-web-apps-cli
swa deploy ./dist \
  --app-name vavilon-app \
  --resource-group vavilon-rg
```

## Step 5: Update Session Storage (Redis)

Update `backend/src/services/sessionService.js`:

```javascript
const redis = require('redis');

const client = redis.createClient({
  url: process.env.REDIS_URL
});

client.connect();

// Replace Map with Redis
async function createSession() {
  // ... existing code ...
  await client.setEx(`session:${sessionId}`, 86400, JSON.stringify(session));
  await client.setEx(`code:${joinCode}`, 86400, sessionId);
  return session;
}

async function getSession(idOrCode) {
  const sessionData = await client.get(`session:${idOrCode}`);
  if (sessionData) return JSON.parse(sessionData);

  const sessionId = await client.get(`code:${idOrCode}`);
  if (sessionId) {
    const session = await client.get(`session:${sessionId}`);
    return JSON.parse(session);
  }
  return null;
}
```

## Step 6: Configure DNS and SSL

### 6.1 Custom Domain (Optional)

```bash
# Add custom domain
az staticwebapp hostname set \
  --name vavilon-app \
  --hostname app.vavilon.com

# SSL is automatic with Azure Static Web Apps
```

### 6.2 CORS Configuration

```bash
az webapp cors add \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --allowed-origins https://vavilon-app.azurestaticapps.net
```

## Step 7: Monitoring and Logging

### 7.1 Enable Application Insights

```bash
az monitor app-insights component create \
  --app vavilon-insights \
  --location eastus \
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
  --location eastus

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