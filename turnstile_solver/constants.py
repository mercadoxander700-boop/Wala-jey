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
            min-height: 100vh;
        }}
        #turnstile-container {{
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 65px;
        }}
        .cf-turnstile {{
            display: block !important;
            visibility: visible !important;
            opacity: 1 !important;
            min-width: 300px;
            min-height: 65px;
        }}
        .cf-turnstile iframe {{
            display: block !important;
            visibility: visible !important;
            opacity: 1 !important;
        }}
    </style>
    <script>
        // Message forwarding — must be installed BEFORE the Turnstile script loads
        // so that we capture the earliest postMessage events.
        (function () {{
          function normalizePayload(data) {{
            if (!data) return null;
            if (typeof data === "string") return {{ event: data }};
            if (Array.isArray(data)) return null;
            if (typeof data === "object") {{
              if (!("event" in data) && ("type" in data)) {{
                return Object.assign({{}}, data, {{ event: data.type }});
              }}
              return data;
            }}
            return null;
          }}

          function isAllowedOrigin(origin) {{
            if (!origin || origin === "null") return true;
            if (origin === window.location.origin) return true;
            return origin === "https://challenges.cloudflare.com";
          }}

          window.addEventListener("message", (m) => {{
            if (!isAllowedOrigin(m.origin)) return;
            const payload = normalizePayload(m.data);
            if (!payload || typeof payload.event !== "string") return;

            // Preferred: Playwright expose_binding bridge — works even when the
            // page is served on an HTTPS origin that blocks mixed-content HTTP
            // requests to 127.0.0.1.
            try {{
              if (typeof window.__turnstileSolverCallback === "function") {{
                window.__turnstileSolverCallback(payload);
                return;
              }}
            }} catch (e) {{
              // fall through
            }}

            // Fallback: HTTP callback
            try {{
              fetch("http://127.0.0.1:{local_server_port}/{local_callback_endpoint}?id={id}", {{
                method: "POST",
                body: JSON.stringify(payload),
                headers: {{
                  "Content-type": "application/json; charset=UTF-8",
                  "Secret": "{secret}",
                }},
                keepalive: true,
              }}).catch(() => {{}});
            }} catch (e) {{}}
          }});
        }})();
    </script>
    <script>
        // Turnstile explicit rendering callback
        window.onLoadTurnstileCallback = function () {{
          var container = document.querySelector('.cf-turnstile');
          if (container && window.turnstile) {{
            try {{
              window.turnstile.render(container, {{
                sitekey: '{site_key}',
                callback: function(token) {{
                  // Directly inject the token into a hidden field for reliable polling
                  var input = document.querySelector('[name=cf-turnstile-response]');
                  if (!input) {{
                    input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = 'cf-turnstile-response';
                    document.body.appendChild(input);
                  }}
                  input.value = token;

                  // Also fire a message event so the solver's bridge picks it up
                  window.postMessage({{
                    event: 'complete',
                    token: token
                  }}, '*');
                }},
                'error-callback': function(err) {{
                  console.error('Turnstile error:', err);
                  window.postMessage({{ event: 'fail', error: err }}, '*');
                }},
                'expired-callback': function() {{
                  window.postMessage({{ event: 'tokenExpired' }}, '*');
                }},
                'timeout-callback': function() {{
                  window.postMessage({{ event: 'interactiveTimeout' }}, '*');
                }},
              }});
              // Force the container to be visible after render
              container.style.display = 'block';
              container.style.visibility = 'visible';
              container.style.opacity = '1';
            }} catch (e) {{
              console.error('Turnstile render error:', e);
            }}
          }}
        }};
    </script>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onLoadTurnstileCallback&render=explicit"
            async="">
    </script>
</head>
<body>
<div id="turnstile-container">
  <div class="cf-turnstile" data-sitekey="{site_key}"></div>
</div>
</body>
</html>
'''

TOKEN_JS_SELECTOR = "document.querySelector('[name=cf-turnstile-response]')?.value"

PROJECT_HOME_DIR = Path.home() / '.turnstile_solver'

HOST = "0.0.0.0"
PORT = 8088
CAPTCHA_EVENT_CALLBACK_ENDPOINT = '/api_js_message_callback'

SECRET = "jWRN7DH6"

MAX_ATTEMPTS_TO_SOLVE_CAPTCHA = 3
CAPTCHA_ATTEMPT_TIMEOUT = 30
MAX_CONTEXTS = 40
MAX_PAGES_PER_CONTEXT = 2
PAGE_LOAD_TIMEOUT = 30
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
