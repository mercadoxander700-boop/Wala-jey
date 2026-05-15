FROM python:3.12-slim

# Install Chromium dependencies + Xvfb for virtual display
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    fonts-liberation \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install bundled Chromium for Patchright
RUN patchright install chromium

COPY . .

# Railway sets PORT env var; default to 5000
ENV PORT=5000

# Start Xvfb virtual framebuffer on display :99, then launch the app.
# The DISPLAY env var tells Chromium where to find the X server;
# if Xvfb isn't running (e.g. local dev), the app falls back to headless mode.
ENV DISPLAY=:99
CMD Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &\n    sleep 1 &&\n    python go.py
