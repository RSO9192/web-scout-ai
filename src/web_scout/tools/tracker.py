"""ResearchTracker — accumulates URL/query records from tool calls."""

from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_ACTION_RANK = {
    "snippet_only": 1,
    "scrape_failed": 2,
    "blocked_by_policy": 2,
    "source_http_error": 2,
    "scraped_irrelevant": 2,
    "bot_detected": 2,
    "scraped": 3,
}

_TRACKING_PARAMS: frozenset = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_source_platform",
        "fbclid",
        "gclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "ref",
    }
)

_BOT_BLOCK_THRESHOLD = 2


class ResearchTracker:
    """Accumulates URL and query records from tool calls."""

    def __init__(self):
        self._urls: Dict[str, Any] = {}
        self._actions: Dict[str, str] = {}
        self._queries: List[Any] = []
        self._consecutive_empty: Dict[str, int] = {}
        self._domain_bot_counts: Dict[str, int] = {}
        self._bot_blocked_domains: set[str] = set()

        self.search_count = 0
        self.scrape_count = 0

    @staticmethod
    def _normalize_url(url: str) -> str:
        p = urlparse(url)
        scheme = "https" if p.scheme in ("http", "https") else p.scheme
        if p.query:
            filtered = [
                (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in _TRACKING_PARAMS
            ]
            query = urlencode(filtered)
        else:
            query = ""
        return urlunparse((scheme, p.netloc.lower(), p.path.rstrip("/"), p.params, query, ""))

    @staticmethod
    def normalize_url(url: str) -> str:
        """Public URL normalization used across pipeline components."""
        return ResearchTracker._normalize_url(url)

    @staticmethod
    def _normalize_domain(url: str) -> str:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc

    def _upgrade_action(self, key: str, new_action: str):
        current = self._actions.get(key)
        if current is None or _ACTION_RANK[new_action] > _ACTION_RANK[current]:
            self._actions[key] = new_action

    def record_search(
        self,
        query: str,
        num_results: int,
        domains: Optional[List[str]],
        results: list,
    ):
        from web_scout.models import SearchQuery, UrlEntry

        self._queries.append(
            SearchQuery(
                query=query,
                num_results_returned=num_results,
                domains_restricted=domains or [],
            )
        )
        for r in results:
            key = self._normalize_url(r.url)
            if key not in self._urls:
                self._urls[key] = UrlEntry(url=r.url, title=r.title, content=r.snippet)
            self._upgrade_action(key, "snippet_only")

    def record_direct_query(self, query: str) -> None:
        """Record a direct-URL run so result metadata stays consistent."""
        from web_scout.models import SearchQuery

        self._queries.append(
            SearchQuery(
                query=query,
                num_results_returned=1,
                domains_restricted=[],
            )
        )

    def record_scrape(self, url: str, title: str, extracted_content: str):
        from web_scout.models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "scraped")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = extracted_content
        if title:
            entry.title = title

    def record_scrape_failure(self, url: str, error: str):
        from web_scout.models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "scrape_failed")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = f"[scrape failed: {error}]"

    def record_blocked_by_policy(self, url: str, error: str):
        from web_scout.models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "blocked_by_policy")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = f"[blocked by policy: {error}]"

    def record_source_http_error(self, url: str, error: str):
        from web_scout.models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "source_http_error")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = f"[source http error: {error}]"

    def record_scraped_irrelevant(self, url: str, error: str):
        from web_scout.models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "scraped_irrelevant")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = f"[scraped but irrelevant: {error}]"

    def record_bot_detection(self, url: str, error: str):
        from web_scout.models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "bot_detected")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = f"[bot detection: {error}]"
        domain = self._normalize_domain(url)
        if domain:
            count = self._domain_bot_counts.get(domain, 0) + 1
            self._domain_bot_counts[domain] = count
            if count >= _BOT_BLOCK_THRESHOLD:
                self._bot_blocked_domains.add(domain)

    def build_result_groups(self) -> dict:
        """Group URLs by action: scraped, scrape_failed, bot_detected, snippet_only."""
        groups: Dict[str, list] = {
            "scraped": [],
            "scrape_failed": [],
            "blocked_by_policy": [],
            "source_http_error": [],
            "scraped_irrelevant": [],
            "bot_detected": [],
            "snippet_only": [],
        }
        for key, entry in self._urls.items():
            action = self._actions.get(key, "snippet_only")
            groups[action].append(entry)
        return groups

    def entries_for_action(self, action: str) -> list:
        """Return tracker entries for a specific action group."""
        return self.build_result_groups()[action]

    def count_for_action(self, action: str) -> int:
        """Return how many entries currently belong to an action group."""
        return len(self.entries_for_action(action))

    def action_for(self, url: str) -> Optional[str]:
        """Return the recorded action for a URL, if any."""
        return self._actions.get(self.normalize_url(url))

    def entry_for(self, url: str):
        """Return the tracked entry for a URL, if any."""
        return self._urls.get(self.normalize_url(url))

    def has_attempted_url(self, url: str) -> bool:
        """True when a URL has been scraped or failed previously."""
        return self.action_for(url) is not None

    def is_unscraped_candidate(self, url: str) -> bool:
        """True when a URL is new or only known from snippets."""
        action = self.action_for(url)
        return action in (None, "snippet_only")

    def cached_scrape_response(self, url: str) -> Optional[str]:
        """Return a user-facing cached response for previously seen URLs."""
        action = self.action_for(url)
        entry = self.entry_for(url)
        if action == "scraped" and entry:
            return f"[Already scraped — cached result] {entry.content[:800]}"
        if action in {
            "scrape_failed",
            "blocked_by_policy",
            "source_http_error",
            "scraped_irrelevant",
            "bot_detected",
        }:
            cached_msg = (entry.content or action) if entry else action
            return f"[Already attempted this URL — it failed: {cached_msg[:200]}. Move on to a different URL.]"
        if self.is_domain_bot_blocked(url):
            domain = self._normalize_domain(url)
            return (
                "[Skipped URL from domain blocked by bot protection earlier in this run: "
                f"{domain}. Move on to a different domain or source.]"
            )
        return None

    def is_domain_bot_blocked(self, url: str) -> bool:
        """True when the URL's domain crossed the bot-detection threshold this run."""
        domain = self._normalize_domain(url)
        return bool(domain) and domain in self._bot_blocked_domains

    def bot_blocked_domains(self) -> set[str]:
        """Return the set of domains blocked for the current run."""
        return set(self._bot_blocked_domains)

    def increment_empty(self, domains_key: str) -> int:
        """Increment and return the consecutive-empty count for a domain set."""
        count = self._consecutive_empty.get(domains_key, 0) + 1
        self._consecutive_empty[domains_key] = count
        return count

    def reset_empty(self, domains_key: str) -> None:
        """Reset the consecutive-empty count for a domain set."""
        self._consecutive_empty[domains_key] = 0

    @property
    def queries(self) -> list:
        return list(self._queries)
