# Vavilon - Real-Time Translation Web App

A production-ready MVP for real-time spoken translation for tours, museums, and conferences. One speaker broadcasts to multiple listeners, each receiving translations in their chosen language with audio and live subtitles.

## Architecture

```
Speaker (Browser)
    ↓ Audio Stream (WebSocket)
Node.js Backend (Port 3000)
    ↓ Audio forwarding
Python AI Service (Port 5000)
    ↓ Azure Speech SDK
    - STT (once per speaker)
    - Translation (once per language)
    - TTS (once per language)
    ↓
Node.js Backend
    ↓ Broadcast (WebSocket)
Listeners (Browsers, 200+)
```

## Features

- **One-to-Many Broadcasting**: Single speaker, unlimited listeners
- **Real-Time Translation**: Audio + live subtitles in 10 languages
- **Session Management**: Simple 6-character join codes + QR codes
- **WebSocket Streaming**: Low-latency audio and subtitle delivery
- **Azure-Powered AI**: Speech-to-Text, Translation, Text-to-Speech
- **No Authentication**: Quick join, no signup required

## Tech Stack

### Backend (Node.js)
- Express.js - HTTP API
- ws - WebSocket server
- Session management + QR code generation
- Audio stream fan-out to listeners

### AI Service (Python)
- Flask - REST API
- Azure Cognitive Services Speech SDK
- STT → Translation → TTS pipeline

### Frontend (React)
- Vite - Build tool
- React Router - Navigation
- WebSocket client - Real-time communication
- MediaRecorder API - Microphone capture

## Prerequisites

- Node.js 18+ and npm
- Python 3.9+
- Azure account with Speech Service resource

## Setup

### 1. Clone and Install

```bash
# Install root dependencies
npm install

# Install all workspace dependencies
cd backend && npm install && cd ..
cd frontend && npm install && cd ..
cd ai-service && pip install -r requirements.txt && cd ..
```

### 2. Azure Setup

