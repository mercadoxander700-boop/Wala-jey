import asyncio
import multiprocessing
import os
import random
import threading
import time
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
API_PORT = 5000
DELAY_BETWEEN_ACCOUNTS = 5  # seconds between account creation cycles


# ── Helpers ─────────────────────────────────────────────────────────────────
def gen_str(n: int = 8) -> str:
    return "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=n))


def load_harvested_proxies() -> list[str]:
    """Load proxies from the harvested proxies.txt output file."""
    if not os.path.exists(PROXIES_OUTPUT):
        return []
    with open(PROXIES_OUTPUT, "r") as fh:
        lines = fh.read().strip().splitlines()
    return [l.strip() for l in lines if l.strip()]


def pick_request_proxy(harvested: list[str]) -> dict | None:
    """Return a random requests-compatible proxy dict from harvested proxies."""
    if not harvested:
        return None
    raw = random.choice(harvested)
    parts = raw.split(":")
    if len(parts) == 4:
        user, pwd, host, port = parts
        url = f"http://{user}:{pwd}@{host}:{port}"
    elif len(parts) == 2:
        url = f"http://{parts[0]}:{parts[1]}"
    else:
        url = f"http://{raw}"
    return {"http": url, "https": url}


def load_proxies() -> list[str]:
    """Load proxies from *proxy.txt*.

    If the file contains a single raw API URL (starts with ``http://`` or
    ``https://``), proxies are fetched from that endpoint at runtime.
    Otherwise every non-empty line is treated as a proxy string.
    """
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


def build_proxy_file(proxies: list[str]) -> str | None:
    """Write proxies to a temp file and return the path."""
    if not proxies:
        return None
    tmp = "_proxies_loaded.txt"
    with open(tmp, "w") as fh:
        fh.write("\n".join(proxies) + "\n")
    return tmp


# ── Raw Proxy API Server ────────────────────────────────────────────────────
class _ProxyAPIHandler(BaseHTTPRequestHandler):
    """Serves harvested proxies as raw text at /proxies."""

    def do_GET(self):
        if self.path == "/proxies" or self.path == "/proxies/":
            if os.path.exists(PROXIES_OUTPUT):
                with open(PROXIES_OUTPUT, "r") as fh:
                    content = fh.read()
            else:
                content = ""
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content.encode())
        elif self.path == "/accounts" or self.path == "/accounts/":
            if os.path.exists("accounts.txt"):
                with open("accounts.txt", "r") as fh:
                    content = fh.read()
            else:
                content = ""
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content.encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                b"Wala-jey Proxy API\n"
                b"  GET /proxies  - harvested proxies (raw text)\n"
                b"  GET /accounts - created accounts (raw text)\n"
            )

    def log_message(self, format, *args):
        pass  # suppress noisy request logs


def _start_proxy_api():
    server = HTTPServer(("0.0.0.0", API_PORT), _ProxyAPIHandler)
    print(f"=> Proxy API server running on http://0.0.0.0:{API_PORT}/proxies")
    server.serve_forever()


# ── Solver Server ───────────────────────────────────────────────────────────
def _start_solver_server(proxies_file: str | None = None) -> None:
    """Entry-point for the solver subprocess."""
    from turnstile_solver.main import run_server
    from turnstile_solver.proxy_provider import ProxyProvider

    proxy_provider = None
    if proxies_file:
        proxy_provider = ProxyProvider(proxies_file)
        proxy_provider.load()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            run_server(
                host=SOLVER_HOST,
                port=SOLVER_PORT,
                secret=SOLVER_SECRET,
                headless=False,
                browser="chromium",
                browser_position=(2000, 2000),
                proxy_provider=proxy_provider,
            )
        )
    except Exception as exc:
        print(f"=> Solver server error: {exc}")


def wait_for_solver(timeout: int = 60) -> bool:
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
        except requests.ConnectionError:
            pass
        time.sleep(1)
    print("=> Solver server failed to start within timeout")
    return False


