"""Scrape-and-extract tool factory.

``create_scrape_and_extract_tool`` returns an async callable that:
1. Pre-fetches the page through the unified router.
2. Optionally calls ``scrape_linked_document`` once for a primary source
   document linked from a metadata page.
3. Reads the fetched content in an isolated extractor sub-agent context.
4. Extracts and summarises only the portions relevant to the query.
5. Returns a detailed, comprehensive excerpt (~5,000 chars) to the caller.
"""

import asyncio
import logging
import re
from typing import Any, Dict, Optional

from web_scout._extractor_contract import ExtractorOutcome
from web_scout.config import EXTRACTOR_HEURISTICS
from .extractor import ExtractorOutput, build_extractor_agent, run_with_retry
from .outcomes import build_failure_outcome, build_success_outcome, classify_failure_action
from .page_analysis import prefetched_is_recoverable, render_cached_page_text
from .rendering import extract_primary_rendered_content, infer_rendered_outcome
from .session_cache import get_or_fetch_session_source_artifact
from .tracker import ResearchTracker

logger = logging.getLogger(__name__)

_ERROR_TITLE_RE = re.compile(r"^Error[:\s\-]", re.IGNORECASE)


def create_scrape_and_extract_tool(
    extractor_model: Any,
    tracker: Optional[ResearchTracker] = None,
    query: str = "",
    max_concurrent: int = 6,
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
    max_pdf_pages: int = 50,
    max_content_chars: int = 30_000,
    use_session_cache: bool = False,
    max_interactive_clicks: int = EXTRACTOR_HEURISTICS.max_interactive_clicks,
    domain_expertise: Optional[str] = None,
):
    """Create a scrape_and_extract function."""
    semaphore = asyncio.Semaphore(max_concurrent)
    _doc_cache: dict = {}
    _doc_in_flight: Dict[str, asyncio.Future[str]] = {}
    in_flight: Dict[str, asyncio.Future[str]] = {}
    outcome_cache: Dict[str, ExtractorOutcome] = {}

    async def scrape_and_extract(
        url: str,
        wait_for: Optional[str] = None,
    ) -> str:
        """Scrape a URL and return a focused summary relevant to the research query.

        Internally validates the URL (skips 404s, auth walls, unsupported
        binary files) then runs a dedicated extractor sub-agent that fetches
        the page with the unified scraper and summarises the relevant content.

        Returns a detailed extraction of ~5,000 characters — never raw page content.

        Works with static HTML, JavaScript-rendered pages (SPAs, data portals),
        JSON endpoints, image URLs, PDFs, DOCX, PPTX, and XLSX.

        Args:
            url: The URL to scrape and extract from.
            wait_for: Optional CSS selector to wait for on JS-rendered pages.
                Only use for known data portals. Omit when in doubt.
        """
        norm = tracker.normalize_url(url) if tracker is not None else url

        if tracker is not None:
            if tracker.scrape_count >= 25:
                return (
                    "CIRCUIT BREAKER: You have scraped 25 URLs. "
                    "You MUST STOP scraping and synthesize your findings immediately."
                )
            cached_response = tracker.cached_scrape_response(url)
            if cached_response is not None:
                if norm not in outcome_cache:
                    outcome_cache[norm] = infer_rendered_outcome(url, cached_response)
                return cached_response

        existing = in_flight.get(norm)
        if existing is not None:
            try:
                return await asyncio.shield(existing)
            except Exception as e:
                return f"Failed to extract content from {url}: {e}"

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        in_flight[norm] = future

        try:
            async with semaphore:
                if tracker is not None:
                    tracker.scrape_count += 1

                _wait_for = wait_for if wait_for and wait_for.lower() not in ("null", "none", "") else None

                from web_scout.scraping import executor as scraping_executor
                from web_scout.scraping import plan as scraping_plan
                from web_scout.scraping import scrape_url
                from web_scout.scraping.page_classifier import looks_like_pdf_resource
                from web_scout.scraping.types import ScrapeStrategy, SourceArtifact

                page_rendered = ""
                is_failure = False

                if use_session_cache:
                    plan = await scraping_plan.build_scrape_plan(url, allowed_domains=allowed_domains)
                    if plan.strategy == ScrapeStrategy.SKIP:
                        page_rendered = f"[Scrape failed: Skipped: {plan.reason}]"
                        is_failure = True
                    else:
                        cached_artifact, cache_error = await get_or_fetch_session_source_artifact(
                            url=url,
                            strategy=plan.strategy,
                            wait_for=_wait_for,
                            vision_model=vision_model,
                            allowed_domains=allowed_domains,
                            max_pdf_pages=max_pdf_pages,
                            cache_pdf_pages=looks_like_pdf_resource(
                                url,
                                plan.content_type,
                                plan.content_disposition,
                            ),
                        )
                        if cache_error or cached_artifact is None:
                            page_rendered = f"[Scrape failed: {cache_error}]"
                            is_failure = True
                        else:
                            content, title, error = await scraping_executor.materialize_source_artifact(
                                SourceArtifact(
                                    kind=cached_artifact.artifact_kind,
                                    title=cached_artifact.title,
                                    text_content=cached_artifact.text_content,
                                    binary_bytes=cached_artifact.binary_bytes,
                                    mime_type=cached_artifact.mime_type,
                                ),
                                query=query,
                                vision_model=vision_model,
                                max_content_chars=max_content_chars,
                            )
                            if error:
                                page_rendered = f"[Scrape failed: {error}]"
                                is_failure = True
                            elif not content.strip():
                                page_rendered = "[Page returned empty content]"
                                is_failure = True
                            else:
                                page_rendered = render_cached_page_text(url, title, content)
                else:
                    content, title, error = await scrape_url(
                        url,
                        _wait_for,
                        query=query,
                        vision_model=vision_model,
                        allowed_domains=allowed_domains,
                        max_pdf_pages=max_pdf_pages,
                        max_content_chars=max_content_chars,
                    )
                    if error:
                        page_rendered = f"[Scrape failed: {error}]"
                        is_failure = True
                    elif not content.strip():
                        page_rendered = "[Page returned empty content]"
                        is_failure = True
                    else:
                        page_rendered = render_cached_page_text(url, title, content)

                if is_failure:
                    outcome = _handle_failure(url, page_rendered, tracker, outcome_cache, norm)
                    future.set_result(outcome.rendered_text)
                    return outcome.rendered_text

                extractor_agent, extractor_cleanup = build_extractor_agent(
                    extractor_model,
                    query,
                    url,
                    _wait_for,
                    vision_model=vision_model,
                    allowed_domains=allowed_domains,
                    max_pdf_pages=max_pdf_pages,
                    max_content_chars=max_content_chars,
                    doc_cache=_doc_cache,
                    doc_in_flight=_doc_in_flight,
                    use_session_cache=use_session_cache,
                    max_interactive_clicks=max_interactive_clicks,
                    domain_expertise=domain_expertise,
                    pre_fetched_content=page_rendered,
                )

                input_text = (
                    f"Research query: {query}\n"
                    f"URL: {url}\n\n"
                    f"Here is the page content that has already been fetched for you:\n"
                    f"==== PAGE CONTENT ====\n"
                    f"{page_rendered}\n"
                    f"======================\n\n"
                    f"Please extract the relevant content."
                )

                try:
                    result = await run_with_retry(
                        extractor_agent,
                        input_text,
                        max_turns=EXTRACTOR_HEURISTICS.max_extractor_turns,
                    )
                    output = result.final_output_as(ExtractorOutput)
                except Exception as e:
                    logger.error(
                        "[extract] sub-agent failed for %s (type=%s): %s",
                        url,
                        type(e).__name__,
                        e,
                    )
                    if prefetched_is_recoverable(page_rendered):
                        recovered_content = (
                            "[Extractor failed after successful scrape; using pre-fetched content]\n"
                            + extract_primary_rendered_content(page_rendered)[:4500]
                        )
                        if tracker is not None:
                            tracker.record_scrape(url, "", recovered_content)
                        outcome = build_success_outcome(
                            url=url,
                            title="",
                            content=recovered_content,
                            page_type="content",
                            links=[],
                            count_scraped=tracker.count_for_action("scraped") if tracker is not None else None,
                        )
                        logger.info("[extract] extractor_outcome status=success recovery=true url=%s", url)
                    else:
                        failure_text = (
                            f"[Extractor failed after scrape and pre-fetched content was not recoverable: "
                            f"{type(e).__name__}: {e}]"
                        )
                        if tracker is not None:
                            tracker.record_scrape_failure(url, failure_text)
                        outcome = build_failure_outcome(
                            url=url,
                            content=failure_text,
                            count_scraped=tracker.count_for_action("scraped") if tracker is not None else None,
                            failure_kind="subagent_failed",
                        )
                        logger.info(
                            "[extract] extractor_outcome status=failure failure_kind=subagent_failed url=%s", url
                        )
                    outcome_cache[norm] = outcome
                    future.set_result(outcome.rendered_text)
                    return outcome.rendered_text
                finally:
                    await extractor_cleanup()

                content = output.relevant_content.strip()
                title = output.title.strip()
                links = output.relevant_links

                _content_lower = content.lower()
                _short = len(content) < EXTRACTOR_HEURISTICS.failure_short_content_chars
                is_failure = (
                    not content
                    or content.startswith("[No relevant content")
                    or content.startswith("[Scrape failed")
                    or content.startswith("Scrape failed")
                    or content.startswith("[Page returned empty")
                    or (_ERROR_TITLE_RE.match(title) and _short)
                    or ("is not valid" in _content_lower and _short)
                    or ("could not access" in _content_lower and _short)
                    or ("not found" in _content_lower and ("404" in content or "http" in _content_lower) and _short)
                    or ("page not found" in _content_lower and _short)
                    or ("access denied" in _content_lower and _short)
                    or (
                        "403" in content
                        and ("forbidden" in _content_lower or "error" in _content_lower or "skipped" in _content_lower)
                        and _short
                    )
                    or ("skipped" in _content_lower and "http" in _content_lower and _short)
                )

                if is_failure:
                    outcome = _handle_failure(url, content or "", tracker, outcome_cache, norm)
                    future.set_result(outcome.rendered_text)
                    return outcome.rendered_text

                if tracker is not None:
                    tracker.record_scrape(url, title, content)
                count_scraped = tracker.count_for_action("scraped") if tracker is not None else None
                outcome = build_success_outcome(
                    url=url,
                    title=title,
                    content=content,
                    page_type=output.page_type,
                    links=links,
                    count_scraped=count_scraped,
                )
                outcome_cache[norm] = outcome
                logger.info(
                    "[extract] extractor_outcome status=success page_type=%s links=%d url=%s",
                    outcome.page_type,
                    len(outcome.relevant_links),
                    url,
                )
                future.set_result(outcome.rendered_text)
                return outcome.rendered_text

        except Exception as exc:
            logger.error(
                "[extract] unexpected scrape failure for %s (type=%s): %s",
                url,
                type(exc).__name__,
                exc,
            )
            failure_text = f"[Scrape failed: internal error: {type(exc).__name__}: {exc}]"
            if tracker is not None:
                tracker.record_scrape_failure(url, failure_text)
            count_scraped = tracker.count_for_action("scraped") if tracker is not None else None
            outcome = build_failure_outcome(
                url=url,
                content=failure_text,
                count_scraped=count_scraped,
                failure_kind="scrape_failed",
            )
            outcome_cache[norm] = outcome
            if not future.done():
                future.set_result(outcome.rendered_text)
            return outcome.rendered_text
        finally:
            if not future.done():
                future.cancel()
            in_flight.pop(norm, None)

    setattr(scrape_and_extract, "_outcome_cache", outcome_cache)
    return scrape_and_extract


