// API and WebSocket configuration
const config = {
  // For API calls, use relative paths (proxied via staticwebapp.config.json)
  apiUrl: '',
  
  // For WebSocket, must use absolute URL to backend
  wsUrl: import.meta.env.VITE_BACKEND_URL || 'http://localhost:3000',
  
  // Helper to get WebSocket URL
  getWebSocketUrl: () => {
    const backendUrl = config.wsUrl;
    // Convert HTTP/HTTPS to WS/WSS
    const wsProtocol = backendUrl.startsWith('https') ? 'wss:' : 'ws:';
    const urlWithoutProtocol = backendUrl.replace(/^https?:/, '');
    return `${wsProtocol}${urlWithoutProtocol}/ws`;
  }
};

export default config;
