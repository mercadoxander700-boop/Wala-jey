import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Turnstile Solver</title>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            background: #ffffff;
        }}
    </style>
    <script>
        // Collect postMessage events from the Turnstile iframe into a global array.
        // The Python solver polls this array via page.evaluate() to dispatch events
        // to the handler. This avoids mixed-content blocking (fetch from HTTPS page
        // to HTTP localhost is blocked by browsers) and works in Patchright where
        // expose_binding and page.on('console') are broken.
        window.__cfEvents = window.__cfEvents || [];
        window.__cfEventIdx = window.__cfEventIdx || 0;
        window.addEventListener("message", function(m) {{
            if (m.origin !== "https://challenges.cloudflare.com" || !!m.data === false) return;
            try {{
                window.__cfEvents.push(JSON.parse(JSON.stringify(m.data)));
            }} catch(e) {{}}
        }});
    </script>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onloadTurnstileCallback" async="" defer=""></script>
    <script>
        function onloadTurnstileCallback() {{
            document.title = "Turnstile API loaded";
        }}
    </script>
</head>
<body>
    <div class="cf-turnstile" data-sitekey="{site_key}" style="display: inline-block; background: white;"></div>
</body>
</html>
'''

TOKEN_JS_SELECTOR = "document.querySelector('[name=cf-turnstile-response]')?.value"

PROJECT_HOME_DIR = Path.home() / '.turnstile_solver'

HOST = "0.0.0.0"
PORT = 8088
CAPTCHA_EVENT_CALLBACK_ENDPOINT = '/api_js_message_callback'

SECRET = "jWRN7DH6"

MAX_ATTEMPTS_TO_SOLVE_CAPTCHA = 5
CAPTCHA_ATTEMPT_TIMEOUT = 60
MAX_CONTEXTS = 40
MAX_PAGES_PER_CONTEXT = 2
PAGE_LOAD_TIMEOUT = 60
BROWSER_POSITION = 2000, 2000
BROWSER = "chromium"
BROWSERS = [
  "chrome",
  "chromium",
  # "msedge",
]

CONSOLE_THEME_STYLES = {
  # Overrides
  "json.key": "#FFFFFF",
  "json.null": "#BCBEC4",
  "json.bool_true": "#00FF00",
  "json.bool_false": "#FF0000",
  "repr.url": "not bold not italic #64B5F6",
  'log.time': 'magenta',
  'logging.keyword': 'bold yellow',
  'logging.level.critical': 'bold reverse red',
  'logging.level.debug': 'green',
  'logging.level.error': 'bold red',
  'logging.level.info': 'cyan',
  'logging.level.notset': 'dim',
  'logging.level.warning': '#FFE600',

  "repr.author": "bold #FFFFFF",
  "repr.version": "bold italic",
  "repr.projectname": "bold italic blink #FFFFFF",
}

# Environment
NGROK_TOKEN = os.environ.get('NGROK_TOKEN')
