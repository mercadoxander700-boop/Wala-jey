import datetime
import logging
import time
from typing import Any

import asyncio

from patchright.async_api import BrowserContext, Page

from turnstile_solver.enums import CaptchaApiMessageEvent
from turnstile_solver.utils import password

logger = logging.getLogger(__name__)

_CLICK_CHECKBOX_SCRIPT = """
  // Ensure the widget is visible before trying to interact
  const container = document.querySelector('.cf-turnstile');
  if (container) {
    container.style.display = 'block';
    container.style.visibility = 'visible';
    container.style.opacity = '1';
    container.style.minHeight = '65px';
    container.style.minWidth = '300px';
  }
  // Ensure iframes are visible too
  const iframes = document.querySelectorAll('.cf-turnstile iframe');
  iframes.forEach(f => {
    f.style.display = 'block';
    f.style.visibility = 'visible';
    f.style.opacity = '1';
  });
"""


class TurnstileResult:
  def __init__(self,
               token: str | None = None,
               elapsed: datetime.timedelta | None = None,
               browser_context: BrowserContext | None = None,
               page: Page | None = None,
               ):
    self.token = token
    self.elapsed = elapsed
    self.browser_context = browser_context
    self.page = page
    self._id = password(10)
    self._received_captcha_events: set[CaptchaApiMessageEvent] = set()

  @property
  def id(self) -> str:
    return self._id

  async def captcha_api_message_event_handler(self, evt: CaptchaApiMessageEvent, data: dict[str, Any]):
    if evt == CaptchaApiMessageEvent.COMPLETE:
      self.token = data['token']
    elif evt == CaptchaApiMessageEvent.INTERACTIVE_BEGIN:
      # Wait some time?
      # import random
      # await asyncio.sleep(random.uniform(0.1, 0.5))
      await self.click_checkbox()
    self._received_captcha_events.add(evt)

  def reset_captcha_fields(self):
    self._received_captcha_events.clear()
    self.token = None

  async def wait_for_captcha_event(self,
                                   *cancelling_evts: CaptchaApiMessageEvent,
                                   evt: CaptchaApiMessageEvent,
                                   timeout: float,
                                   sleep_time: float = 0.05,
                                   ) -> CaptchaApiMessageEvent | bool:
    endTime = time.time() + timeout
    while True:
      if evt in self._received_captcha_events:
        return True
      elif cancelling_evts:
        for e in cancelling_evts:
          if e in self._received_captcha_events:
            return e
      if time.time() >= endTime:
        raise TimeoutError(f"Captcha event '{evt.value}' not received within {timeout} seconds")
      await asyncio.sleep(sleep_time)

  async def click_checkbox(self, page: Page | None = None):
    page = page or self.page
    # First ensure the widget is visible
    await page.evaluate(_CLICK_CHECKBOX_SCRIPT)
    await asyncio.sleep(0.3)

    # Try clicking the Turnstile widget — use force=True because the widget
    # may still report as hidden even after our visibility overrides.
    try:
      await page.locator('.cf-turnstile').click(timeout=2000, force=True)
      logger.debug("Attempt to click checkbox performed (outer container)")
    except Exception as e:
      logger.debug(f"Outer click failed: {e}")
      try:
        # Try clicking inside the Cloudflare iframe
        iframe = page.frame_locator('iframe[src*="challenges.cloudflare.com"]')
        await iframe.locator('body').click(timeout=1500)
        logger.debug("Attempt to click checkbox performed (iframe)")
      except Exception:
        try:
          iframe = page.frame_locator('iframe[title*="Cloudflare"]')
          await iframe.locator('body').click(timeout=1500)
          logger.debug("Attempt to click checkbox performed (alt iframe)")
        except TimeoutError:
          logger.error("Captcha widget click timed-out")
