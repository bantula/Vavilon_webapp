// API and WebSocket configuration
const config = {
  // Backend URL - use environment variable or default to localhost
  backendUrl: import.meta.env.VITE_BACKEND_URL || 'http://localhost:3000',
  
  // API base URL (same as backend)
  get apiUrl() {
    return this.backendUrl;
  },
  
  // Helper to get WebSocket URL
  getWebSocketUrl: () => {
    const backendUrl = config.backendUrl;
    // Convert HTTP/HTTPS to WS/WSS
    const wsProtocol = backendUrl.startsWith('https') ? 'wss:' : 'ws:';
    const urlWithoutProtocol = backendUrl.replace(/^https?:/, '');
    return `${wsProtocol}${urlWithoutProtocol}/ws`;
  }
};

export default config;
