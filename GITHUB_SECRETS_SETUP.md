# GitHub Secrets Setup

To enable automated CI/CD deployments, add the following secrets to your GitHub repository.

## How to Add Secrets to GitHub

1. Go to your GitHub repository: https://github.com/bantula/Vavilon_webapp
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret** for each secret below

## Required Secrets

### 1. AZURE_WEBAPP_PUBLISH_PROFILE

**Description:** Allows GitHub Actions to deploy the Node.js backend to Azure App Service.

**Value:** Copy the entire contents of the file `backend-publish-profile.xml` that was just created in your workspace.

**How to get it:**
```bash
# File is already created at: backend-publish-profile.xml
# Copy its entire XML content as the secret value
```

---

### 2. ACR_USERNAME

**Description:** Azure Container Registry username for pushing Docker images.

**Value:** 
```
vavilonacr
```

---

### 3. ACR_PASSWORD

**Description:** Azure Container Registry password for authentication.

**Value:** 
```
FIRMPZd7hlzrciYmhVVo4bjwzUjogTp541JtQclUOIu23lA6kBVUJQQJ99CBACc5RqLJEqg7NAAACAZCRmXAL
```

---

## What Happens After Setup

Once you add these secrets and push code to the `main` branch:

1. **Frontend** (already working): Auto-deploys via the existing Static Web Apps workflow
2. **Backend**: Auto-deploys to Azure App Service via the new `deploy.yml` workflow
3. **AI Service**: Docker image is built and pushed to ACR (you'll need to manually restart the container instance)

## Testing the Workflow

After adding the secrets:

```bash
# Commit and push the workflow files
git add .github/workflows/
git commit -m "Add CI/CD workflows"
git push origin main

# Monitor the deployment
# Go to: https://github.com/bantula/Vavilon_webapp/actions
```

## Manual Container Restart (After AI Deployment)

After the AI service workflow completes, restart the container to pull the latest image:

```bash
az container restart --name vavilon-ai --resource-group vavilon-rg
```

## Troubleshooting

**If backend deployment fails:**
- Verify `AZURE_WEBAPP_PUBLISH_PROFILE` contains the full XML (no truncation)
- Check the Actions tab for error messages

**If AI deployment fails:**
- Verify ACR credentials are correct
- Check Docker build logs in Actions tab

**To regenerate publish profile:**
```bash
az webapp deployment list-publishing-profiles \
  --name vavilon-backend \
  --resource-group vavilon-rg \
  --xml > backend-publish-profile.xml
```

**To regenerate ACR credentials:**
```bash
az acr credential show --name vavilonacr --resource-group vavilon-rg
```

---

**Next Step:** Add these 3 secrets to GitHub, then commit and push the workflow files to trigger your first automated deployment! 🚀
