FROM python:3.9-bullseye

WORKDIR /app

# Azure Speech SDK 1.36.0 requires libssl1.1 (available on Bullseye/Debian 11,
# NOT on Bookworm/Debian 12 which only ships libssl3).
# Also needs libasound2 for audio output and ca-certificates for HTTPS.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libasound2 \
    libssl1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 5000

CMD ["python", "src/app.py"]