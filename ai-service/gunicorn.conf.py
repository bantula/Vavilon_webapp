# Gunicorn Configuration for Vavilon AI Service
# https://docs.gunicorn.org/en/stable/settings.html

import os
import multiprocessing

# Server Socket
bind = "0.0.0.0:5000"
backlog = 128  # Max pending connections

# Worker Processes
workers = 1  # Single process for shared state (sessions dict, Azure SDK objects)
worker_class = "sync"  # Synchronous workers (Azure SDK is sync, not async)
threads = 4  # 4 threads per worker for concurrent request handling
worker_connections = 100  # Max simultaneous clients (sync worker default)
max_requests = 0  # No worker restart (0 = disable, avoids session loss)
max_requests_jitter = 0
timeout = 60  # Request timeout (allow long TTS synthesis + graceful stop)
graceful_timeout = 30  # Time to finish requests before force-killing worker
keepalive = 5  # Seconds to keep idle connections alive

# Logging
accesslog = "-"  # Log to stdout (Azure Container Instance captures this)
errorlog = "-"   # Log to stderr
loglevel = "info"  # Options: debug, info, warning, error, critical
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'
# %(D)s = request duration in microseconds (monitor slow requests)

# Process Naming
proc_name = "vavilon-ai"

# Server Mechanics
daemon = False  # Run in foreground (required for containers)
pidfile = None  # No PID file needed
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (not used, backend handles TLS termination)
keyfile = None
certfile = None
