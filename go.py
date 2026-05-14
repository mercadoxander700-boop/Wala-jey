import asyncio
import multiprocessing
import os
import random
import sys
import signal
import threading
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Configuration ───────────────────────────────────────────────────────────
SOLVER_HOST = "127.0.0.1"
SOLVER_PORT = 8088
SOLVER_SECRET = "jWRN7DH6"

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
API_PORT = int(os.environ.get("PORT", 5000))  # Replit sets PORT env var
DELAY_BETWEEN_ACCOUNTS = 5  # seconds between account creation cycles
MAX_CONSECUTIVE_FAILURES = 10  # restart solver after this many failures in a row
SOLVER_PROXY_FILE = "_solver_proxies.txt"


# ── Helpers ─────────────────────────────────────────────────────────────────
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


def load_proxies() -> list[str]:
    """Load proxies from *proxy.txt*.

    If the file contains a single raw API URL (starts with ``http://`` or
    ``https://``), proxies are fetched from that endpoint at runtime.
    Otherwise every non-empty line is treated as a proxy string.
    """
    try:
        if not os.path.exists(PROXY_FILE):
            return []

        with open(PROXY_FILE, "r") as fh:
            content = fh.read().strip()

        if not content:
            return []

        first_line = content.splitlines()[0].strip()
        if first_line.startswith("http://") or first_line.startswith("https://"):
            api_url = first_line
            print(f"=> Fetching proxies from API: {api_url}")
            try:
                resp = requests.get(api_url, timeout=30, verify=False)
                if resp.status_code != 200:
                    print(f"=> Proxy API returned HTTP {resp.status_code}")
                    return []
                try:
                    data = resp.json()
                    if isinstance(data, list):
                        proxies = [str(p) for p in data if p]
                    elif isinstance(data, dict) and "proxies" in data:
                        proxies = [str(p) for p in data["proxies"] if p]
                    else:
                        proxies = resp.text.strip().splitlines()
                except ValueError:
                    proxies = resp.text.strip().splitlines()
                print(f"=> Fetched {len(proxies)} proxies from API")
                return [p.strip() for p in proxies if p.strip()]
            except Exception as exc:
                print(f"=> Error fetching proxies from API: {exc}")
                return []

        return [line.strip() for line in content.splitlines() if line.strip()]
    except Exception as exc:
        print(f"=> Error loading proxies: {exc}")
        return []


def build_proxy_file() -> str:
    """Create an empty solver proxy file.

    The file starts empty -- only harvested GoLogin proxies are written to
    it (via sync_harvested_to_solver).  The ProxyProvider auto-reloads
    when it detects changes.
    """
    if not os.path.exists(SOLVER_PROXY_FILE):
        with open(SOLVER_PROXY_FILE, "w") as fh:
            fh.write("")
    return SOLVER_PROXY_FILE


def sync_harvested_to_solver():
    """Append harvested proxies (from proxies.txt) to the solver proxy file.

    Converts ``user:pass:host:port`` → ``host:port@user:pass`` format that
    the solver's ProxyProvider expects.  The provider auto-reloads on change.
    """
    try:
        if not os.path.exists(PROXIES_OUTPUT):
            return
        with open(PROXIES_OUTPUT, "r") as fh:
            lines = [l.strip() for l in fh if l.strip()]
        if not lines:
            return

        converted = []
        for line in lines:
            parts = line.split(":")
            if len(parts) == 4:
                user, pwd, host, port = parts
                converted.append(f"{host}:{port}@{user}:{pwd}")
            else:
                converted.append(line)

        with open(SOLVER_PROXY_FILE, "w") as fh:
            fh.write("\n".join(converted) + "\n")
        print(f"=> Synced {len(converted)} harvested proxies to solver")
    except Exception as exc:
        print(f"=> Error syncing proxies to solver: {exc}")


