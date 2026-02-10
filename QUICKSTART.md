# Quick Start Guide

Get Vavilon running in 5 minutes.

## 1. Install Dependencies

```bash
# Install all dependencies
npm install
cd backend && npm install && cd ..
cd frontend && npm install && cd ..
cd ai-service && pip install -r requirements.txt && cd ..
```

## 2. Get Azure Credentials

1. Go to [Azure Portal](https://portal.azure.com)
2. Create **Speech Service** resource
3. Copy **Key** and **Region**

## 3. Configure Environment

```bash
# Create AI service config
cp ai-service/.env.example ai-service/.env
```

Edit `ai-service/.env`:
```env
AZURE_SPEECH_KEY=your_actual_key_here
AZURE_SPEECH_REGION=eastus
```

## 4. Run the App

```bash
npm run dev
```

This starts:
- Backend (Node.js) on port 3000
- AI Service (Python) on port 5000
- Frontend (React) on port 5173

## 5. Test It

1. Open http://localhost:5173
2. Click **"Start a Tour"**
3. In another browser/tab, click **"Join a Tour"**
4. Enter the session code
5. As speaker: Click "Start Speaking"
6. As listener: Select language and hear translation

## Common Issues

**Port already in use?**
```bash
# Windows
netstat -ano | findstr :3000
taskkill /PID <pid> /F

# Mac/Linux
lsof -ti:3000 | xargs kill -9
```

**Python not found?**
```bash
# Use python3 explicitly
cd ai-service
python3 src/app.py
```

**Azure errors?**
- Double-check your AZURE_SPEECH_KEY
- Verify AZURE_SPEECH_REGION matches your resource (e.g., "eastus")
- Check Azure Portal for service health

## Next Steps

- Read [README.md](README.md) for full documentation
- Review architecture diagrams
- Check [API endpoints](#api-endpoints) for integration
- Plan production deployment

## Demo Checklist

Before showing to tour agency:

- [ ] All 3 services running
- [ ] Microphone permission granted
- [ ] Test with 2+ browsers
- [ ] Verify audio playback works
- [ ] Check subtitles display
- [ ] Test QR code scanning
- [ ] Prepare backup device
- [ ] Have Azure credentials ready

Good luck with your demo!