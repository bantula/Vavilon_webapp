# Azure Deployment Guide — Complete Workflow

**Last Updated:** February 15, 2026  
**Purpose:** Step-by-step instructions to deploy all three Vavilon services to Azure and verify everything is live.

---

## Prerequisites

✅ Azure CLI installed and logged in  
✅ Git repository up to date locally  
✅ All code changes tested locally (optional but recommended)  
✅ Azure resources already created (resource group, services, etc.)

---

## Deployment Workflow Overview

```
Local Changes → Git Commit → Push to GitHub → GitHub Actions Deploy → Manual Verification
```

### Services to Deploy
1. **Frontend** — Azure Static Web Apps (auto-deploys via GitHub Actions)
2. **Backend** — Azure App Service (manual deployment via Azure CLI)
3. **AI Service** — Azure Container Instance (Docker image + restart)

---

## Step 1: Commit and Push to GitHub

### 1.1 Check Git Status

```powershell
cd C:\Users\Dell\VS\Vavilon_webapp
git status
```

**Expected output:**
- List of modified/new files
- Current branch name (should be `main` or feature branch)

### 1.2 Stage All Changes

```powershell
# Stage all changes
git add -A

# Review what will be committed
git status
```

### 1.3 Commit Changes

```powershell
# Commit with descriptive message
git commit -m "fix: [brief description of what you fixed]"

# Example:
# git commit -m "fix: improve Redis connection resilience and TTS event delivery"
```

### 1.4 Push to Main Branch

```powershell
# If on main branch
git push origin main

# If on feature branch, merge to main first:
git checkout main
git merge your-feature-branch
git push origin main
```

**What happens:** Pushing to `main` triggers GitHub Actions workflows automatically.

---

## Step 2: Monitor GitHub Actions Deployments

### 2.1 Check Workflow Status

Open browser: **https://github.com/bantula/Vavilon_webapp/actions**

You should see two workflows running:
1. **Deploy Vavilon** (Backend + AI Docker build)
2. **Azure Static Web Apps CI/CD** (Frontend)

### 2.2 Wait for Completion

**Typical durations:**
- Frontend: 2-3 minutes
- Backend: 3-5 minutes
- AI Docker build: 5-8 minutes

**Success indicators:**
- ✅ Green checkmark next to workflow
- Status: "Success" or "Completed"

**If red ❌ appears:**
1. Click on the failed workflow
2. Expand the failed step
3. Read error logs
4. Fix issue locally, commit, push again

---

## Step 3: Deploy Backend Manually

**Note:** If GitHub Actions "Deploy Vavilon" completed successfully, backend is already deployed. This step is for manual deployment or if GitHub Actions failed.

### 3.1 Navigate to Backend Directory

```powershell
cd backend
```

### 3.2 Create Deployment Package

```powershell
python -c "import zipfile, os; z=zipfile.ZipFile('deploy.zip','w',zipfile.ZIP_DEFLATED); z.write('package.json'); z.write('package-lock.json'); [z.write(os.path.join(r,f),os.path.join(r,f)) for r,_,fs in os.walk('src') for f in fs]; z.close()"
```

**Expected:** Creates `deploy.zip` (~30-50 KB)

Verify:
```powershell
ls deploy.zip
```

### 3.3 Deploy to Azure App Service

```powershell
az webapp deploy --resource-group vavilon-rg --name vavilon-backend --src-path "$(Get-Location)\deploy.zip" --type zip
```

**Expected output:**
```
Deployment has completed successfully
Status: RuntimeSuccessful
You can visit your app at: http://vavilon-backend.azurewebsites.net
```

**Typical duration:** 30-60 seconds

### 3.4 Clean Up

```powershell
Remove-Item deploy.zip -Force
cd ..
```

---

## Step 4: Restart AI Container (CRITICAL)

**Why:** After GitHub Actions builds a new Docker image, the container must be restarted to pull and run the latest image.

### 4.1 Restart Container

```powershell
az container restart --name vavilon-ai --resource-group vavilon-rg
```

**Expected output:**
```json
{
  "containers": [...],
  "id": "/subscriptions/.../vavilon-ai",
  "instanceView": {
    "state": "Running"
  },
  ...
}
```

**Typical duration:** 20-40 seconds

### 4.2 Wait for Container to Start

```powershell
Start-Sleep -Seconds 30
```

### 4.3 Verify Container Status

```powershell
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView.state"
```

**Expected output:** `"Running"`

**If "Waiting" or "Terminated":**
```powershell
# Check logs for errors
az container logs --name vavilon-ai --resource-group vavilon-rg
```

---

## Step 5: Verify Deployments

### 5.1 Check Frontend

**URL:** https://www.vavilonapp.rs (or https://green-pond-05766a403.1.azurestaticapps.net)

Open in browser. **Expected:**
- Landing page loads
- No console errors (press F12 → Console tab)
- "Create Session" and "Join Session" buttons visible

### 5.2 Check Backend

```powershell
# Test backend connectivity
Invoke-WebRequest -Uri "https://vavilon-backend.azurewebsites.net" -Method Get -UseBasicParsing
```