# ── Captcha ─────────────────────────────────────────────────────────────────
def solve_captcha() -> str | None:
    print("=> Solving captcha...")
    try:
        resp = requests.get(
            f"http://{SOLVER_HOST}:{SOLVER_PORT}/solve",
            json={"site_url": SITE_URL, "site_key": SITE_KEY},
            headers={"secret": SOLVER_SECRET},
            timeout=120,
        )
        if resp.status_code != 200:
            print(f"=> Captcha failed [{resp.status_code}]: {resp.text}")
            return None
        data = resp.json()
        token = data.get("token")
        elapsed = data.get("elapsed", "?")
        if token:
            print(f"=> Captcha solved in {elapsed}s: {token[:30]}...")
            return token
        print(f"=> Captcha failed: {data.get('message', 'no token')}")
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
        with open("proxies.txt", "a") as fh:
            for p in prox_list:
                if all(p.get(k) for k in ("username", "password", "host", "port")):
                    fh.write(f"{p['username']}:{p['password']}:{p['host']}:{p['port']}\n")
        print(f"=> Saved {len(prox_list)} proxies to proxies.txt")
        return True
    except Exception as exc:
        print(f"=> Proxy fetch error: {exc}")
        return False


def create_account(captcha_token: str, req_proxy: dict | None = None) -> bool:
    """Register a GoLogin account and fetch its proxies."""
    print("=> Creating account...")
    if req_proxy:
        print(f"   (using proxy: {list(req_proxy.values())[0][:40]}...)")
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
        print(f"=> Account creation failed [{resp.status_code}]: {resp.text[:500]}")
        return False
    except Exception as exc:
        print(f"=> Account creation error: {exc}")
        return False


# ── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    # Start the raw proxy API server so harvested proxies can be fetched
    api_thread = threading.Thread(target=_start_proxy_api, daemon=True)
    api_thread.start()

    # Load proxies (supports raw API URL in proxy.txt)
    proxies = load_proxies()
    if proxies:
        print(f"=> Loaded {len(proxies)} proxies from {PROXY_FILE}")

    proxy_file = build_proxy_file(proxies)

    # Start the turnstile solver server in a background process
    solver_proc = multiprocessing.Process(
        target=_start_solver_server,
        kwargs={"proxies_file": proxy_file},
        daemon=True,
    )
    solver_proc.start()

    if not wait_for_solver():
        print("=> Exiting: solver server not available")
        return

    # Continuous account creation loop
    created = 0
    failed = 0
    print("=" * 50)
    print("=> Starting continuous account creation")
    print(f"=> Proxy API: http://0.0.0.0:{API_PORT}/proxies")
    print(f"=> Press Ctrl+C to stop")
    print("=" * 50)

    try:
        while True:
            cycle = created + failed + 1
            print(f"\n--- Cycle {cycle} (created: {created}, failed: {failed}) ---")

            # Pick a random harvested proxy for API requests (if available)
            harvested = load_harvested_proxies()
            req_proxy = pick_request_proxy(harvested)
            if req_proxy:
                print(f"=> Using harvested proxy for API calls ({len(harvested)} available)")

            # Solve captcha
            token = solve_captcha()
            if not token:
                failed += 1
                print(f"=> Captcha failed, retrying in {DELAY_BETWEEN_ACCOUNTS}s...")
                time.sleep(DELAY_BETWEEN_ACCOUNTS)
                continue

            # Create account with proxy rotation
            if create_account(token, req_proxy=req_proxy):
                created += 1
                print(f"=> Total accounts created: {created}")
            else:
                failed += 1

            # Brief delay between cycles to avoid rate limiting
            print(f"=> Waiting {DELAY_BETWEEN_ACCOUNTS}s before next cycle...")
            time.sleep(DELAY_BETWEEN_ACCOUNTS)

    except KeyboardInterrupt:
        print(f"\n=> Shutting down. Created {created} accounts, {failed} failures.")


if __name__ == "__main__":
    main()