def _handle_failure(
    url: str,
    content: str,
    tracker: Optional[ResearchTracker],
    outcome_cache: dict,
    norm: str,
) -> ExtractorOutcome:
    """Record a scrape failure in the tracker and build the failure outcome."""
    action = classify_failure_action(content)
    if action in {"scraped_irrelevant", "blocked_by_policy"}:
        logger.debug("[extract] %s %s", action, url)
    elif action in {"source_http_error", "bot_detected"}:
        logger.info("[extract] %s %s", action, url)
    else:
        logger.info("[extract] scrape_failed %s", url)

    if tracker is not None:
        _record_by_action(tracker, action, url, content)

    count_scraped = tracker.count_for_action("scraped") if tracker is not None else None
    outcome = build_failure_outcome(url=url, content=content, count_scraped=count_scraped, failure_kind=action)
    outcome_cache[norm] = outcome
    logger.info("[extract] extractor_outcome status=failure failure_kind=%s url=%s", action, url)
    return outcome


def _record_by_action(tracker: ResearchTracker, action: str, url: str, content: str) -> None:
    if action == "bot_detected":
        tracker.record_bot_detection(url, content)
    elif action == "blocked_by_policy":
        tracker.record_blocked_by_policy(url, content)
    elif action == "source_http_error":
        tracker.record_source_http_error(url, content)
    elif action == "scraped_irrelevant":
        tracker.record_scraped_irrelevant(url, content)
    else:
        tracker.record_scrape_failure(url, content or "empty extraction")
