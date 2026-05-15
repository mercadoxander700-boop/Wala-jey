import asyncio
import logging
from typing import TYPE_CHECKING

from patchright.async_api import Browser

from turnstile_solver.constants import MAX_PAGES_PER_CONTEXT, MAX_CONTEXTS
from turnstile_solver.page_pool import PagePool
from turnstile_solver.pool import Pool
from turnstile_solver.proxy_provider import ProxyProvider

if TYPE_CHECKING:
  from turnstile_solver.solver import TurnstileSolver

logger = logging.getLogger(__name__)


class BrowserContextPool(Pool):
  def __init__(self,
               solver: "TurnstileSolver",
               max_contexts: int = MAX_CONTEXTS,
               max_pages_per_context: int = MAX_PAGES_PER_CONTEXT,
               single_instance: bool = False,
               proxy_provider: ProxyProvider | None = None,
               ):
    self._solver = solver
    self._browser: Browser | None = None
    self._max_pages_per_context = max_pages_per_context
    self._get_lock = asyncio.Lock()
    self._playwright = None
    self._proxy_provider = proxy_provider
    self._single_instance = single_instance

    super().__init__(
      size=max_contexts,
      item_getter=self._page_pool_getter,
    )

  @property
  def browser(self) -> Browser | None:
    return self._browser

  async def init(self):
    logger.info("BrowserContextPool.init() — launching first browser instance …")
    try:
      self._browser, self._playwright = await self._solver.get_browser(None)
      logger.info(f"BrowserContextPool.init() — browser launched: {self._browser}")
    except Exception as exc:
      logger.error(f"BrowserContextPool.init() — browser launch FAILED: {exc}")
      raise

  async def get(self) -> PagePool:

    if not self._browser:
      raise RuntimeError("'self._browser' instance has not been assigned. Make sure to call init() method at least once")

    # logger.debug('Acquiring lock to fetch PagePool')
    async with self._get_lock:
      # logger.debug('Fetching PagePool')
      for pool in self.in_use:
        if not pool.is_full:
          # logger.debug(f"Reusing PagePool (size = {pool.size})")
          return pool
      # logger.debug("Getting PagePool from pool manager")
      try:
        return await super().get()
      except Exception as exc:
        # If browser crashed, try to relaunch it
        if "has been closed" in str(exc) or "Target closed" in str(exc) or "Session closed" in str(exc):
          logger.warning(f"Browser appears to have crashed: {exc}. Attempting to relaunch...")
          try:
            # Close old browser and playwright if they exist
            if self._browser and not self._browser.is_connected():
              try:
                await self._browser.close()
              except Exception:
                pass
            if self._playwright:
              try:
                await self._playwright.stop()
              except Exception:
                pass
            # Launch new browser
            self._browser, self._playwright = await self._solver.get_browser(None)
            logger.info(f"Browser relaunched successfully: {self._browser}")
            # Retry getting the page pool
            return await super().get()
          except Exception as relaunch_exc:
            logger.error(f"Failed to relaunch browser: {relaunch_exc}")
            raise RuntimeError(f"Browser crashed and relaunch failed: {relaunch_exc}")
        raise

  async def _page_pool_getter(self):
    proxy = self._proxy_provider.get() if self._proxy_provider else None
    proxy and logger.debug(f"Using proxy: '{proxy.server}'")
    logger.debug(f"Getting browser context for browser: '{self._browser}'")
    context, self._playwright = await self._solver.get_browser_context(
      browser=self._browser if self._single_instance else None,
      playwright=self._playwright,
      proxy=proxy,
    )
    pool = PagePool(context, self._max_pages_per_context)
    return pool
