#!/usr/bin/env bash
set -e

# ── Start Xvfb virtual framebuffer (for Railway / Docker) ──
# Chromium needs an X display for headed mode (headless=False).
# We use Xvfb to provide a virtual display + fluxbox as window manager.
# This is CRITICAL for Cloudflare Turnstile — it detects headless browsers
# and blocks the CAPTCHA. Headed mode with Xvfb is the only working approach.
if command -v Xvfb >/dev/null 2>&1; then
    if [ -z "${DISPLAY}" ] || [ "${DISPLAY}" = ":99" ]; then
        echo "=> Starting Xvfb on :99 (1920x1080x24) ..."
        Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp -ac &
        XVFB_PID=$!
        export DISPLAY=:99

        # Wait for Xvfb to be ready
        echo "=> Waiting for Xvfb to be ready..."
        for i in $(seq 1 10); do
            if xdpyinfo -display :99 >/dev/null 2>&1; then
                echo "=> Xvfb is ready (PID: ${XVFB_PID})"
                break
            fi
            sleep 0.5
        done

        # Start fluxbox window manager — needed for proper window
        # rendering and focus management in headed Chromium on Xvfb.
        # Some Turnstile widget interactions require a proper WM.
        if command -v fluxbox >/dev/null 2>&1; then
            echo "=> Starting fluxbox window manager..."
            fluxbox -display :99 &
            FLUXBOX_PID=$!
            sleep 1
            echo "=> Fluxbox started (PID: ${FLUXBOX_PID})"
        else
            echo "=> WARNING: fluxbox not found, running without window manager"
        fi
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

# ── Print environment info ──
echo "=> Environment: DISPLAY=${DISPLAY}, HEADLESS_MODE=${HEADLESS_MODE:-not set}"
echo "=> Browser will run in HEADED mode with Xvfb (Turnstile requires headed mode)"

# ── Launch the app ──
echo "=> Starting Wala-jey ..."
exec python -u go.py
