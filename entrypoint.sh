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
# Try to remount with more space; fallback is --disable-dev-shm-usage in browser args.
SHM_SIZE=$(df /dev/shm 2>/dev/null | awk 'NR==2{print $2}' || echo "0")
if [ "${SHM_SIZE:-0}" -lt 1048576 ] 2>/dev/null; then
    echo "=> /dev/shm is small (${SHM_SIZE}KB), Chromium will use /tmp fallback"
    # Create a larger tmpfs for Chromium if possible
    if mount -t tmpfs -o size=1g tmpfs /dev/shm 2>/dev/null; then
        echo "=> Remounted /dev/shm with 1GB"
    fi
fi

# ── Verify Chromium binary exists ──
echo "=> Checking Chromium installation..."
CHROMIUM_BIN=$(find /root/.cache/ms-playwright /root/.cache/ms-patchright -name "chrome" -path "*/chrome-linux/*" 2>/dev/null | head -1 || true)
if [ -n "${CHROMIUM_BIN}" ]; then
    echo "=> Chromium binary: ${CHROMIUM_BIN}"
    if [ -x "${CHROMIUM_BIN}" ]; then
        echo "=> Chromium is executable"
    else
        echo "=> WARNING: Chromium not executable, fixing permissions..."
        chmod +x "${CHROMIUM_BIN}" || true
    fi
    # Check for missing shared libraries
    MISSING=$(ldd "${CHROMIUM_BIN}" 2>/dev/null | grep "not found" || true)
    if [ -n "${MISSING}" ]; then
        echo "=> WARNING: Missing shared libraries:"
        echo "${MISSING}"
    else
        echo "=> All shared libraries present"
    fi
else
    echo "=> WARNING: No Chromium binary found in cache directories"
    echo "=> Cache contents:"
    find /root/.cache/ms-playwright /root/.cache/ms-patchright -type f 2>/dev/null | head -20 || true
fi

# ── Launch the app ──
echo "=> Starting Wala-jey ..."
exec python -u go.py
