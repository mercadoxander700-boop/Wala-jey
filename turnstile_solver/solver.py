import asyncio
import datetime
import logging
import time
from pathlib import Path
from typing import Callable, Awaitable
from patchright.async_api import async_playwright, Page, BrowserContext, Browser, Playwright

import turnstile_solver.constants as c
from turnstile_solver.enums import CaptchaApiMessageEvent
from turnstile_solver.proxy import Proxy
from turnstile_solver.solver_console import SolverConsole
from turnstile_solver.turnstile_result import TurnstileResult
from turnstile_solver.turnstile_solver_server import TurnstileSolverServer, CAPTCHA_EVENT_CALLBACK_ENDPOINT

logger = logging.getLogger(__name__)

BROWSER_ARGS = {

  "--no-sandbox",
  "--disable-dev-shm-usage",
  "--disable-setuid-sandbox",
  "--disable-software-rasterizer",

  "--disable-blink-features=AutomationControlled",  # avoid navigator.webdriver detection
  "--disable-background-networking",
  "--disable-background-timer-throttling",
  "--disable-backgrounding-occluded-windows",
  "--disable-renderer-backgrounding",
  '--disable-application-cache',
  '--disable-field-trial-config',
  '--export-tagged-pdf',
  '--force-color-profile=srgb',
  '--safebrowsing-disable-download-protection',
  '--disable-search-engine-choice-screen',
  '--disable-browser-side-navigation',
  '--disable-save-password-bubble',
  '--disable-single-click-autofill',
  '--allow-file-access-from-files',
  '--disable-prompt-on-repost',
  '--dns-prefetch-disable',
  '--disable-translate',
  '--disable-client-side-phishing-detection',
  '--disable-oopr-debug-crash-dump',
  '--disable-top-sites',
  '--ash-no-nudges',
  '--no-crash-upload',
  '--deny-permission-prompts',
  '--simulate-outdated-no-au="Tue, 31 Dec 2099 23:59:59 GMT"',
  '--disable-ipc-flooding-protection',
  '--disable-password-generation',
  '--disable-domain-reliability',
  '--disable-breakpad',

  # Headless-mode essentials – these flags ensure the Turnstile widget
  # renders correctly and the page content is fully laid out even when
  # there is no physical display (Railway / Docker / CI).
  '--disable-gpu',                       # avoid GPU-related crashes in containers
  '--disable-software-rasterizer',        # redundant safety for GPU disable
  '--window-size=1920,1080',              # explicit viewport so widget paints fully
  '--hide-scrollbars',                    # clean screenshots / rendering

  # Allow Manifest V2 extensions
  # --disable-features=ExtensionManifestV2DeprecationWarning,ExtensionManifestV2Disabled,ExtensionManifestV2Unsupported
  '--disable-features=OptimizationHints,OptimizationHintsFetching,Translate,OptimizationTargetPrediction,OptimizationGuideModelDownloading,DownloadBubble,DownloadBubbleV2,InsecureDownloadWarnings,InterestFeedContentSuggestions,PrivacySandboxSettings4,SidePanelPinning,UserAgentClientHint,TrustedDOMTypes,BlockInsecurePrivateNetworkRequests',
  '--no-pings',
  # '--homepage=chrome://version/',
  '--animation-duration-scale=0',
  '--wm-window-animations-disabled',
  '--enable-privacy-sandbox-ads-apis',
  # '--disable-popup-blocking',
  '--lang=en-US',
  '--no-default-browser-check',
  '--no-first-run',
  '--no-service-autorun',
  '--password-store=basic',
  '--log-level=3',
  '--proxy-bypass-list=<-loopback>;localhost;127.0.0.1;*.local',

  # Not needed, here just for reference
  # Network/Connection Tuning
  # '--enable-features=NetworkService,ParallelDownloading',
  # '--max-connections=255',  # Total active connections
  # '--max-parallel-downloads=50',  # Concurrent downloads
  # '--socket-reuse-policy=2',  # Aggressive socket reuse

  # Thread/Process Management
  # '--renderer-process-limit=0',  # Unlimited renderers
  # '--in-process-gpu',  # Reduce process count # NO
  # '--disable-site-isolation-trials',  # Prevent tab grouping # NO

  # Protocol-Specific
  # '--http2-no-coalesce-host',  # Bypass HTTP/2 coalescing
  # '--force-http2-hpack-huffman=off',  # Reduce HPACK overhead
}


