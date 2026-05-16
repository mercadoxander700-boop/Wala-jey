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

  "--disable-blink-features=AutomationControlled",
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

  # ── Docker / Xvfb rendering essentials ──
  # These flags are CRITICAL for Turnstile to work inside containers.
  # Turnstile uses WebGL/WebGPU fingerprinting — without these it detects
  # a non-rendering environment and blocks the CAPTCHA.
  #
  # The working Docker setup (odell0111 PR #4) uses BOTH --disable-gpu AND
  # --use-gl=swiftshader. This is NOT contradictory:
  #   --disable-gpu                disables HARDWARE GPU (no GPU in Docker)
  #   --use-gl=swiftshader         enables SOFTWARE GL via SwiftShader
  #   --disable-software-rasterizer  disables the default software rasterizer
  #                                 so SwiftShader is used instead
  #   --enable-webgl               Turnstile checks for WebGL support
  #   --enable-unsafe-webgpu       some Turnstile challenges use WebGPU
  '--disable-gpu',
  '--disable-software-rasterizer',
  '--use-gl=swiftshader',
  '--enable-webgl',
  '--enable-unsafe-webgpu',
  '--start-maximized',
  '--window-size=1024,720',

  '--disable-features=OptimizationHints,OptimizationHintsFetching,Translate,OptimizationTargetPrediction,OptimizationGuideModelDownloading,DownloadBubble,DownloadBubbleV2,InsecureDownloadWarnings,InterestFeedContentSuggestions,PrivacySandboxSettings4,SidePanelPinning,UserAgentClientHint,TrustedDOMTypes,BlockInsecurePrivateNetworkRequests',
  '--enable-features=SharedArrayBuffer,TrustTokens,PrivateNetworkAccessChecksBypassingPermissionPolicy',
  '--no-pings',
  '--animation-duration-scale=0',
  '--wm-window-animations-disabled',
  '--enable-privacy-sandbox-ads-apis',
  '--lang=en-US,en',
  '--no-default-browser-check',
  '--no-first-run',
  '--no-service-autorun',
  '--password-store=basic',
  '--log-level=3',
  '--proxy-bypass-list=<-loopback>;localhost;127.0.0.1;*.local',
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
    """Poll the page for a solved Turnstile token value.

    This is the simple, proven approach: just check the hidden input
    that Turnstile auto-creates when using auto-render mode. No complex
    clicking or iframe manipulation — the Turnstile JS handles all of that
    when it can render properly in a headed browser with SwiftShader.
    """
    end_time = time.time() + timeout

    while time.time() < end_time:
      try:
        # The auto-render mode creates a hidden input named cf-turnstile-response
        # and populates it with the token when the challenge is solved.
        token = await page.evaluate("""
          (() => {
            const el = document.querySelector('[name=cf-turnstile-response]');
            if (el && el.value && el.value.length > 10) return el.value;
            return '';
          })()
        """)
        if token and len(token) > 10:
          logger.info(f"Token found via input value: {token[:30]}...")
          return token

        # Also try the textarea variant (some Turnstile versions)
        token = await page.evaluate("""
          (() => {
            const el = document.querySelector('textarea[name=cf-turnstile-response]');
            if (el && el.value && el.value.length > 10) return el.value;
            return '';
          })()
        """)
        if token and len(token) > 10:
          logger.info(f"Token found via textarea: {token[:30]}...")
          return token

      except Exception as e:
        logger.debug(f"Token poll error: {e}")

      await asyncio.sleep(interval)

    logger.warning(f"Token not found after {timeout}s")
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

    # Wait for the Turnstile widget div to be attached to the DOM.
    try:
      await page.wait_for_selector('.cf-turnstile', state="attached", timeout=15000)
      logger.debug("Turnstile widget div attached to DOM")
    except Exception as e:
      logger.warning(f"Turnstile widget div not attached after 15s: {e}")

    # Wait for the Turnstile iframe to appear inside the container.
    try:
      await page.wait_for_selector('.cf-turnstile iframe', state="attached", timeout=20000)
      logger.debug("Turnstile challenge iframe attached")
    except Exception as e:
      logger.warning(f"Turnstile challenge iframe not found after 20s: {e}")

    return page

  async def get_browser(self,
                        playwright: Playwright | None = None,
                        proxy: Proxy | None = None,
                        ) -> tuple[Browser, Playwright]:

    proxy = proxy or self.proxy

    if not playwright:
      playwright = await async_playwright().start()

    # Use the browser channel from self.browser (e.g. "chromium", "chrome").
    # Patchright's bundled Chromium is the default and works in Docker.
    channel = self.browser if self.browser != "chromium" else None

    logger.info(f"Launching browser: headless={self.headless}, channel={channel}, "
                f"executable_path={self.browser_executable_path}, "
                f"args_count={len(self.browser_args)}")
    logger.debug(f"Browser args: {self.browser_args}")

    try:
      browser: Browser | None = await playwright.chromium.launch(
        executable_path=self.browser_executable_path,
        channel=channel,
        args=self.browser_args,
        headless=self.headless,
        proxy=proxy.dict() if proxy else None,
      )
    except Exception as launch_err:
      logger.error(f"Browser launch failed: {launch_err}")
      # Try once more without a channel as fallback
      if channel is not None:
        logger.info("Retrying browser launch without channel...")
        try:
          browser = await playwright.chromium.launch(
            executable_path=self.browser_executable_path,
            channel=None,
            args=self.browser_args,
            headless=self.headless,
            proxy=proxy.dict() if proxy else None,
          )
        except Exception as retry_err:
          logger.error(f"Browser launch retry also failed: {retry_err}")
          raise
      else:
        raise

    logger.info(f"Browser launched successfully: {browser}")
    return browser, playwright

  async def get_browser_context(self,
                                browser: Browser | None = None,
                                playwright: Playwright | None = None,
                                proxy: Proxy | None = None,
                                ) -> tuple[BrowserContext, Playwright]:

    if not browser:
      browser, playwright = await self.get_browser(playwright=playwright, proxy=proxy)
    elif not playwright:
      # If a browser was provided but no playwright, we need to create one
      # so that the caller can properly shut it down later.
      logger.warning("Browser provided without Playwright instance — creating a new Playwright")
      playwright = await async_playwright().start()

    # Match the working odell0111 original:
    # - no_viewport=True: do NOT set an explicit viewport — an explicit viewport
    #   is a fingerprinting signal that Turnstile can detect. The browser window
    #   size is controlled by --window-size in BROWSER_ARGS instead.
    # - NO user_agent override: Patchright's default UA is fine; overriding it
    #   is another fingerprinting signal.
    # - NO add_init_script(): Patchright already handles anti-detection internally.
    #   Adding our own init scripts can CONFLICT with Patchright's patches and
    #   actually make detection MORE likely (e.g. double-overriding navigator.webdriver).
    context = await browser.new_context(
      proxy=proxy.dict() if proxy else None,
      no_viewport=True,
    )

    if playwright is None:
      raise RuntimeError("Playwright instance is None (this should never happen)")
    return context, playwright