**Expected:**
- StatusCode: 200 or 404 (not 500 or timeout)
- Response within 2-3 seconds

### 5.3 Check AI Container Logs

```powershell
az container logs --name vavilon-ai --resource-group vavilon-rg
```

**Expected logs:**
```json
{"level":"info","component":"python","step":"startup","port":5000,"azure_configured":true}
* Running on http://0.0.0.0:5000
```

**Bad signs:**
- Python tracebacks
- "Error" or "CRITICAL" entries
- Container restart loops

---

## Step 6: End-to-End Testing

### 6.1 Create Session (Speaker)

1. Open https://www.vavilonapp.rs
2. Click **"Create Session"**
3. Select source language (e.g., English)
4. Copy the **Session Code** (6 characters)
5. Click **"Start Speaking"**
6. Grant microphone permissions
7. Speak a test sentence: "Hello, this is a test"

**Expected:**
- "Recording..." indicator appears
- No errors in console

### 6.2 Join Session (Listener)

1. Open new browser tab/window (or different device)
2. Go to https://www.vavilonapp.rs
3. Click **"Join Session"**
4. Enter the session code
5. Select target language (e.g., Spanish)
6. Click **"Join"**

**Expected:**
- "Connected" status appears
- Session code matches

### 6.3 Verify Translation

1. **Speaker window:** Say "This is sentence number one"
2. **Listener window:** Should see:
   - ✅ Spanish subtitle appears within 2 seconds
   - ✅ Spanish audio plays within 3-5 seconds

3. **Speaker window:** Say "This is sentence number two"
4. **Listener window:** Should see:
   - ✅ Another subtitle
   - ✅ Another audio clip

**Repeat for 3-5 sentences to ensure stability.**

### 6.4 Check Backend Logs (Optional)

```powershell
az webapp log tail --name vavilon-backend --resource-group vavilon-rg
```

**Look for:**
- `segment_finalized_received` (recognition working)
- `tts_languages_requested` (Node requesting TTS)
- `generate_tts_sent` (Node calling Python)
- `tts_ready_received` (Python returning audio)

**Bad signs:**
- `missing_tts_for_active_language` (TTS not delivered)
- `audio_dispatch_fail` (timeouts)
- `SocketClosedUnexpectedlyError` (Redis disconnects)

### 6.5 Check AI Container Logs (Optional)

```powershell
az container logs --name vavilon-ai --resource-group vavilon-rg
```

**Look for:**
- `start_session` (session created)
- `process_audio` (audio chunks received)
- `segment_finalized` (recognition events)
- `generate_tts` (TTS requests received)

**Bad signs:**
- Python exceptions/tracebacks
- `SESSION_DEAD` errors
- Azure SDK errors

---

## Step 7: Post-Deployment Checklist

Run through this checklist after every deployment:

- [ ] GitHub Actions workflows both show ✅ green
- [ ] Frontend loads at www.vavilonapp.rs
- [ ] Backend responds (not 500 error)
- [ ] AI container shows "Running" state
- [ ] AI container logs show no Python errors
- [ ] Can create speaker session
- [ ] Can join as listener
- [ ] Subtitles appear when speaking
- [ ] Audio plays after subtitles (within 5 seconds)
- [ ] Multiple sentences work (not just the first one)
- [ ] Backend logs show `tts_ready_received` events
- [ ] No `missing_tts_for_active_language` warnings in logs

**If all checked:** ✅ Deployment successful!

---

## Quick Reference Commands

### Deploy Everything (Fast Path)

```powershell
# 1. Commit and push
git add -A
git commit -m "fix: deployment message here"
git push origin main

# 2. Wait for GitHub Actions (~5 min)
# Check: https://github.com/bantula/Vavilon_webapp/actions

# 3. Restart AI container
az container restart --name vavilon-ai --resource-group vavilon-rg

# 4. Wait and verify
Start-Sleep -Seconds 30
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView.state"

# 5. Test at www.vavilonapp.rs
```

### Check Deployment Status

```powershell
# Backend status
az webapp show --name vavilon-backend --resource-group vavilon-rg --query "state"

# AI container status
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView.state"

# Frontend (GitHub Actions)
# Open: https://github.com/bantula/Vavilon_webapp/actions
```

### View Logs

```powershell
# Backend logs (real-time)
az webapp log tail --name vavilon-backend --resource-group vavilon-rg

# AI container logs (last 100 lines)
az container logs --name vavilon-ai --resource-group vavilon-rg

# Frontend logs
# Go to: https://portal.azure.com → Static Web Apps → green-pond-05766a403 → Logs
```

### Troubleshooting Commands

```powershell
# Force backend restart
az webapp restart --name vavilon-backend --resource-group vavilon-rg

# Check AI container restart count
az container show --name vavilon-ai --resource-group vavilon-rg --query "containers[0].instanceView.restartCount"

# Re-deploy backend manually (if GitHub Actions failed)
cd backend
python -c "import zipfile, os; z=zipfile.ZipFile('deploy.zip','w',zipfile.ZIP_DEFLATED); z.write('package.json'); z.write('package-lock.json'); [z.write(os.path.join(r,f),os.path.join(r,f)) for r,_,fs in os.walk('src') for f in fs]; z.close()"
az webapp deploy --resource-group vavilon-rg --name vavilon-backend --src-path "$(Get-Location)\deploy.zip" --type zip
Remove-Item deploy.zip
cd ..
```

