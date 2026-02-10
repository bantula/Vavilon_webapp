const express = require('express');
const http = require('http');
const cors = require('cors');
const sessionRoutes = require('./routes/sessions');
const broadcastRoutes = require('./routes/broadcast');
const { setupWebSocket } = require('./websocket/wsHandler');

const app = express();
const server = http.createServer(app);

// Middleware
app.use(cors());
app.use(express.json());

// Routes
app.use('/api/sessions', sessionRoutes);
app.use('/api/broadcast', broadcastRoutes);

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