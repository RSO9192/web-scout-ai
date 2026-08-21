"""Query-focused PDF extraction with physical page citations.

Short PDFs (markdown length <= ``short_pdf_max_chars``) use one LLM call on the
full bannered markdown. Long PDFs use a hierarchical path: document summary,
per-section evidence extraction, then a final answer call. An optional LLM
claim-verification pass is off by default.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

import litellm
from pydantic import BaseModel, Field

from web_scout.config import ROUTING_HEURISTICS
from web_scout.scraping.types import PdfDocumentLayout, SourceArtifact

logger = logging.getLogger(__name__)

_EVIDENCE_CONCURRENCY = 3
_NO_RELEVANT = "[No relevant content found for this query]"
_PAGE_START_RE = re.compile(r"^========== page (\d+) start ==========$")


class PdfEvidenceItem(BaseModel):
    text: str = Field(description="Evidence snippet relevant to the research query.")
    page_start: int = Field(description="First physical page of the evidence.")
    page_end: int = Field(description="Last physical page of the evidence.")


class PdfExtractResult(BaseModel):
    relevant_content: str = Field(
        description=(
            "Narrative extract answering the research query. Tag each fact with "
            "[pp. 3–7] or [p. 3] using physical page numbers from the source."
        )
    )
    evidence: list[PdfEvidenceItem] = Field(default_factory=list)


class _DocumentSummary(BaseModel):
    summary: str = Field(
        description="Short scope, definitions, methodology, and structure summary."
    )


class _ClaimVerdict(BaseModel):
    claim_index: int
    supported: bool


class _ClaimVerification(BaseModel):
    verdicts: list[_ClaimVerdict] = Field(default_factory=list)


def format_page_span(pages: list[int]) -> str:
    """Compact page list into ``p. 3`` / ``pp. 3–7, 12`` form (no title)."""
    if not pages:
        return ""
    unique = sorted(set(pages))
    ranges: list[tuple[int, int]] = []
    start = prev = unique[0]
    for page in unique[1:]:
        if page == prev + 1:
            prev = page
            continue
        ranges.append((start, prev))
        start = prev = page
    ranges.append((start, prev))

    parts: list[str] = []
    for lo, hi in ranges:
        if lo == hi:
            parts.append(str(lo))
        else:
            parts.append(f"{lo}–{hi}")
    joined = ", ".join(parts)
    if len(unique) == 1:
        return f"p. {joined}"
    if len(ranges) == 1 and ranges[0][0] != ranges[0][1]:
        return f"pp. {joined}"
    if all(lo == hi for lo, hi in ranges) and len(ranges) > 1:
        return f"pp. {joined}"
    return f"pp. {joined}"


def format_reference(title: str, pages: list[int]) -> str:
    """Build ``Title, pp. 3–7`` (or title-only when no pages)."""
    title = (title or "").strip()
    span = format_page_span(pages)
    if title and span:
        return f"{title}, {span}"
    return title or span


def valid_layout_pages(layout: PdfDocumentLayout) -> set[int]:
    return {page.page for page in layout.pages}


def filter_evidence_by_layout(
    evidence: list[PdfEvidenceItem],
    layout: PdfDocumentLayout,
) -> list[PdfEvidenceItem]:
    pages = valid_layout_pages(layout)
    if not pages:
        return list(evidence)
    kept: list[PdfEvidenceItem] = []
    for item in evidence:
        if item.page_start in pages and item.page_end in pages and item.page_start <= item.page_end:
            kept.append(item)
    return kept


def pages_from_evidence(evidence: list[PdfEvidenceItem]) -> list[int]:
    pages: list[int] = []
    for item in evidence:
        pages.extend(range(item.page_start, item.page_end + 1))
    return sorted(set(pages))


def _slice_markdown(markdown: str, start_line: int, end_line: int) -> str:
    lines = markdown.splitlines()
    start = max(1, start_line) - 1
    end = min(len(lines), end_line)
    return "\n".join(lines[start:end])


def _split_oversized_section_markdown(section_md: str, max_chars: int) -> list[str]:
    """Split a single oversized section on page-start banners."""
    if len(section_md) <= max_chars:
        return [section_md]
    lines = section_md.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    for line in lines:
        if _PAGE_START_RE.match(line.strip()) and current and len("\n".join(current)) >= max_chars // 2:
            chunks.append("\n".join(current).strip())
            current = [line]
            continue
        current.append(line)
        if len("\n".join(current)) >= max_chars and _PAGE_START_RE.match(line.strip()):
            # keep banner with next chunk; already handled above
            pass
    if current:
        chunks.append("\n".join(current).strip())
    # If still oversized (no page banners), hard-split by chars.
    final: list[str] = []
    for chunk in chunks or [section_md]:
        if len(chunk) <= max_chars:
            final.append(chunk)
            continue
        for i in range(0, len(chunk), max_chars):
            final.append(chunk[i : i + max_chars])
    return [c for c in final if c.strip()]


def pack_section_chunks(
    markdown: str,
    layout: PdfDocumentLayout,
    *,
    max_chars: int,
) -> list[tuple[str, str, str]]:
    """Pack consecutive sections up to ``max_chars``.

    Returns list of ``(heading_path, titles_joined, chunk_markdown)``.
    """
    sections = list(layout.sections)
    if not sections:
        return [("", layout.document_title or "", markdown)]

    packed: list[tuple[str, str, str]] = []
    buf_parts: list[str] = []
    buf_titles: list[str] = []
    buf_paths: list[str] = []
    buf_len = 0

    def _flush() -> None:
        nonlocal buf_parts, buf_titles, buf_paths, buf_len
        if not buf_parts:
            return
        path = " > ".join(buf_paths[-1].split(" > ")) if buf_paths else ""
        if buf_paths:
            path = buf_paths[0] if len(buf_paths) == 1 else " | ".join(buf_paths)
        packed.append((path, " / ".join(buf_titles), "\n\n".join(buf_parts)))
        buf_parts, buf_titles, buf_paths, buf_len = [], [], [], 0

    for section in sections:
        section_md = _slice_markdown(markdown, section.start_line, section.end_line).strip()
        if not section_md:
            continue
        path = " > ".join(section.heading_path) if section.heading_path else section.title
        title = section.title or path or "Section"
        if len(section_md) > max_chars:
            _flush()
            for piece in _split_oversized_section_markdown(section_md, max_chars):
                packed.append((path, title, piece))
            continue
        extra = len(section_md) + (2 if buf_parts else 0)
        if buf_parts and buf_len + extra > max_chars:
            _flush()
        buf_parts.append(section_md)
        buf_titles.append(title)
        buf_paths.append(path)
        buf_len += extra
    _flush()
    return packed


async def _llm_json(model: Any, prompt: str, schema: type[BaseModel]) -> BaseModel:
    response = await litellm.acompletion(
        model=model if isinstance(model, str) else getattr(model, "model", None) or str(model),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return schema.model_validate(json.loads(raw))


def _model_name(model: Any) -> str:
    if isinstance(model, str):
        return model
    return getattr(model, "model", None) or str(model)


async def _summarize_document(
    *,
    model: Any,
    layout: PdfDocumentLayout,
) -> str:
    outline_lines = []
    for section in layout.sections:
        path = " > ".join(section.heading_path) if section.heading_path else section.title
        pages = ""
        if section.page_start is not None:
            if section.page_end is None or section.page_end == section.page_start:
                pages = f" p. {section.page_start}"
            else:
                pages = f" pp. {section.page_start}–{section.page_end}"
        outline_lines.append(f"- [L{section.level}] {path or '(untitled)'}{pages}")
    prompt = (
        f"Document title: {layout.document_title}\n\n"
        "Section outline:\n"
        + ("\n".join(outline_lines) if outline_lines else "(no sections)")
        + "\n\n"
        "Write a short document-level summary covering scope, definitions, "
        "methodology, and structure. Do not invent facts beyond the outline. "
        'Return JSON: {"summary": "..."}'
    )
    result = await _llm_json(model, prompt, _DocumentSummary)
    assert isinstance(result, _DocumentSummary)
    return result.summary.strip()


async def _extract_chunk_evidence(
    *,
    model: Any,
    query: str,
    document_summary: str,
    heading_path: str,
    chunk_markdown: str,
) -> list[PdfEvidenceItem]:
    prompt = (
        f"Research query: {query}\n\n"
        f"Document context summary:\n{document_summary}\n\n"
        f"Section heading path: {heading_path or '(none)'}\n\n"
        "Section markdown (with physical page banners "
        "'========== page N start/end =========='):\n"
        f"{chunk_markdown}\n\n"
        "Extract ONLY query-relevant evidence from this section. "
        "Do NOT write a section answer. For each evidence item, set page_start "
        "and page_end to physical page numbers from the banners. "
        'Return JSON: {"evidence":[{"text":"...","page_start":1,"page_end":1}]}'
    )

    class _ChunkEvidence(BaseModel):
        evidence: list[PdfEvidenceItem] = Field(default_factory=list)

    try:
        result = await _llm_json(model, prompt, _ChunkEvidence)
        assert isinstance(result, _ChunkEvidence)
        return result.evidence
    except Exception as exc:
        logger.warning("[pdf-extract] chunk evidence failed: %s", exc)
        return []


async def _final_answer_from_evidence(
    *,
    model: Any,
    query: str,
    document_title: str,
    evidence: list[PdfEvidenceItem],
) -> PdfExtractResult:
    payload = [
        {"text": item.text, "page_start": item.page_start, "page_end": item.page_end}
        for item in evidence
    ]
    prompt = (
        f"Research query: {query}\n"
        f"Document title: {document_title}\n\n"
        f"Evidence items:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Answer the research query using ONLY these evidence items. "
        "Tag each fact with [pp. X–Y] or [p. X] matching the evidence pages. "
        "Return JSON matching "
        '{"relevant_content":"...","evidence":[{"text":"...","page_start":1,"page_end":1}]} '
        "where evidence is the subset you relied on."
    )
    result = await _llm_json(model, prompt, PdfExtractResult)
    assert isinstance(result, PdfExtractResult)
    return result


async def _short_path_extract(
    *,
    model: Any,
    query: str,
    document_title: str,
    markdown: str,
) -> PdfExtractResult:
    prompt = (
        f"Research query: {query}\n"
        f"Document title: {document_title}\n\n"
        "Full PDF markdown with physical page banners "
        "('========== page N start/end =========='):\n"
        f"{markdown}\n\n"
        "Extract all facts that answer the research query. "
        "Every fact MUST cite physical pages present in the markdown using "
        "[pp. X–Y] or [p. X]. Do not invent pages. "
        "Return JSON matching "
        '{"relevant_content":"...","evidence":[{"text":"...","page_start":1,"page_end":1}]}'
    )
    result = await _llm_json(model, prompt, PdfExtractResult)
    assert isinstance(result, PdfExtractResult)
    return result


async def _verify_claims_llm(
    *,
    model: Any,
    relevant_content: str,
    evidence: list[PdfEvidenceItem],
) -> PdfExtractResult:
    prompt = (
        "Verify whether each factual claim in relevant_content is supported by "
        "the cited evidence snippets. Return JSON "
        '{"verdicts":[{"claim_index":0,"supported":true}]}.\n\n'
        f"relevant_content:\n{relevant_content}\n\n"
        f"evidence:\n{json.dumps([e.model_dump() for e in evidence], ensure_ascii=False, indent=2)}"
    )
    try:
        result = await _llm_json(model, prompt, _ClaimVerification)
        assert isinstance(result, _ClaimVerification)
    except Exception as exc:
        logger.warning("[pdf-extract] claim verification failed: %s", exc)
        return PdfExtractResult(relevant_content=relevant_content, evidence=evidence)

    unsupported = {v.claim_index for v in result.verdicts if not v.supported}
    if not unsupported:
        return PdfExtractResult(relevant_content=relevant_content, evidence=evidence)
    # Drop unsupported evidence indices when they align; keep narrative trimmed lightly.
    kept_evidence = [item for i, item in enumerate(evidence) if i not in unsupported]
    if not kept_evidence:
        return PdfExtractResult(relevant_content=_NO_RELEVANT, evidence=[])
    return PdfExtractResult(relevant_content=relevant_content, evidence=kept_evidence)


async def extract_pdf_for_query(
    *,
    artifact: SourceArtifact,
    query: str,
    model: Any,
    short_pdf_max_chars: int = ROUTING_HEURISTICS.short_pdf_max_chars,
    verify_pdf_claims: bool = ROUTING_HEURISTICS.verify_pdf_claims,
) -> tuple[str, str, list[int]]:
    """Extract query-focused PDF content with page references.

    Returns ``(title, relevant_content, used_pages)``.
    """
    layout = artifact.layout
    markdown = artifact.text_content or ""
    title = (layout.document_title if layout else "") or artifact.title or "Document"
    if not markdown.strip() or layout is None:
        return title, _NO_RELEVANT, []

    try:
        if len(markdown) <= short_pdf_max_chars:
            result = await _short_path_extract(
                model=model,
                query=query,
                document_title=title,
                markdown=markdown,
            )
        else:
            summary = await _summarize_document(model=model, layout=layout)
            chunks = pack_section_chunks(markdown, layout, max_chars=short_pdf_max_chars)
            semaphore = asyncio.Semaphore(_EVIDENCE_CONCURRENCY)

            async def _one(chunk: tuple[str, str, str]) -> list[PdfEvidenceItem]:
                heading_path, _titles, chunk_md = chunk
                async with semaphore:
                    return await _extract_chunk_evidence(
                        model=model,
                        query=query,
                        document_summary=summary,
                        heading_path=heading_path,
                        chunk_markdown=chunk_md,
                    )

            evidence_lists = await asyncio.gather(*[_one(chunk) for chunk in chunks])
            all_evidence = [item for group in evidence_lists for item in group]
            all_evidence = filter_evidence_by_layout(all_evidence, layout)
            if not all_evidence:
                return title, _NO_RELEVANT, []
            result = await _final_answer_from_evidence(
                model=model,
                query=query,
                document_title=title,
                evidence=all_evidence,
            )
    except Exception as exc:
        logger.error("[pdf-extract] extraction failed: %s", exc)
        return title, f"[Scrape failed: PDF extraction error: {exc}]", []

    evidence = filter_evidence_by_layout(result.evidence, layout)
    if verify_pdf_claims:
        verified = await _verify_claims_llm(
            model=model,
            relevant_content=result.relevant_content,
            evidence=evidence,
        )
        evidence = filter_evidence_by_layout(verified.evidence, layout)
        content = verified.relevant_content.strip() or _NO_RELEVANT
    else:
        content = result.relevant_content.strip() or _NO_RELEVANT

    if not evidence and content != _NO_RELEVANT:
        # Keep content but pages unknown → no page span in reference.
        return title, content, []
    if not evidence:
        return title, _NO_RELEVANT, []

    used_pages = pages_from_evidence(evidence)
    return title, content, used_pages