---

## Common Issues and Fixes

### Issue: Frontend shows "Cannot connect to backend"

**Cause:** Backend not running or CORS misconfigured

**Fix:**
```powershell
# Check backend status
az webapp show --name vavilon-backend --resource-group vavilon-rg --query "state"

# Restart if needed
az webapp restart --name vavilon-backend --resource-group vavilon-rg
```

### Issue: Subtitles work but no audio

**Cause:** AI container not restarted after new Docker image built

**Fix:**
```powershell
# Restart container to pull latest image
az container restart --name vavilon-ai --resource-group vavilon-rg

# Wait 30 seconds
Start-Sleep -Seconds 30

# Verify running
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView.state"
```

### Issue: Backend logs show "SocketClosedUnexpectedlyError" (Redis)

**Cause:** Redis connection instability

**Fix:**
1. Check Redis status:
   ```powershell
   az redis show --name vavilon-cache --resource-group vavilon-rg --query "provisioningState"
   ```

2. If Redis is fine, deploy Redis resilience fixes from PLAN.md Phase 2

### Issue: "Session not found" errors in AI service

**Cause:** AI container restarted between session creation and audio processing

**Fix:**
- This is expected behavior (in-memory sessions)
- Speaker must stop and restart session after AI container restart
- Or: Implement Redis-backed session persistence (future enhancement)

### Issue: GitHub Actions shows red ❌

**Cause:** Build/deployment failure

**Fix:**
1. Click on failed workflow
2. Expand failed step
3. Common issues:
   - **Missing secrets:** Add required secrets in GitHub repo settings
   - **Syntax error:** Check code for typos
   - **ACR credentials:** Verify ACR_USERNAME and ACR_PASSWORD secrets
4. Fix locally, commit, push again

---

## Emergency Rollback

If new deployment breaks production:

### Option 1: Revert Git Commit

```powershell
# Find last working commit
git log --oneline -10

# Revert to that commit (example: ec6dce6)
git revert HEAD  # or git reset --hard ec6dce6
git push --force origin main

# Wait for GitHub Actions to redeploy old version
# Then restart AI container
az container restart --name vavilon-ai --resource-group vavilon-rg
```

### Option 2: Redeploy Previous Docker Image

```powershell
# List available images
az acr repository show-tags --name vavilonacr --repository vavilon-ai

# Recreate container with specific tag
az container create `
  --name vavilon-ai `
  --resource-group vavilon-rg `
  --image vavilonacr.azurecr.io/vavilon-ai:previous-tag `
  --registry-username vavilonacr `
  --registry-password <ACR_PASSWORD> `
  --os-type Linux --cpu 1 --memory 1.5 `
  --dns-name-label vavilon-ai --ports 5000 `
  --environment-variables `
    PORT=5000 `
    AZURE_SPEECH_KEY=<key> `
    AZURE_SPEECH_REGION=westeurope `
    NODE_BACKEND_URL=https://vavilon-backend.azurewebsites.net
```

---

## Maintenance Tasks

### Weekly Checks

```powershell
# Check Redis memory usage
az redis show --name vavilon-cache --resource-group vavilon-rg --query "redisConfiguration"

# Check backend resource usage
az webapp show --name vavilon-backend --resource-group vavilon-rg --query "usageState"

# Check AI container restart count (should be low)
az container show --name vavilon-ai --resource-group vavilon-rg --query "containers[0].instanceView.restartCount"
```

### Monthly Tasks

1. Review Application Insights metrics
2. Check Azure service quotas
3. Update dependencies (npm, pip packages)
4. Test disaster recovery (manual backup/restore)

---

## Support & Resources

**GitHub Repository:** https://github.com/bantula/Vavilon_webapp  
**Azure Portal:** https://portal.azure.com  
**Resource Group:** `vavilon-rg`  
**Region:** West Europe

**Key Documentation:**
- [COPILOT_CONTEXT.md](COPILOT_CONTEXT.md) — Architecture and data flow
- [PLAN.md](PLAN.md) — Current fix plan for TTS issues
- [DEPLOYMENT.md](DEPLOYMENT.md) — Initial Azure setup (resource creation)
- [TESTING_CHECKLIST.md](TESTING_CHECKLIST.md) — Comprehensive testing scenarios

**Azure Resources:**
- Frontend: green-pond-05766a403 (Static Web App)
- Backend: vavilon-backend (App Service)
- AI Service: vavilon-ai (Container Instance)
- Redis: vavilon-cache (Azure Cache for Redis)
- Speech: vavilon-speech (Cognitive Services)

---

**Last Successful Deployment:** [Update after each successful deployment]  
**Current Version:** [Git commit hash]  
**Known Issues:** See [PLAN.md](PLAN.md)