1. Go to [Azure Portal](https://portal.azure.com)
2. Create a **Speech Service** resource
3. Copy the **Key** and **Region** (e.g., `eastus`)

### 3. Environment Configuration

Create `.env` files from examples:

```bash
# Backend
cp backend/.env.example backend/.env
# Edit: Set AI_SERVICE_URL if needed

# AI Service
cp ai-service/.env.example ai-service/.env
# Edit: Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION
```

**ai-service/.env:**
```env
PORT=5000
NODE_BACKEND_URL=http://localhost:3000
AZURE_SPEECH_KEY=your_actual_key_here
AZURE_SPEECH_REGION=eastus
```

### 4. Run Development Servers

**Option A: Run all services together (recommended)**
```bash
npm run dev
```

**Option B: Run separately**
```bash
# Terminal 1: Backend
cd backend && npm run dev

# Terminal 2: AI Service
cd ai-service && npm run dev

# Terminal 3: Frontend
cd frontend && npm run dev
```

### 5. Access the App

Open browser: **http://localhost:5173**

## Usage

### As Speaker:
1. Click "Start a Tour"
2. Share the 6-character code or QR code
3. Click "Start Speaking"
4. Speak into your microphone
5. Listeners receive translations in real-time

### As Listener:
1. Click "Join a Tour"
2. Enter session code
3. Select your language
4. Click "Join Session"
5. Hear translated audio + see live subtitles

## Project Structure

```
vavilon_webapp/
├── backend/                 # Node.js server
│   ├── src/
│   │   ├── index.js        # Express + WebSocket setup
│   │   ├── routes/
│   │   │   ├── sessions.js # Session CRUD API
│   │   │   └── broadcast.js # Translation broadcast
│   │   ├── services/
│   │   │   └── sessionService.js # Session management
│   │   └── websocket/
│   │       └── wsHandler.js # WebSocket logic
│   └── package.json
│
├── ai-service/              # Python AI service
│   ├── src/
│   │   ├── app.py          # Flask API
│   │   └── speech_service.py # Azure Speech SDK
│   └── requirements.txt
│
├── frontend/                # React frontend
│   ├── src/
│   │   ├── pages/
│   │   │   ├── LandingPage.jsx
│   │   │   ├── SpeakerPage.jsx
│   │   │   └── ListenerPage.jsx
│   │   ├── App.jsx
│   │   └── main.jsx
│   └── package.json
│
└── package.json             # Root workspace config
```

## API Endpoints

### Backend (Node.js)

**Sessions**
- `POST /api/sessions` - Create new session
- `GET /api/sessions/:idOrCode` - Get session details
- `GET /api/sessions/:id/stats` - Get listener statistics
- `DELETE /api/sessions/:id` - End session

**Broadcasting**
- `POST /api/broadcast` - Broadcast translations to listeners

**WebSocket**
- `ws://localhost:3000/ws` - WebSocket connection
  - `speaker_join` - Speaker joins session
  - `listener_join` - Listener joins session
  - `audio_chunk` - Speaker sends audio
  - `audio` - Listener receives audio
  - `subtitle` - Listener receives subtitles

### AI Service (Python)

- `POST /process-audio` - Process speaker audio (STT + Translation + TTS)
- `POST /start-session` - Initialize translation session
- `POST /end-session` - End translation session
- `GET /health` - Health check

## Supported Languages

- English (en)
- Spanish (es)
- French (fr)
- German (de)
- Italian (it)
- Portuguese (pt)
- Russian (ru)
- Chinese (zh)
- Japanese (ja)
- Arabic (ar)

## Data Flow

1. **Speaker speaks** → Microphone captures audio
2. **Audio chunks** → Sent via WebSocket to Node.js backend
3. **Forward to AI** → Node.js forwards to Python service
4. **STT** → Python: Speech-to-Text (once)
5. **Translation** → Python: Translate to all target languages (once per language)
6. **TTS** → Python: Text-to-Speech for each language (once per language)
7. **Broadcast** → Node.js broadcasts audio + subtitles per language
8. **Listeners receive** → Audio plays + subtitles display

## Production Deployment

### Azure Recommendations:

1. **Backend**: Azure App Service (Node.js)
2. **AI Service**: Azure Container Instance (Python)
3. **Frontend**: Azure Static Web Apps
4. **Database**: Azure Redis Cache (session storage)
5. **Speech**: Azure Cognitive Services (already required)

### Environment Variables (Production):

```env
# Backend
PORT=3000
AI_SERVICE_URL=https://your-ai-service.azurecontainer.io
FRONTEND_URL=https://your-frontend.azurestaticapps.net

# AI Service
PORT=5000
NODE_BACKEND_URL=https://your-backend.azurewebsites.net
AZURE_SPEECH_KEY=<production-key>
AZURE_SPEECH_REGION=<region>
```

### Scaling Considerations:

- **Session Storage**: Replace in-memory Map with Redis
- **WebSocket Scaling**: Use Azure SignalR Service
- **AI Service**: Scale horizontally with container orchestration
- **CDN**: Use Azure CDN for frontend assets

## Known Limitations (MVP)

- **No authentication** - Anyone with code can join
- **No session persistence** - Sessions lost on server restart
- **In-memory storage** - Use Redis for production
- **Single server** - No horizontal scaling yet
- **Basic error handling** - Production needs retry logic
- **No analytics** - Add telemetry for insights

## Future Features (TODO)

### Phase 2:
- [ ] Q&A mode (listener questions)
- [ ] Session recording and playback
- [ ] User accounts and session history
- [ ] Mobile apps (iOS/Android)

### Phase 3:
- [ ] Multi-speaker support
- [ ] Advanced analytics dashboard
- [ ] Custom vocabulary/terminology
- [ ] Offline mode with caching

### Phase 4:
- [ ] Enterprise features (SSO, RBAC)
- [ ] API for third-party integrations
- [ ] White-label customization
- [ ] SLA guarantees

## Troubleshooting

### Microphone not working
- Check browser permissions (Chrome/Edge recommended)
- Ensure HTTPS in production (required for getUserMedia)

### WebSocket connection fails
- Check firewall settings
- Verify ports 3000 and 5173 are open
- Check proxy configuration in vite.config.js

### Azure Speech errors
- Verify AZURE_SPEECH_KEY is correct
- Check AZURE_SPEECH_REGION matches your resource
- Ensure Speech Service quota is not exceeded

### No translations received
- Check Python service is running (port 5000)
- Verify AI_SERVICE_URL in backend .env
- Check backend logs for errors

## Development Tips

- Use Chrome DevTools Network tab to debug WebSocket
- Monitor Python service logs for Azure SDK errors
- Test with 2+ browser windows (speaker + listener)
- Use incognito mode to test multiple listeners

## License

Proprietary - Internal MVP

## Support

For issues or questions, contact the development team.