// Initialize Application Insights first
const appInsights = require('applicationinsights');
if (process.env.APPINSIGHTS_INSTRUMENTATIONKEY) {
  appInsights.setup(process.env.APPINSIGHTS_INSTRUMENTATIONKEY)
    .setAutoDependencyCorrelation(true)
    .setAutoCollectRequests(true)
    .setAutoCollectPerformance(true)
    .setAutoCollectExceptions(true)
    .start();
  console.log('✓ Application Insights enabled');
}

const express = require('express');
const http = require('http');
const cors = require('cors');
const sessionRoutes = require('./routes/sessions');
const broadcastRoutes = require('./routes/broadcast');
const eventsRoutes = require('./routes/events');
const authRoutes = require('./routes/auth');
const adminRoutes = require('./routes/admin');
const paytenRoutes = require('./routes/payten');
const leadsRoutes  = require('./routes/leads');
const { setupWebSocket, connections } = require('./websocket/wsHandler');
const { startWatchdog } = require('./services/watchdogService');
const { execFile } = require('child_process');
const path = require('path');

const app = express();
const server = http.createServer(app);

// Middleware
app.use(cors({
  origin: [
    'http://localhost:3000',
    'http://localhost:3001',
    'http://localhost:5173',
    'https://green-pond-05766a403.1.azurestaticapps.net',
    'https://delightful-beach-0f44c2303.azurestaticapps.net',
    'https://vavilonapp.rs',
    'https://www.vavilonapp.rs',
    'https://vavilonsolutions.rs',
    'https://www.vavilonsolutions.rs',
  ],
  credentials: true
}));
app.use(express.json({ limit: '5mb' }));

// Routes
app.use('/api/sessions', sessionRoutes);
app.use('/api/broadcast', broadcastRoutes);
app.use('/api/events', eventsRoutes);
app.use('/api/auth', authRoutes);
app.use('/api/admin', adminRoutes);
app.use('/api', paytenRoutes);
app.use('/api', leadsRoutes);

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'vavilon-backend' });
});

// Auto-import guides from guides.csv into Redis on every startup
const importScript = path.join(__dirname, '..', 'scripts', 'import-guides.js');
execFile(process.execPath, [importScript], { env: process.env }, (err, stdout, stderr) => {
  if (err) {
    console.error('Guide import failed:', stderr || err.message);
  } else {
    console.log('✓ Guides imported from CSV\n' + stdout.trim());
  }
});

// Setup WebSocket
setupWebSocket(server);
startWatchdog(connections);

const PORT = process.env.PORT || 3000;

server.listen(PORT, () => {
  console.log(`✓ Vavilon backend running on port ${PORT}`);
  console.log(`✓ WebSocket ready for connections`);
  console.log(`✓ AI Service URL: ${process.env.AI_SERVICE_URL || 'http://localhost:5000'}`);
});

module.exports = { app, server };