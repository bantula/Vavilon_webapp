/**
 * Shared Redis client.
 *
 * Previously sessionService, authService and leads each created their own
 * client (3 TCP connections on every boot). They now share this one, which
 * matters on Azure Cache basic tiers where the connection budget is small.
 *
 * Connection is resilient (bounded reconnect) and TLS is enabled automatically
 * when REDIS_URL points at Azure Cache (port 6380).
 */
const redis = require('redis');

const client = redis.createClient({
  url: process.env.REDIS_URL ? `redis://${process.env.REDIS_URL}:6380` : 'redis://localhost:6379',
  password: process.env.REDIS_PASSWORD,
  socket: {
    tls: !!process.env.REDIS_URL,
    rejectUnauthorized: false,
    reconnectStrategy: (retries) => {
      if (retries > 10) {
        console.error('Redis reconnect failed after 10 attempts');
        return new Error('Redis reconnect limit exceeded');
      }
      const delay = Math.min(retries * 50, 2000);
      console.log(`Redis reconnecting in ${delay}ms (attempt ${retries})`);
      return delay;
    }
  }
});

client.on('error', (err) => console.error('Redis client error:', err.message));
client.on('reconnecting', () => console.warn('Redis client reconnecting...'));
client.on('ready', () => console.log('✓ Redis client ready'));
client.on('connect', () => console.log('✓ Connected to Redis'));

// Connect once at process startup. Callers can `await ready` if they need the
// connection to be up before their first command, but redis@4 also queues
// commands issued before connect resolves.
const ready = (async () => {
  try {
    await client.connect();
  } catch (err) {
    console.error('Failed to connect to Redis:', err.message);
  }
})();

/**
 * Collect all keys matching a pattern using SCAN (non-blocking) instead of
 * KEYS (which blocks the Redis server on large keyspaces).
 */
async function scanKeys(pattern) {
  const keys = [];
  for await (const key of client.scanIterator({ MATCH: pattern, COUNT: 100 })) {
    // redis@4 may yield either a string or an array of strings per iteration.
    if (Array.isArray(key)) keys.push(...key);
    else keys.push(key);
  }
  return keys;
}

module.exports = { client, ready, scanKeys };
