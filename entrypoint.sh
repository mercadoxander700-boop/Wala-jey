#!/usr/bin/env bash
set -e

# ── Start Xvfb virtual framebuffer (for Railway / headless Docker) ──
# Chromium needs an X display even in "new" headless mode for some operations.
# If Xvfb is available and no display is already running, start one.
if command -v Xvfb >/dev/null 2>&1; then
    # If DISPLAY is not set, or is set to our default :99
    if [ -z "${DISPLAY}" ] || [ "${DISPLAY}" = ":99" ]; then
        echo "=> Starting Xvfb on :99 ..."
        Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp -ac &
        XVFB_PID=$!
        # Wait briefly for Xvfb to be ready
        sleep 1
        export DISPLAY=:99
        echo "=> Xvfb started (PID: ${XVFB_PID})"
    fi
fi

# ── Ensure /dev/shm is usable ──
# Railway containers have a small /dev/shm which can crash Chromium.
# Create a tmpfs mount if possible (needs --privileged or cap-add).
# Fallback: --disable-dev-shm-usage is already in browser args.
if [ ! -d /dev/shm ] || [ "$(stat -f -c '%T' /dev/shm 2>/dev/null || echo 'unknown')" != "tmpfs" ]; then
    echo "=> Warning: /dev/shm may be too small for Chromium (using --disable-dev-shm-usage fallback)"
fi

# ── Launch the app ──
echo "=> Starting Wala-jey ..."
exec python go.py
