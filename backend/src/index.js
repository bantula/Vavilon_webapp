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
const { setupWebSocket } = require('./websocket/wsHandler');

const app = express();
const server = http.createServer(app);

// Middleware
app.use(cors({
  origin: [
    'http://localhost:5173',
    'https://green-pond-05766a403.1.azurestaticapps.net',
    'https://vavilonapp.rs',
    'https://www.vavilonapp.rs'
  ],
  credentials: true
}));
app.use(express.json({ limit: '5mb' }));

// Routes
app.use('/api/sessions', sessionRoutes);
app.use('/api/broadcast', broadcastRoutes);
app.use('/api/events', eventsRoutes);

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'vavilon-backend' });
});

// Setup WebSocket
setupWebSocket(server);

const PORT = process.env.PORT || 3000;

server.listen(PORT, () => {
  console.log(`✓ Vavilon backend running on port ${PORT}`);
  console.log(`✓ WebSocket ready for connections`);
  console.log(`✓ AI Service URL: ${process.env.AI_SERVICE_URL || 'http://localhost:5000'}`);
});

module.exports = { app, server };