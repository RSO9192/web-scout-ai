"""Per-URL context passed through the Fetcher → Parser → Crawler pipeline."""

from pydantic import BaseModel, PrivateAttr


class URLContext(BaseModel):
    """Context object created for each URL entering the pipeline.

    Carries the URL, its depth from the initial seed, an optional parent URL,
    and an optional CSS selector to wait for during browser fetches.

    ``stop()`` sets a private flag that the Orchestrator polls at each stage
    boundary (fetch → parse, parse → crawl, crawl → queue) to cancel
    remaining work for this URL without affecting others.
    """

    url: str
    depth: int
    parent_url: str | None = None
    wait_for: str | None = None     # CSS selector for browser wait (forwarded to ScraplingFetcher)
    _stopped: bool = PrivateAttr(default=False)

    def stop(self) -> None:
        """Signal the Orchestrator to skip remaining pipeline stages for this URL."""
        self._stopped = True

    @property
    def is_stopped(self) -> bool:
        return self._stopped
