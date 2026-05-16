"""
Wala-jey — GoLogin Account Creator with Cloudflare Turnstile CAPTCHA Solver

Architecture:
  - Single process: HTTP API (for Railway) + in-process Turnstile solver
  - proxy.txt provides browser proxies for the CAPTCHA solver
  - Harvested GoLogin proxies are written to proxies.txt and served at /proxies
  - No subprocess isolation — the solver runs in the main asyncio event loop
"""

import asyncio
import logging
import os
import random
import signal
import sys
import threading
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Configuration ──────────────────────────────────────────────────────────────
GOLOGIN_API = "https://api.gologin.com"
SITE_URL = "https://captcha.gologin.com"
SITE_KEY = "0x4AAAAAAAQn-wN8S1gi-nJa"

FINGERPRINT = {
    "fontsHash": "a1b2c3d4e5f6g7h8",
    "canvasHash": "1234567890",
    "canvasAndFontsHash": "x9y8z7w6v5u4t3s2",
    "os": "win",
    "osSpec": "win11",
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

PROXY_FILE = "proxy.txt"
PROXIES_OUTPUT = "proxies.txt"
SOLVER_PROXY_FILE = "_solver_proxies.txt"
ACCOUNTS_FILE = "accounts.txt"
API_PORT = int(os.environ.get("PORT", 5000))
DELAY_BETWEEN_ACCOUNTS = 5
MAX_CONSECUTIVE_FAILURES = 10

# CRITICAL: Cloudflare Turnstile detects headless browsers and blocks the CAPTCHA.
# When running in Docker/Railway, we use Xvfb to provide a virtual display so the
# browser can run in headed mode (headless=False). This is the only reliable way
# to solve Turnstile — headless=True always fails.
# Set HEADLESS_MODE=1 ONLY if you have NO virtual display at all (rare / debugging).
HEADLESS = False
if os.environ.get("HEADLESS_MODE", "").lower() in ("1", "true", "yes"):
    HEADLESS = True

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("wala-jey")

# ── Solver state (shared between main and solver) ──────────────────────────────
solver_ready = False
solver_server = None
solver_error = None


# ══════════════════════════════════════════════════════════════════════════════
#  Proxy helpers
# ══════════════════════════════════════════════════════════════════════════════

def gen_str(n: int = 8) -> str:
    return "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=n))


def load_harvested_proxies() -> list[str]:
    """Load proxies from the harvested proxies.txt output file."""
    try:
        if not os.path.exists(PROXIES_OUTPUT):
            return []
        with open(PROXIES_OUTPUT, "r") as fh:
            lines = fh.read().strip().splitlines()
        return [l.strip() for l in lines if l.strip()]
    except Exception:
        return []


def pick_request_proxy(harvested: list[str]) -> dict | None:
    """Return a random requests-compatible proxy dict from harvested proxies."""
    if not harvested:
        return None
    raw = random.choice(harvested)
    parts = raw.split(":")
    try:
        if len(parts) == 4:
            user, pwd, host, port = parts
            url = f"http://{user}:{pwd}@{host}:{port}"
        elif len(parts) == 2:
            url = f"http://{parts[0]}:{parts[1]}"
        else:
            url = f"http://{raw}"
        return {"http": url, "https": url}
    except Exception:
        return None


def load_proxies_from_file(filepath: str) -> list[str]:
    """Load proxies from a file.  Supports:
    - proxy.txt format:  user:pass:host:port  → converted to host:port@user:pass
    - solver format:     host:port@user:pass  → kept as-is
    - API URL:           http://…             → fetched from endpoint
    """
    try:
        if not os.path.exists(filepath):
            return []

        with open(filepath, "r") as fh:
            content = fh.read().strip()

        if not content:
            return []

        first_line = content.splitlines()[0].strip()

        # If the first line is an API URL, fetch proxies from it
        if first_line.startswith("http://") or first_line.startswith("https://"):
            print(f"=> Fetching proxies from API: {first_line}")
            try:
                resp = requests.get(first_line, timeout=30, verify=False)
                if resp.status_code != 200:
                    print(f"=> Proxy API returned HTTP {resp.status_code}")
                    return []
                try:
                    data = resp.json()
                    if isinstance(data, list):
                        raw = [str(p) for p in data if p]
                    elif isinstance(data, dict) and "proxies" in data:
                        raw = [str(p) for p in data["proxies"] if p]
                    else:
                        raw = resp.text.strip().splitlines()
                except ValueError:
                    raw = resp.text.strip().splitlines()
                print(f"=> Fetched {len(raw)} proxies from API")
                return _convert_proxy_lines(raw)
            except Exception as exc:
                print(f"=> Error fetching proxies from API: {exc}")
                return []

        lines = [l.strip() for l in content.splitlines() if l.strip()]
        return _convert_proxy_lines(lines)
    except Exception as exc:
        print(f"=> Error loading proxies from {filepath}: {exc}")
        return []


def _convert_proxy_lines(lines: list[str]) -> list[str]:
    """Convert proxy lines to the solver's expected format: host:port@user:pass

    Input formats accepted:
      - user:pass:host:port  (GoLogin format from proxy.txt)
      - host:port@user:pass  (solver format — kept as-is)
      - host:port            (no auth)
    """
    converted = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if "@" in line:
            # Already in solver format: host:port@user:pass
            converted.append(line)
        else:
            parts = line.split(":")
            if len(parts) == 4:
                # user:pass:host:port → host:port@user:pass
                user, pwd, host, port = parts
                converted.append(f"{host}:{port}@{user}:{pwd}")
            elif len(parts) == 2:
                # host:port (no auth)
                converted.append(line)
            else:
                # Unknown format — pass through
                converted.append(line)
    return converted


def build_solver_proxy_file() -> str:
    """Create the solver proxy file from proxy.txt, converting formats."""
    proxies = load_proxies_from_file(PROXY_FILE)
    with open(SOLVER_PROXY_FILE, "w") as fh:
        for p in proxies:
            fh.write(p + "\n")
    print(f"=> Wrote {len(proxies)} proxies to {SOLVER_PROXY_FILE}")
    return SOLVER_PROXY_FILE


def sync_harvested_to_solver():
    """Append harvested GoLogin proxies to the solver proxy file.

    Converts user:pass:host:port → host:port@user:pass format.
    """
    try:
        harvested = load_harvested_proxies()
        if not harvested:
            return

        # Load existing solver proxies
        existing = set()
        if os.path.exists(SOLVER_PROXY_FILE):
            with open(SOLVER_PROXY_FILE, "r") as fh:
                existing = {l.strip() for l in fh if l.strip()}

        converted = []
        for line in harvested:
            parts = line.split(":")
            if len(parts) == 4:
                user, pwd, host, port = parts
                proxy_str = f"{host}:{port}@{user}:{pwd}"
            else:
                proxy_str = line
            if proxy_str not in existing:
                converted.append(proxy_str)
                existing.add(proxy_str)

        if not converted:
            return

        # Append new proxies
        with open(SOLVER_PROXY_FILE, "a") as fh:
            for p in converted:
                fh.write(p + "\n")
        print(f"=> Synced {len(converted)} new harvested proxies to solver")
    except Exception as exc:
        print(f"=> Error syncing proxies to solver: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP API Server
# ══════════════════════════════════════════════════════════════════════════════

class _ProxyAPIHandler(BaseHTTPRequestHandler):
    """Serves harvested proxies and accounts as raw text."""

    def do_GET(self):
        try:
            if self.path in ("/proxies", "/proxies/"):
                content = ""
                if os.path.exists(PROXIES_OUTPUT):
                    with open(PROXIES_OUTPUT, "r") as fh:
                        content = fh.read()
                self._text(200, content)

            elif self.path in ("/accounts", "/accounts/"):
                content = ""
                if os.path.exists(ACCOUNTS_FILE):
                    with open(ACCOUNTS_FILE, "r") as fh:
                        content = fh.read()
                self._text(200, content)

            elif self.path in ("/status", "/status/"):
                status = "initializing"
                if solver_ready:
                    status = "running"
                elif solver_error:
                    status = f"error: {solver_error}"
                self._text(200, status)

            elif self.path in ("/health", "/health/"):
                self._json(200, {
                    "status": "ok" if solver_ready else "initializing",
                    "solver": "ready" if solver_ready else "starting",
                    "accounts": _count_lines(ACCOUNTS_FILE),
                    "proxies": _count_lines(PROXIES_OUTPUT),
                })

            else:
                self._text(200,
                    "Wala-jey — GoLogin Account Creator\n"
                    "  GET /proxies   - harvested proxies (user:pass:host:port)\n"
                    "  GET /accounts  - created accounts (email:pass)\n"
                    "  GET /status    - solver status\n"
                    "  GET /health    - JSON health check\n"
                )
        except Exception:
            try:
                self._text(500, "Internal error")
            except Exception:
                pass

    def _text(self, code: int, body: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode())

    def _json(self, code: int, data: dict):
        import json
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass  # suppress noisy request logs


def _count_lines(filepath: str) -> int:
    try:
        if not os.path.exists(filepath):
            return 0
        with open(filepath, "r") as fh:
            return sum(1 for l in fh if l.strip())
    except Exception:
        return 0


def _start_proxy_api():
    """Start the proxy API server. Retries on failure."""
    while True:
        try:
            server = HTTPServer(("0.0.0.0", API_PORT), _ProxyAPIHandler)
            print(f"=> Proxy API server running on http://0.0.0.0:{API_PORT}")
            server.serve_forever()
        except OSError as exc:
            if "Address already in use" in str(exc):
                print(f"=> Port {API_PORT} already in use, retrying in 5s...")
                time.sleep(5)
            else:
                print(f"=> Proxy API error: {exc}")
                time.sleep(5)
        except Exception as exc:
            print(f"=> Proxy API crashed: {exc}, restarting in 5s...")
            time.sleep(5)


# ══════════════════════════════════════════════════════════════════════════════
#  In-process Turnstile Solver
# ══════════════════════════════════════════════════════════════════════════════

async def init_solver(proxy_file: str):
    """Initialize the Turnstile solver server in-process.

    Uses run_task() so the Quart server runs as a non-blocking asyncio task.
    The browser pool init happens in the background after the server is up.
    """
    global solver_ready, solver_server, solver_error

    try:
        from turnstile_solver.proxy_provider import ProxyProvider
        from turnstile_solver.turnstile_solver_server import TurnstileSolverServer
        from turnstile_solver.solver import TurnstileSolver
        import turnstile_solver.constants as c

        # ── Load proxies for the solver ──
        proxy_provider = ProxyProvider(proxy_file)
        proxy_provider.load()
        print(f"=> Solver proxy provider: {len(proxy_provider.proxies)} proxies loaded")

        # ── Build solver + server manually ──
        # These extra args are passed ON TOP of the BROWSER_ARGS already defined
        # in solver.py. The solver.py BROWSER_ARGS already include the full set
        # of Docker/Xvfb flags (including --disable-gpu, --disable-software-rasterizer,
        # --use-gl=swiftshader, --enable-webgl, etc.) that are proven to work.
        #
        # We only add the --enable-features flag here since solver.py's BROWSER_ARGS
        # already has a version but we want to make sure these specific ones are present.
        extra_args = [
            "--enable-features=SharedArrayBuffer,TrustTokens,PrivateNetworkAccessChecksBypassingPermissionPolicy",
        ]

        solver_server = TurnstileSolverServer(
            host="127.0.0.1",
            port=8088,
            secret="jWRN7DH6",
            disable_access_logs=True,
            turnstile_solver=None,
            on_shutting_down=None,
            console=None,
            log_level=logging.INFO,
            ignore_food_events=True,
        )

        solver = TurnstileSolver(
            server=solver_server,
            page_load_timeout=60,
            browser_position=(2000, 2000),
            browser_executable_path=None,
            browser="chromium",
            reload_page_on_captcha_overrun_event=False,
            max_attempts=5,
            attempt_timeout=60,
            headless=False,  # MUST be False — Turnstile blocks headless browsers. Xvfb provides virtual display.
            console=None,
            log_level=logging.INFO,
            proxy=None,
            browser_args=extra_args,
        )
        solver_server.solver = solver

        # ── Register before_serving / after_serving hooks ──
        # These are normally registered inside solver_server.run(), but since
        # we're calling solver_server.app.run_task() directly, we need to
        # register them ourselves.
        async def _before_serving():
            solver_server.down = False
            print("=> [solver] Server up and running (before_serving hook)")

        async def _after_serving():
            solver_server.down = True
            print("=> [solver] Server is down (after_serving hook)")

        solver_server.app.before_serving(_before_serving)
        solver_server.app.after_serving(_after_serving)

        # ── Start HTTP server as a non-blocking task ──
        # app.run_task() starts the Quart/Hypercorn server. It handles the
        # ASGI lifespan protocol, which fires Quart's before_serving hook
        # (setting solver_server.down = False) and after_serving hook.
        #
        # Since we await this inside an asyncio.create_task(), the event loop
        # can still run other tasks (pool init, account creation) while the
        # server runs in the background.
        await solver_server.app.run_task(
            host=solver_server.host,
            port=solver_server.port,
            debug=False,
        )
        # This point is reached when the server finishes — mark it
        solver_error = "Solver server exited unexpectedly"
        print("=> [solver] Server exited unexpectedly!")

    except Exception as exc:
        solver_error = str(exc)
        print(f"=> [solver] Fatal error: {exc}")
        traceback.print_exc()


async def _init_browser_pool(proxy_file: str):
    """Initialize the browser context pool (with retries).

    This is called after the solver HTTP server is up. If the pool init
    fails (e.g. Chromium crash, proxy issues), it retries every 30 seconds.
    """
    global solver_ready, solver_server, solver_error

    from turnstile_solver.proxy_provider import ProxyProvider
    import turnstile_solver.constants as c

    # Wait for solver server to be up
    for i in range(120):
        if solver_server and not solver_server.down:
            break
        await asyncio.sleep(1)
    else:
        print("=> [pool] Solver server never came up — cannot init pool")
        solver_error = "Solver server never came up"
        return

    print("=> [pool] Solver server is up, initializing browser context pool...")

    attempt = 0
    while True:
        attempt += 1
        try:
            proxy_provider = ProxyProvider(proxy_file)
            proxy_provider.load()
            print(f"=> [pool] Attempt {attempt}: {len(proxy_provider.proxies)} proxies available")

            await solver_server.create_browser_context_pool(
                max_contexts=min(c.MAX_CONTEXTS, 3),  # limit for Railway
                max_pages_per_context=1,
                single_instance=True,
                proxy_provider=proxy_provider,
            )
            solver_ready = True
            solver_error = None
            print(f"=> [pool] Browser context pool ready — solver is online! (attempt {attempt})")
            return
        except Exception as exc:
            solver_error = str(exc)
            print(f"=> [pool] Attempt {attempt} failed: {exc}")
            wait = min(30 * attempt, 120)
            print(f"=> [pool] Retrying in {wait}s...")
            await asyncio.sleep(wait)


async def solve_captcha_via_solver() -> str | None:
    """Solve a Turnstile captcha using the in-process solver.

    Calls the solver directly (not via HTTP) to avoid body parsing issues
    with GET requests and reduce overhead.
    """
    global solver_ready, solver_server, solver_error

    if not solver_server:
        print("=> [solver] Server not started yet")
        return None

    if not solver_ready:
        # Pool not ready — check if we can still try
        if solver_server.browser_context_pool is None:
            print("=> [solver] Browser pool not initialized, skipping captcha solve")
            return None

    # Call the solver directly in-process
    print("=> Solving captcha...")
    try:
        # Get a page from the pool
        async with solver_server._lock:
            page_pool = await solver_server.browser_context_pool.get()
            page = await page_pool.get()

        try:
            # Solve the captcha
            result = await solver_server.solver.solve(
                site_url=SITE_URL,
                site_key=SITE_KEY,
                page=page,
                about_blank_on_finish=True,
            )

            if not result:
                print(f"=> Captcha failed: {solver_server.solver.error}")
                return None

            token = result.token
            elapsed = str(result.elapsed.total_seconds())
            print(f"=> Captcha solved in {elapsed}s: {token[:30]}...")
            return token

        finally:
            # Put the page back in the pool
            await solver_server.browser_context_pool.put_back(page_pool)
            await page_pool.put_back(page)

    except Exception as exc:
        print(f"=> Captcha error: {exc}")
        traceback.print_exc()
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  GoLogin API
# ══════════════════════════════════════════════════════════════════════════════

def get_proxies_from_gologin(bearer: str, req_proxy: dict | None = None) -> bool:
    """Fetch proxy list from GoLogin and write to proxies.txt."""
    print("=> Fetching proxies from GoLogin...")
    headers = {
        "accept": "*/*",
        "authorization": f"Bearer {bearer}",
        "gologin-meta-header": f"site-{FINGERPRINT['os']}-10.0",
        "user-agent": UA,
    }
    try:
        resp = requests.get(
            f"{GOLOGIN_API}/proxy/v2?page=1",
            headers=headers,
            timeout=30,
            verify=False,
            proxies=req_proxy,
        )
        if resp.status_code != 200:
            print(f"=> Proxy fetch failed [{resp.status_code}]")
            return False
        prox_list = resp.json().get("proxies", [])
        if not prox_list:
            print("=> No proxies returned from GoLogin")
            return False
        new_count = 0
        with open(PROXIES_OUTPUT, "a") as fh:
            for p in prox_list:
                if all(p.get(k) for k in ("username", "password", "host", "port")):
                    fh.write(f"{p['username']}:{p['password']}:{p['host']}:{p['port']}\n")
                    new_count += 1
        print(f"=> Saved {new_count} proxies to {PROXIES_OUTPUT}")
        return new_count > 0
    except Exception as exc:
        print(f"=> Proxy fetch error: {exc}")
        return False


def create_account(captcha_token: str, req_proxy: dict | None = None) -> bool:
    """Register a GoLogin account and fetch its proxies."""
    print("=> Creating account...")
    if req_proxy:
        proxy_display = list(req_proxy.values())[0]
        print(f"   (using proxy: {proxy_display[:50]}...)")
    email = f"user_{gen_str()}@ixcyon.top"
    pwd = f"tg@ixcynigga{random.randint(1000, 9999)}"
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "gologin-meta-header": f"site-{FINGERPRINT['os']}-10.0",
        "origin": "https://app.gologin.com",
        "referer": "https://app.gologin.com/",
        "user-agent": UA,
    }
    body = {
        "email": email,
        "password": pwd,
        "passwordConfirm": pwd,
        "captchaToken": captcha_token,
        "fromApp": False,
        "canvasAndFontsHash": FINGERPRINT["canvasAndFontsHash"],
        "fontsHash": FINGERPRINT["fontsHash"],
        "canvasHash": FINGERPRINT["canvasHash"],
        "userOs": FINGERPRINT["os"],
        "osSpec": FINGERPRINT["osSpec"],
        "resolution": "1920x1080",
    }
    try:
        resp = requests.post(
            f"{GOLOGIN_API}/user",
            params={"free-plan": "true", "registerAs": "workspaces"},
            headers=headers,
            json=body,
            timeout=30,
            verify=False,
            proxies=req_proxy,
        )
        if resp.status_code in (200, 201):
            print(f"=> Account created: {email}")
            bearer = resp.json().get("token")
            with open(ACCOUNTS_FILE, "a") as fh:
                fh.write(f"{email}:{pwd}\n")
            print(f"=> Saved to {ACCOUNTS_FILE}")
            time.sleep(0.5)
            if bearer:
                get_proxies_from_gologin(bearer, req_proxy=req_proxy)
            return True
        print(f"=> Account creation failed [{resp.status_code}]: {resp.text[:300]}")
        return False
    except requests.Timeout:
        print("=> Account creation timed out")
        return False
    except requests.ConnectionError:
        print("=> Account creation error: connection failed")
        return False
    except Exception as exc:
        print(f"=> Account creation error: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  Main Application
# ══════════════════════════════════════════════════════════════════════════════

async def app_main():
    """Main application loop — runs solver + account creation in-process."""
    global solver_ready

    print("=" * 60)
    print("  Wala-jey — GoLogin Account Creator + Proxy Harvester")
    print("=" * 60)
    print(f"  Headless: {HEADLESS}")
    print(f"  API Port: {API_PORT}")
    print(f"  Proxy file: {PROXY_FILE}")
    print("=" * 60)

    # ── Build solver proxy file from proxy.txt ──
    proxy_file = build_solver_proxy_file()

    # ── Start HTTP API server in background thread ──
    api_thread = threading.Thread(target=_start_proxy_api, daemon=True)
    api_thread.start()
    # Give it a moment to bind the port
    await asyncio.sleep(0.5)

    # ── Start the Turnstile solver in-process ──
    # Use run_task() so the Quart server runs as a non-blocking asyncio task.
    # This returns immediately — the server runs in the background.
    solver_task = asyncio.create_task(init_solver(proxy_file))

    # ── Initialize browser pool in a separate background task ──
    # This retries automatically if the first attempt fails.
    pool_task = asyncio.create_task(_init_browser_pool(proxy_file))

    # Wait for the solver to be ready (with progress logging)
    print("=> Waiting for solver to initialize...")
    wait_start = time.time()
    while not solver_ready:
        elapsed = int(time.time() - wait_start)
        if elapsed > 0 and elapsed % 15 == 0:
            print(f"=> Still waiting for solver... ({elapsed}s) pool_error={solver_error}")

        # Check if pool task finished (success or fatal failure)
        if pool_task.done():
            try:
                pool_task.result()
            except Exception as exc:
                print(f"=> Pool init task failed: {exc}")
                break

        # Check if solver task died
        if solver_task.done():
            try:
                solver_task.result()
            except Exception as exc:
                print(f"=> Solver task died: {exc}")
                break

        if solver_error and elapsed > 60:
            print(f"=> Solver has errors after 60s: {solver_error}")
            print("=> Continuing to retry pool init in background...")
            break

        await asyncio.sleep(2)

    if solver_ready:
        print("=> Solver is ready! Starting account creation loop...")
    else:
        print("=> Solver not yet ready, but will keep trying in background...")
        print("=> Starting account creation loop — will attempt captcha solves as the solver comes online")

    # ── Continuous account creation loop ──
    created = 0
    failed = 0
    consecutive_failures = 0

    print("=" * 60)
    print("=> Starting continuous account creation + proxy harvesting")
    print(f"=> Proxy API: http://0.0.0.0:{API_PORT}/proxies")
    print(f"=> Health:    http://0.0.0.0:{API_PORT}/health")
    print("=> Press Ctrl+C to stop")
    print("=" * 60)

    try:
        while True:
            cycle = created + failed + 1
            harvested = load_harvested_proxies()
            print(f"\n--- Cycle {cycle} (created: {created}, failed: {failed}, proxies: {len(harvested)}) ---")

            # Use harvested proxies for API requests (if available)
            req_proxy = pick_request_proxy(harvested)
            if req_proxy:
                print(f"=> Using harvested proxy for API calls ({len(harvested)} available)")

            # Solve captcha
            try:
                token = await solve_captcha_via_solver()
            except Exception as exc:
                print(f"=> Unexpected captcha error: {exc}")
                token = None

            if not token:
                consecutive_failures += 1
                failed += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"=> {consecutive_failures} failures in a row, waiting longer...")
                    delay = 60
                else:
                    delay = min(DELAY_BETWEEN_ACCOUNTS * (1 + consecutive_failures), 60)
                print(f"=> Captcha failed ({consecutive_failures}x), retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue

            # Reset failure counter on successful captcha
            consecutive_failures = 0

            # Create account with proxy rotation
            try:
                if create_account(token, req_proxy=req_proxy):
                    created += 1
                    print(f"=> Total accounts created: {created}")
                    # Feed harvested proxies back to the solver browser
                    sync_harvested_to_solver()
                else:
                    failed += 1
            except Exception as exc:
                print(f"=> Unexpected account creation error: {exc}")
                failed += 1

            # Brief delay between cycles
            print(f"=> Waiting {DELAY_BETWEEN_ACCOUNTS}s before next cycle...")
            await asyncio.sleep(DELAY_BETWEEN_ACCOUNTS)

    except asyncio.CancelledError:
        print("\n=> Shutting down...")
    except Exception as exc:
        print(f"\n=> Fatal error in main loop: {exc}")
        traceback.print_exc()
    finally:
        print(f"=> Done. Created {created} accounts, {failed} failures.")


def main():
    # Ignore broken pipe errors
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    try:
        asyncio.run(app_main())
    except KeyboardInterrupt:
        print("\n=> Interrupted")


if __name__ == "__main__":
    main()
