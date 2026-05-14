import logging
import os
from pathlib import Path

from turnstile_solver.proxy import Proxy

logger = logging.getLogger(__name__)


class ProxyProvider:
  def __init__(self, proxies_fp: str | Path):
    self.proxies_fp = proxies_fp
    self._index = 0
    self.proxies: list[Proxy] = []
    self._last_mtime: float = 0

  def get(self) -> Proxy | None:
    self._reload_if_changed()
    if not self.proxies:
      return
    proxy = self.proxies[self._index]
    self._index = (self._index + 1) % len(self.proxies)
    return proxy

  def _reload_if_changed(self):
    """Reload proxies if the file has been modified since last load."""
    try:
      mtime = os.path.getmtime(self.proxies_fp)
      if mtime > self._last_mtime:
        self.load()
    except OSError:
      pass

  def load(self):
    try:
      self._last_mtime = os.path.getmtime(self.proxies_fp)
    except OSError:
      pass
    with open(self.proxies_fp, 'rt') as f:
      proxyCount = 0
      new_proxies = []
      for line in f.readlines():
        if not (line := line.strip()) or line.startswith('#'):
          continue
        parts = line.split('@')
        server = parts[0]
        if len(parts) > 1:
          username, password = parts[1].split(':')
        else:
          username = password = None
        new_proxies.append(Proxy(server, username, password))
        proxyCount += 1
      self.proxies = new_proxies
      self._index = 0
      logger.info(f"{proxyCount} proxies loaded from '{self.proxies_fp}'")

  def __repr__(self) -> str:
    return str(self.proxies)
