#!/usr/bin/env bash
set -e

# ── Start Xvfb virtual framebuffer (for Railway / headless Docker) ──
# Chromium needs an X display even in "new" headless mode for some operations.
# If Xvfb is available and no display is already running, start one.
if command -v Xvfb >/dev/null 2>&1; then
    if [ -z "${DISPLAY}" ] || [ "${DISPLAY}" = ":99" ]; then
        echo "=> Starting Xvfb on :99 ..."
        Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp -ac &
        XVFB_PID=$!
        sleep 1
        export DISPLAY=:99
        echo "=> Xvfb started (PID: ${XVFB_PID})"
    fi
fi

# ── Verify Chromium binary exists ──
echo "=> Checking Chromium installation..."
CHROMIUM_BIN=$(find /root/.cache/ms-playwright /root/.cache/ms-patchright -name "chrome" -path "*/chrome-linux/*" 2>/dev/null | head -1 || true)
if [ -n "${CHROMIUM_BIN}" ]; then
    echo "=> Chromium binary: ${CHROMIUM_BIN}"
    if [ ! -x "${CHROMIUM_BIN}" ]; then
        echo "=> WARNING: Chromium not executable, fixing permissions..."
        chmod +x "${CHROMIUM_BIN}" || true
    fi
else
    echo "=> WARNING: No Chromium binary found in cache directories"
    echo "=> Cache contents:"
    find /root/.cache/ms-playwright /root/.cache/ms-patchright -type f 2>/dev/null | head -20 || true
fi

# ── Launch the app ──
echo "=> Starting Wala-jey ..."
exec python -u go.py