# ── Raw Proxy API Server ────────────────────────────────────────────────────
class _ProxyAPIHandler(BaseHTTPRequestHandler):
    """Serves harvested proxies as raw text at /proxies."""

    def do_GET(self):
        try:
            if self.path == "/proxies" or self.path == "/proxies/":
                content = ""
                if os.path.exists(PROXIES_OUTPUT):
                    with open(PROXIES_OUTPUT, "r") as fh:
                        content = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content.encode())
            elif self.path == "/accounts" or self.path == "/accounts/":
                content = ""
                if os.path.exists("accounts.txt"):
                    with open("accounts.txt", "r") as fh:
                        content = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content.encode())
            elif self.path == "/status" or self.path == "/status/":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"running")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(
                    b"Wala-jey Proxy API\n"
                    b"  GET /proxies  - harvested proxies (raw text)\n"
                    b"  GET /accounts - created accounts (raw text)\n"
                    b"  GET /status   - server status\n"
                )
        except Exception:
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def log_message(self, format, *args):
        pass  # suppress noisy request logs


def _start_proxy_api():
    """Start the proxy API server. Retries on failure."""
    while True:
        try:
            server = HTTPServer(("0.0.0.0", API_PORT), _ProxyAPIHandler)
            print(f"=> Proxy API server running on http://0.0.0.0:{API_PORT}/proxies")
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


# ── Solver Server ───────────────────────────────────────────────────────────
def _start_solver_server(proxies_file: str) -> None:
    """Entry-point for the solver subprocess."""
    try:
        from turnstile_solver.main import run_server
        from turnstile_solver.proxy_provider import ProxyProvider

        proxy_provider = ProxyProvider(proxies_file)
        proxy_provider.load()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            run_server(
                host=SOLVER_HOST,
                port=SOLVER_PORT,
                secret=SOLVER_SECRET,
                headless=False,
                browser="chromium",
                browser_position=(2000, 2000),
                proxy_provider=proxy_provider,
                max_attempts=5,
                attempt_timeout=30,
            )
        )
    except Exception as exc:
        print(f"=> Solver server error: {exc}")
        traceback.print_exc()


def start_solver_process(proxy_file: str) -> multiprocessing.Process:
    """Start the solver in a subprocess and return the Process object."""
    proc = multiprocessing.Process(
        target=_start_solver_server,
        args=(proxy_file,),
        daemon=True,
    )
    proc.start()
    return proc


def wait_for_solver(timeout: int = 90) -> bool:
    """Wait until the solver server is reachable."""
    print("=> Waiting for solver server...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"http://{SOLVER_HOST}:{SOLVER_PORT}/",
                headers={"secret": SOLVER_SECRET},
                timeout=3,
            )
            if resp.status_code == 200:
                print("=> Solver server is ready")
                return True
        except (requests.ConnectionError, requests.Timeout):
            pass
        except Exception:
            pass
        time.sleep(1)
    print("=> Solver server failed to start within timeout")
    return False


def is_solver_alive(proc: multiprocessing.Process) -> bool:
    """Check if the solver subprocess is still running."""
    return proc is not None and proc.is_alive()


# ── Captcha ─────────────────────────────────────────────────────────────────
def solve_captcha() -> str | None:
    """Request the solver to solve a Turnstile captcha."""
    print("=> Solving captcha...")
    try:
        resp = requests.get(
            f"http://{SOLVER_HOST}:{SOLVER_PORT}/solve",
            json={"site_url": SITE_URL, "site_key": SITE_KEY},
            headers={"secret": SOLVER_SECRET},
            timeout=120,
        )
        if resp.status_code != 200:
            print(f"=> Captcha failed [{resp.status_code}]: {resp.text[:200]}")
            return None
        data = resp.json()
        token = data.get("token")
        elapsed = data.get("elapsed", "?")
        if token:
            print(f"=> Captcha solved in {elapsed}s: {token[:30]}...")
            return token
        print(f"=> Captcha failed: {data.get('message', 'no token')}")
    except requests.Timeout:
        print("=> Captcha timed out (120s)")
    except requests.ConnectionError:
        print("=> Captcha error: solver server not reachable")
    except Exception as exc:
        print(f"=> Captcha error: {exc}")
    return None


# ── GoLogin API ─────────────────────────────────────────────────────────────
def get_proxies_from_gologin(bearer: str, req_proxy: dict | None = None) -> bool:
    """Fetch proxy list from GoLogin and append to *proxies.txt*."""
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
            print("=> No proxies returned")
            return False
        with open(PROXIES_OUTPUT, "a") as fh:
            for p in prox_list:
                if all(p.get(k) for k in ("username", "password", "host", "port")):
                    fh.write(f"{p['username']}:{p['password']}:{p['host']}:{p['port']}\n")
        print(f"=> Saved {len(prox_list)} proxies to {PROXIES_OUTPUT}")
        return True
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
            with open("accounts.txt", "a") as fh:
                fh.write(f"{email}:{pwd}\n")
            print("=> Saved to accounts.txt")
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


# ── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    print("=" * 50)
    print("  Wala-jey — GoLogin Account Creator")
    print("=" * 50)

    # Start the raw proxy API server (runs in background thread)
    api_thread = threading.Thread(target=_start_proxy_api, daemon=True)
    api_thread.start()

    # Load initial proxies for API requests (supports raw API URL in proxy.txt)
    proxies = load_proxies()
    if proxies:
        print(f"=> Loaded {len(proxies)} proxies from {PROXY_FILE} (for API requests)")
    else:
        print(f"=> No proxies loaded (add proxies to {PROXY_FILE} for best results)")

    # Solver browser proxy file starts empty; only harvested GoLogin proxies
    # (residential) go here. Public proxies from proxy.txt are too unreliable
    # for browser automation.
    proxy_file = build_proxy_file()

    # Start the turnstile solver server in a subprocess
    solver_proc = start_solver_process(proxy_file)

    if not wait_for_solver():
        print("=> ERROR: Solver server failed to start")
        print("=> Make sure patchright + chromium are installed:")
        print("   pip install -r requirements.txt")
        print("   patchright install chromium")
        return

    # Continuous account creation loop with auto-restart on crash
    created = 0
    failed = 0
    consecutive_failures = 0

    print("=" * 50)
    print("=> Starting continuous account creation")
    print(f"=> Proxy API: http://0.0.0.0:{API_PORT}/proxies")
    print("=> Press Ctrl+C to stop")
    print("=" * 50)

    try:
        while True:
            cycle = created + failed + 1
            print(f"\n--- Cycle {cycle} (created: {created}, failed: {failed}) ---")

            # Check if solver is still alive; restart if crashed
            if not is_solver_alive(solver_proc):
                print("=> Solver process died, restarting...")
                try:
                    solver_proc.terminate()
                except Exception:
                    pass
                time.sleep(2)
                solver_proc = start_solver_process(proxy_file)
                if not wait_for_solver():
                    print("=> Solver restart failed, waiting 30s...")
                    time.sleep(30)
                    continue

            # Pick a random harvested proxy for API requests (if available)
            harvested = load_harvested_proxies()
            req_proxy = pick_request_proxy(harvested)
            if req_proxy:
                print(f"=> Using harvested proxy for API calls ({len(harvested)} available)")

            # Solve captcha
            try:
                token = solve_captcha()
            except Exception as exc:
                print(f"=> Unexpected captcha error: {exc}")
                token = None

            if not token:
                consecutive_failures += 1
                failed += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"=> {consecutive_failures} failures in a row, restarting solver...")
                    try:
                        solver_proc.terminate()
                    except Exception:
                        pass
                    time.sleep(3)
                    solver_proc = start_solver_process(proxy_file)
                    if not wait_for_solver():
                        print("=> Solver restart failed, waiting 30s...")
                        time.sleep(30)
                    consecutive_failures = 0
                else:
                    delay = min(DELAY_BETWEEN_ACCOUNTS * (1 + consecutive_failures), 60)
                    print(f"=> Captcha failed ({consecutive_failures}x), retrying in {delay}s...")
                    time.sleep(delay)
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

            # Brief delay between cycles to avoid rate limiting
            print(f"=> Waiting {DELAY_BETWEEN_ACCOUNTS}s before next cycle...")
            time.sleep(DELAY_BETWEEN_ACCOUNTS)

    except KeyboardInterrupt:
        print(f"\n=> Shutting down. Created {created} accounts, {failed} failures.")
    except Exception as exc:
        print(f"\n=> Fatal error in main loop: {exc}")
        traceback.print_exc()
    finally:
        # Clean up solver process
        try:
            if solver_proc and solver_proc.is_alive():
                solver_proc.terminate()
                solver_proc.join(timeout=5)
        except Exception:
            pass
        print("=> Goodbye!")


if __name__ == "__main__":
    # Ignore broken pipe errors (common on Replit when clients disconnect)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL) if hasattr(signal, "SIGPIPE") else None
    main()
