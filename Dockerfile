FROM python:3.12-slim

# Install Chromium dependencies + Xvfb for virtual display + fluxbox window manager
# + fonts + debugging tools.
# fluxbox is needed because some Chromium operations (and Turnstile's widget rendering)
# expect a window manager to be running even on a virtual display.
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
    fluxbox \
    x11-utils \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install bundled Chromium for Patchright
RUN patchright install chromium

# Verify Chromium was installed correctly (log but don't fail the build)
RUN python -c "import glob, os; home = os.path.expanduser('~'); bins = glob.glob(os.path.join(home, '.cache', 'ms-playwright', 'chromium-*', 'chrome-linux', 'chrome')) + glob.glob(os.path.join(home, '.cache', 'ms-patchright', 'chromium-*', 'chrome-linux', 'chrome')); print('Chromium binaries found:', bins); [os.chmod(b, 0o755) for b in bins if b]; print('Done' if bins else 'WARNING: No chromium binary found')"

COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

COPY . .

# Railway sets PORT env var; default to 5000
ENV PORT=5000

# Xvfb virtual display — the browser runs in HEADED mode (headless=False)
# because Cloudflare Turnstile detects and blocks headless browsers.
# Xvfb provides a virtual display so headed mode works in Docker.
# Do NOT set HEADLESS_MODE=1 — that would force headless=True which breaks Turnstile.
ENV DISPLAY=:99

# Ensure Python output is unbuffered so logs appear immediately
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["./entrypoint.sh"]
