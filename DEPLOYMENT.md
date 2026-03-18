# Vavilon — Deployment Runbook

**Production URL:** https://www.vavilonapp.rs
**Resource group:** `vavilon-rg` (West Europe)

---

## Deploy Changes (Run Every Time)

```bash
# 1. Commit & push
git add -A
git commit -m "feat/fix: description"
git push origin main

# 2. Deploy backend
cd backend
python -c "
import zipfile, os
z = zipfile.ZipFile('deploy.zip', 'w', zipfile.ZIP_DEFLATED)
for entry in ['package.json', 'package-lock.json']:
    z.write(entry)
for folder in ['src', 'scripts', 'data']:
    for r, _, fs in os.walk(folder):
        for f in fs:
            z.write(os.path.join(r, f))
z.close()
"
az webapp deployment source config-zip --resource-group vavilon-rg --name vavilon-backend --src deploy.zip
rm deploy.zip
cd ..

# 3. Restart AI container (ALWAYS — even if only backend changed)
az container restart --name vavilon-ai --resource-group vavilon-rg
```

Frontend auto-deploys via GitHub Actions on push (~3 min).
Backend build takes ~3 min. AI container takes ~30 sec to come back up.

---

## Verify Everything Is Up

```bash
# Backend health
curl https://vavilon-backend.azurewebsites.net/health

# AI container state
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView.state"

# AI container logs (look for startup line)
az container logs --name vavilon-ai --resource-group vavilon-rg
```

Expected: backend returns `{"status":"ok"}`, container state is `"Running"`, logs show `startup` with `azure_configured: true`.

---

## Import / Update Guides

Edit `backend/data/guides.csv`, then run:

```bash
cd backend
REDIS_URL=vavilon-redis.redis.cache.windows.net \
REDIS_PASSWORD='<redis-primary-key>' \
node scripts/import-guides.js
```

CSV format: `name,surname,username,email,phone,access_start_date,access_end_date`
Multiple rows with the same username = multiple access windows.

---

## Live Logs

```bash
# Backend (streaming)
az webapp log tail --name vavilon-backend --resource-group vavilon-rg

# AI container
az container logs --name vavilon-ai --resource-group vavilon-rg
```

---

## Troubleshooting

### "Failed to start translation session"
The AI container process has died (container shows Running but Flask is frozen).

```bash
az container restart --name vavilon-ai --resource-group vavilon-rg
```

### Backend not responding / 500 errors
```bash
az webapp restart --name vavilon-backend --resource-group vavilon-rg
```

### Frontend not updating
Check GitHub Actions: https://github.com/bantula/Vavilon_webapp/actions
If the workflow failed, fix and push again.

---

## Emergency Rollback

```bash
# Find last working commit
git log --oneline -10

# Revert
git revert HEAD
git push origin main

# Redeploy backend (steps above), restart AI container
az container restart --name vavilon-ai --resource-group vavilon-rg
```
