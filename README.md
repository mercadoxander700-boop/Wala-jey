# Wala-jey

GoLogin account creator with integrated Cloudflare Turnstile solver.

## Features

- **Built-in Turnstile solver** -- uses [turnstile_solver](https://github.com/odell0111/turnstile_solver) (patchright / Playwright) to solve Cloudflare Turnstile captchas locally. No external captcha service needed.
- **Raw API proxy support** -- put a proxy API URL in `proxy.txt` and fresh proxies are fetched at runtime instead of keeping a static list.
- **Auto proxy harvest** -- after creating a GoLogin account the script fetches proxies from the GoLogin API and saves them to `proxies.txt`.

## Setup (local)

```bash
pip install -r requirements.txt
patchright install chromium
```

## Deploy on Railway

1. Push this repo to GitHub.
2. Create a new project on [Railway](https://railway.app) and connect the repo.
3. Railway auto-detects the `Dockerfile` — no extra config needed.
4. Set the `PORT` environment variable in Railway if you want a custom port (defaults to `5000`).
5. The app runs headless automatically (no display required).

## Proxy configuration (`proxy.txt`)

`proxy.txt` supports two formats:

### Raw API link (recommended)

Put a single URL on the first line. The script will `GET` the URL and parse
the response as a proxy list (plain text, one per line, or JSON).

```
https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all
```

### Static list

```
http://proxy1.example.com:8080
http://proxy2.example.com:3128@user:pass
```

## Usage

```bash
python go.py
```

The script will:

1. Start a **raw proxy API server** on port 5000.
2. Read `proxy.txt` -- if it contains a URL, proxies are fetched from that API.
3. Start the Turnstile solver server in the background (Chromium).
4. Solve the GoLogin sign-up captcha.
5. Create a new GoLogin account and save credentials to `accounts.txt`.
6. Fetch proxies from the new account and append to `proxies.txt`.
7. Keep running so harvested proxies stay accessible via the API.

## Raw Proxy API

After running `go.py`, harvested proxies are served as raw text via HTTP:

| Endpoint | Description |
|----------|-------------|
| `GET /proxies` | Harvested proxies from GoLogin (`user:pass:host:port`, one per line) |
| `GET /accounts` | Created accounts (`email:password`, one per line) |

**Railway:**
```
https://your-app.up.railway.app/proxies
```

**Replit:**
```
https://your-repl.repl.co/proxies
```

**Local:**
```
http://localhost:5000/proxies
```

Use any of the above as a raw API proxy link in other tools. Proxies update live as new accounts are created.

## Output files

| File | Content |
|------|---------|
| `accounts.txt` | `email:password` for every created account |
| `proxies.txt` | `user:pass:host:port` proxies harvested from GoLogin |

## Credits

Turnstile solver by [OGM](https://github.com/odell0111/turnstile_solver).