class TurnstileSolver:

  def __init__(self,
               server: TurnstileSolverServer | None,
               page_load_timeout: float = c.PAGE_LOAD_TIMEOUT,
               browser_position: tuple[int, int] | None = c.BROWSER_POSITION,
               browser_executable_path: str | Path | None = None,
               browser: str = c.BROWSER,
               reload_page_on_captcha_overrun_event: bool = False,
               max_attempts: int = c.MAX_ATTEMPTS_TO_SOLVE_CAPTCHA,
               attempt_timeout: int = c.CAPTCHA_ATTEMPT_TIMEOUT,
               headless: bool = False,
               console: SolverConsole = SolverConsole(),
               log_level: int | str = logging.INFO,
               proxy: Proxy | None = None,
               browser_args: list[str] | None = None,
               ):

    logger.setLevel(log_level)
    self.console = console
    self.page_load_timeout = page_load_timeout
    self.reload_page_on_captcha_overrun_event = reload_page_on_captcha_overrun_event
    self.browser_executable_path = browser_executable_path
    self.browser = browser
    self.headless = headless

    self.server: TurnstileSolverServer | None = server

    self.browser_args = list(BROWSER_ARGS) + (browser_args or [])
    if browser_position:
      self.browser_args.append(f'--window-position={browser_position[0]},{browser_position[1]}')
    self._error: str | None = None
    self.max_attempts = max_attempts
    self.attempt_timeout = attempt_timeout

    self.proxy = proxy

  @property
  def _server_down(self) -> bool:
    if self.server.down:
      self._error = "Server down"
      logger.warning("Captcha can't be solved because server is down")
      return True
    return False

  @property
  def error(self) -> str:
    return self._error or 'Unknown'

  async def solve(self,
                  site_url: str,
                  site_key: str,
                  attempts: int | None = None,
                  timeout: float | None = None,
                  page: Page | bool = False,
                  about_blank_on_finish: bool = False,
                  ) -> TurnstileResult | None:
    """
    If page is a Page instance, this instance will be reused, else a new BrowserContext instance will be created and destroyed upon finish if browser_context is False, else the created instance will be returned along with the Browser instance
    """

    if not self.server:
      raise RuntimeError("self.server instance has not been assigned")

    if self.server.down:
      raise RuntimeError("Server is down. Make sure to run server and wait fot it to be up. Use method .wait_for_server_up()")

    if not attempts:
      attempts = self.max_attempts
    if not timeout:
      timeout = self.attempt_timeout
    self._error = None
    site_url = site_url.rstrip('/') + "/"

    startTime = time.time()

    result = TurnstileResult()
    self.server.subscribe_captcha_message_event_handler(result.id, result.captcha_api_message_event_handler)

    onFinishCallbacks: list[Callable[[], Awaitable[None]]] = []

    if isinstance(page, bool):
      pageOrContext, playwright = await self.get_browser_context()
      if page is True:
        result.browser_context = pageOrContext
      else:
        async def _closeBrowserAndConnection():
          result.page = None
          await pageOrContext.close()
          await pageOrContext.browser.close()
          await playwright.stop()
          logging.debug("Browser closed")

        onFinishCallbacks.append(_closeBrowserAndConnection)
    else:  # elif isinstance(page, Page):
      pageOrContext = page

    try:
      for a in range(1, attempts + 1):
        logger.info(f"Attempt: {a}/{attempts}")

        result.reset_captcha_fields()

        # 1. Route and load page, reset captcha fields
        if not (page := await self._setup_page(
            page_or_context=result.page or pageOrContext,
            site_url=site_url,
            site_key=site_key,
            id=result.id,
        )):
          return

        result.page = page

        # 2. Wait (briefly) for init event.
        #
        # In some environments, the 'init' event is not reliably forwarded even
        # though the widget can still be solved and a token can still appear in
        # the page. So we treat INIT as best-effort (non-fatal).
        init_wait = min(timeout, 5)
        logger.debug(f"Waiting for '{CaptchaApiMessageEvent.INIT.value}' event (up to {init_wait}s)")
        try:
          if await result.wait_for_captcha_event(evt=CaptchaApiMessageEvent.INIT, timeout=init_wait) is False:
            return
        except TimeoutError:
          logger.warning(
            f"Captcha API message '{CaptchaApiMessageEvent.INIT.value}' event not received within {init_wait} seconds; continuing without it"
          )

        if self._server_down:
          return

        # 3. Wait for 'complete' (or cancellation) OR for token to appear.
        try:
          cancellingEvents = [CaptchaApiMessageEvent.REJECT, CaptchaApiMessageEvent.FAIL, CaptchaApiMessageEvent.RELOAD_REQUEST]
          if self.reload_page_on_captcha_overrun_event:
            cancellingEvents.append(CaptchaApiMessageEvent.OVERRUN_BEGIN)
          poll_task = asyncio.create_task(self._poll_token(page, timeout=timeout), name="poll_turnstile_token")
          complete_task = asyncio.create_task(
            result.wait_for_captcha_event(
              *cancellingEvents,
              evt=CaptchaApiMessageEvent.COMPLETE,
              timeout=timeout,
            ),
            name="wait_turnstile_complete_evt",
          )
          done, pending = await asyncio.wait(
            {poll_task, complete_task},
            return_when=asyncio.FIRST_COMPLETED,
          )
          for t in pending:
            t.cancel()
          if pending:
            # Ensure cancelled tasks don't leak warnings/exceptions.
            await asyncio.gather(*pending, return_exceptions=True)

          # Token polled directly (works even when captcha API events aren't forwarded)
          if poll_task in done:
            token_val = poll_task.result()
            if token_val:
              result.token = token_val
              elapsed = datetime.timedelta(seconds=time.time() - startTime)
              logger.info(f"Captcha solved via token poll. Elapsed: {str(elapsed).split('.')[0]}")
              result.elapsed = elapsed
              break

          # Captcha API event path
          cancellingEvent = complete_task.result()
          if cancellingEvent is False:
            return
          if isinstance(cancellingEvent, CaptchaApiMessageEvent):
            logger.warning(f"'{cancellingEvent.value}' event received")
            continue
        except TimeoutError as te:
          self._error = te.args[0]
          logger.warning(f"Captcha not solved within {timeout} seconds")
          continue
        except asyncio.CancelledError:
          raise
        except Exception as ex:
          self._error = str(ex)
          logger.warning(f"Captcha solve attempt error: {ex}")
          continue

        if result.token is None:
          # Some sites/runtimes can report COMPLETE without a token payload; try to
          # pull it directly from the page as a last resort.
          token_val = await self._poll_token(page, timeout=2)
          if token_val:
            result.token = token_val
          else:
            raise RuntimeError("'result.token' is not supposed to be None at this point")

        elapsed = datetime.timedelta(seconds=time.time() - startTime)
        logger.info(f"Captcha solved. Elapsed: {str(elapsed).split('.')[0]}")
        logger.debug(f"TOKEN: {result.token}")
        result.elapsed = elapsed
        break

      if about_blank_on_finish:
        await page.goto("about:blank")
      if result.token:
        return result
      self._error = f"Captcha failed to solve in {attempts} attempts :("
      logger.error(self._error)
    except Exception as ex:
      self._error = str(ex)
      raise
      # logger.error(ex)
    finally:
      self.server.unsubscribe_captcha_message_event_handler(result.id)
      for callback in onFinishCallbacks:
        await callback()

  async def _poll_token(self, page: Page, timeout: float = 30, interval: float = 0.5) -> str | None:
    """Poll the page for a solved Turnstile token value."""
    end_time = time.time() + timeout
    click_attempted = False
    while time.time() < end_time:
      try:
        token = await page.evaluate(
          "document.querySelector('[name=cf-turnstile-response]')?.value || ''"
        )
        if token:
          return token
        # Try clicking the checkbox periodically — in headless mode the widget
        # sometimes needs an explicit click to trigger the challenge flow.
        if not click_attempted or int(time.time()) % 3 == 0:
          try:
            # First try the outer container
            await page.locator('.cf-turnstile').click(timeout=1500)
            click_attempted = True
          except Exception:
            try:
              # Fallback: click inside the iframe if present
              iframe = page.frame_locator('iframe[title*="Cloudflare"]')
              await iframe.locator('body').click(timeout=1000)
              click_attempted = True
            except Exception:
              pass
      except Exception:
        pass
      await asyncio.sleep(interval)
    return None

  async def _setup_page(
      self,
      page_or_context: BrowserContext | Page,
      site_url: str,
      site_key: str,
      id: str,
  ) -> Page | None:

    if self._server_down:
      return

    page = await page_or_context.new_page() if isinstance(page_or_context, BrowserContext) else page_or_context

    # Expose a direct Python callback that the page can call to forward Turnstile
    # messages. This avoids relying on `fetch(http://127.0.0.1/...)`, which can be
    # blocked as mixed-content when the page origin is HTTPS.
    async def _turnstile_msg_bridge(source, payload):  # noqa: ARG001
      try:
        if isinstance(payload, dict):
          await self.server.dispatch_captcha_message_event(id=id, payload=payload)
      except Exception as ex:
        logger.debug(f"Turnstile message bridge error: {ex}")

    try:
      await page.expose_binding("__turnstileSolverCallback", _turnstile_msg_bridge)
    except Exception:
      # If the page is reused and the binding already exists, ignore.
      pass

    pageContent = c.HTML_TEMPLATE.format(
      local_server_port=self.server.port,
      local_callback_endpoint=CAPTCHA_EVENT_CALLBACK_ENDPOINT.lstrip('/'),
      site_key=site_key,
      id=id,
      secret=self.server.secret,
    )

    async def _fulfill(route):
      await route.fulfill(
        body=pageContent,
        status=200,
        headers={
          "Content-Type": "text/html; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
        },
      )

    await page.route(site_url, _fulfill)

    if page.url != site_url:
      logger.debug(f"Navigating to URL: {site_url}")
      await page.goto(site_url, timeout=self.page_load_timeout * 1000)
    else:
      logger.debug("Reloading page")
      await page.reload(timeout=self.page_load_timeout * 1000)

    page.window_width = await page.evaluate("window.innerWidth")
    page.window_height = await page.evaluate("window.innerHeight")

    return page

  async def get_browser(self,
                        playwright: Playwright | None = None,
                        proxy: Proxy | None = None,
                        ) -> tuple[Browser, Playwright]:

    proxy = proxy or self.proxy

    if not playwright:
      playwright = await async_playwright().start()

    # Use headless="new" for the modern headless mode that shares the same
    # rendering engine as headed Chromium.  The old headless=True mode uses a
    # separate (limited) renderer that is trivially detected by Cloudflare
    # Turnstile, causing every solve attempt to fail.
    headless_mode = "new" if self.headless else False

    # ?
    # browser: Browser | None = await playwright.chromium.launch_persistent_context(no_viewport=True)
    browser: Browser | None = await playwright.chromium.launch(
      executable_path=self.browser_executable_path,
      channel=self.browser,
      args=self.browser_args,
      headless=headless_mode,
      proxy=proxy.dict() if proxy else None,
    )
    return browser, playwright

  async def get_browser_context(self,
                                browser: Browser | None = None,
                                playwright: Playwright | None = None,
                                proxy: Proxy | None = None,
                                ) -> tuple[BrowserContext, Playwright]:

    if not browser:
      browser, playwright = await self.get_browser(playwright=playwright, proxy=proxy)

    context = await browser.new_context(
      proxy=proxy.dict() if proxy else None,
      viewport={"width": 1920, "height": 1080},
      no_viewport=False,
      user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
      ),
    )

    # Anti-detection: override navigator.webdriver so Cloudflare can't
    # trivially flag the browser as automated.
    await context.add_init_script("""
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      // Patch chrome runtime to look like a real browser
      window.chrome = { runtime: {} };
      // Override permissions query
      const originalQuery = window.navigator.permissions.query;
      window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : originalQuery(parameters);
      // Fake plugins length (headless has 0, normal has 5+)
      Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
      });
      // Fake languages
      Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
      });
    """)

    # await context.route('**', lambda route: route.continue_())
    # await context.set_extra_http_headers({'HTTP2-Settings': 'MAX_CONCURRENT_STREAMS=100'})

    if playwright is None:
      raise RuntimeError("Playwright instance is None (this should never happen)")
    return context, playwright
