"""Document scraping strategy (private): PDF, DOCX, PPTX, and XLSX via docling.

Single entry point: ``scrape_document``.

PDF handling uses Docling's layout pipeline for structure (sections, tables,
picture locations) and optionally enriches figure placeholders with vision-model
summaries.  Scanned / image-only PDFs (thin text layer) return a *binary*
``SourceArtifact`` so that the caller can optionally apply vision extraction via
``materialize_parse_result``.

Office documents (DOCX, PPTX, XLSX) are converted by docling's default pipeline
via URL fetch.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Optional, Tuple

from web_scout.config import ROUTING_HEURISTICS

from ._download import download_pdf
from ._markdown import append_links
from .page_classifier import looks_like_pdf_resource
from .types import PdfDocumentLayout, PdfPageSpan, PdfSectionSpan, SourceArtifact
from .utils import unsupported_legacy_document_reason

logger = logging.getLogger(__name__)

_PDF_CONVERTER = None
# Protect both lazy converter creation and use. Docling's native PDF pipeline
# can segfault when conversions overlap in threads.
_PDF_LOCK = threading.Lock()

_IMAGE_PLACEHOLDER = "<!-- image -->"
_VISUAL_TOKEN_RE = re.compile(r"<!--\s*visual:([^\s>]+)\s*-->")
_ANY_IMAGE_PLACEHOLDER_RE = re.compile(r"<!--\s*(?:image|visual:[^>]+)\s*-->")
_PAGE_START_RE = re.compile(r"^========== page (\d+) start ==========$")
_PAGE_END_RE = re.compile(r"^========== page (\d+) end ==========$")
_PAGE_BANNER_RE = re.compile(r"^========== page \d+ (?:start|end) ==========$", re.MULTILINE)
_MIN_VISUAL_SIDE_PX = 80
_VISUAL_ENRICH_CONCURRENCY = 3


def _page_start_banner(page: int) -> str:
    return f"========== page {page} start =========="


def _page_end_banner(page: int) -> str:
    return f"========== page {page} end =========="


@dataclass
class _PageBannerState:
    """Tracks open page banners across section serialization."""

    current_page: int | None = None
    open: bool = False

    def transition(self, page: int | None) -> list[str]:
        """Return banner lines needed before emitting content on ``page``."""
        if page is None:
            return []
        if self.current_page == page and self.open:
            return []
        parts: list[str] = []
        if self.open and self.current_page is not None:
            parts.append(_page_end_banner(self.current_page))
        parts.append(_page_start_banner(page))
        self.current_page = page
        self.open = True
        return parts

    def close(self) -> list[str]:
        if not self.open or self.current_page is None:
            return []
        banner = _page_end_banner(self.current_page)
        self.open = False
        return [banner]


@dataclass(frozen=True)
class _HeadingAnchor:
    """Page/y position of a section heading, used to attach floating figures."""

    section_index: int
    page: int
    top: float


@dataclass
class PdfVisual:
    """A graphical element extracted from a PDF section."""

    visual_id: str
    png_bytes: bytes
    page: int | None = None
    caption: str = ""
    width: int = 0
    height: int = 0
    bbox: str = "unknown"


@dataclass
class PdfSection:
    """One structural section of a Docling-converted PDF."""

    index: int
    title: str
    level: int
    page_start: int | None
    page_end: int | None
    markdown: str
    visuals: list[PdfVisual] = field(default_factory=list)


def _get_pdf_converter():
    """Return the shared Docling PDF converter.

    Reusing the same ``DocumentConverter`` lets Docling reuse its initialised
    pipeline and heavy layout model across PDF conversion calls. Production
    callers hold ``_PDF_LOCK`` across both this initialization and conversion.

    Layout inference is pinned to CPU: Apple MPS aborts inside RT-DETR resize
    (``data layout to resample should be nchw or nhwc``).
    """
    global _PDF_CONVERTER
    if _PDF_CONVERTER is None:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        opts = PdfPipelineOptions(
            do_ocr=False,
            do_table_structure=True,
            generate_page_images=True,
            generate_picture_images=True,
            images_scale=2.0,
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
        )
        _PDF_CONVERTER = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _PDF_CONVERTER


async def _resolve_is_pdf(url: str, content_type: str, content_disposition: str, *, needs_browser: bool = False) -> bool:
    """Return True when the URL is confirmed to serve a PDF.

    Uses known content-type / content-disposition metadata when available;
    falls back to a GET request, then to extension sniffing.  When
    ``needs_browser`` is True the fallback GET uses ``StealthyFetcher`` to
    bypass bot-walls instead of ``AsyncFetcher``.
    """
    if looks_like_pdf_resource(url, content_type, content_disposition):
        return True
    if content_type or content_disposition:
        return False  # already resolved from headers — not a PDF
    try:
        if needs_browser:
            from ._scrapling import stealthy_fetch

            resp = await stealthy_fetch(
                url,
                headless=True,
                network_idle=True,
                solve_cloudflare=True,
                timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
            )
        else:
            from scrapling.fetchers import AsyncFetcher

            resp = await AsyncFetcher.get(
                url,
                stealthy_headers=True,
                follow_redirects=True,
                timeout=ROUTING_HEURISTICS.validation_timeout,
            )
        return looks_like_pdf_resource(
            url,
            resp.headers.get("content-type", ""),
            resp.headers.get("content-disposition", ""),
        )
    except Exception:
        return url.lower().split("?")[0].endswith(".pdf")


def _page_nos(item) -> list[int]:
    return [p.page_no for p in (getattr(item, "prov", None) or []) if getattr(p, "page_no", None) is not None]


def _page_height(document, page_no: int | None) -> float | None:
    if page_no is None or not getattr(document, "pages", None):
        return None
    page = document.pages.get(page_no)
    size = getattr(page, "size", None) if page is not None else None
    return getattr(size, "height", None)


def _page_width(document, page_no: int | None) -> float | None:
    if page_no is None or not getattr(document, "pages", None):
        return None
    page = document.pages.get(page_no)
    size = getattr(page, "size", None) if page is not None else None
    return getattr(size, "width", None)


def _top_left_bbox(item, document) -> tuple[int, object] | None:
    """Return ``(page_no, bbox)`` with top-left origin, or None."""
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    first = prov[0]
    page_no = getattr(first, "page_no", None)
    bbox = getattr(first, "bbox", None)
    if page_no is None or bbox is None:
        return None
    height = _page_height(document, page_no)
    if height is not None and hasattr(bbox, "to_top_left_origin"):
        bbox = bbox.to_top_left_origin(height)
    return page_no, bbox


def _format_bbox(item, document) -> str:
    located = _top_left_bbox(item, document)
    if located is None:
        return "unknown"
    _page_no, bbox = located
    origin = getattr(getattr(bbox, "coord_origin", None), "value", "TOPLEFT")
    return f"l={bbox.l:.1f}, t={bbox.t:.1f}, r={bbox.r:.1f}, b={bbox.b:.1f} ({origin})"


def _is_significant_picture(pil_image) -> bool:
    if pil_image is None:
        return False
    width, height = pil_image.size
    return min(width, height) >= _MIN_VISUAL_SIDE_PX


def _section_index_for_picture(anchors: list[_HeadingAnchor], page: int, top: float) -> int:
    """Attach a figure to the nearest heading above it on the same page.

    Later headings on earlier pages must not steal the figure (two-column
    reading order often emits those headings after the figure).
    """
    if not anchors:
        return 0
    same_page = [a for a in anchors if a.page == page and a.top <= top + 1.0]
    if same_page:
        return max(same_page, key=lambda a: a.top).section_index
    previous = [a for a in anchors if a.page < page]
    if previous:
        return max(previous, key=lambda a: (a.page, a.top)).section_index
    return anchors[0].section_index


def _item_page_xy(item, document) -> tuple[int, float, float] | None:
    """Return ``(page, top, left)`` in top-left page coordinates, if known."""
    located = _top_left_bbox(item, document)
    if located is None:
        return None
    page_no, bbox = located
    return int(page_no), float(bbox.t), float(getattr(bbox, "l", 0.0))


def _column_centers(lefts: list[float], page_width: float) -> list[float]:
    """Cluster item left-edges into at most three column anchors."""
    if page_width <= 0 or not lefts:
        return [0.0]
    xs = sorted(lefts)
    min_gap = page_width * 0.12
    clusters: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] > min_gap and len(clusters) < 3:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _column_count(lefts: list[float], page_width: float) -> int:
    return len(_column_centers(lefts, page_width))


def _column_index(left: float, centers: list[float]) -> int:
    if not centers:
        return 0
    return min(range(len(centers)), key=lambda i: abs(left - centers[i]))


def _layout_sort_key(page: int, col: int, top: float, left: float = 0.0, seq: int = 0, idx: int = 0) -> tuple:
    """Newspaper order: physical page, left-to-right column, then top-to-bottom."""
    return (page, col, top, left, seq, idx)


def _bbox_contained(inner, outer, slack: float = 12.0) -> bool:
    return (
        float(inner.l) >= float(outer.l) - slack
        and float(inner.t) >= float(outer.t) - slack
        and float(inner.r) <= float(outer.r) + slack
        and float(inner.b) <= float(outer.b) + slack
    )


def _markdown_from_section_items(
    document,
    bucket: list[tuple[object, int]],
    picture_boxes: list[tuple[int, object]] | None = None,
    *,
    page_state: _PageBannerState | None = None,
    visual_tokens_by_item: dict[int, str] | None = None,
) -> str:
    """Serialize column-grouped items with page banners. Skip groups and in-figure labels."""
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams
    from docling_core.types.doc import GroupItem, ImageRefMode, PictureItem, SectionHeaderItem, TitleItem

    serializer = MarkdownDocSerializer(
        doc=document,
        params=MarkdownParams(
            image_mode=ImageRefMode.PLACEHOLDER,
            image_placeholder=_IMAGE_PLACEHOLDER,
        ),
    )
    boxes = picture_boxes or []
    state = page_state or _PageBannerState()
    tokens_by_item = visual_tokens_by_item or {}
    parts: list[str] = []
    for item_idx, (item, _level) in enumerate(bucket):
        if isinstance(item, GroupItem):
            continue
        located = _top_left_bbox(item, document)
        page_no = located[0] if located is not None else (_page_nos(item)[0] if _page_nos(item) else None)
        for banner in state.transition(page_no):
            parts.append(banner)

        if isinstance(item, PictureItem):
            token = tokens_by_item.get(item_idx)
            if token:
                parts.append(token)
            continue

        if located is not None and not isinstance(item, (SectionHeaderItem, TitleItem)):
            page_no, bbox = located
            if any(p == page_no and _bbox_contained(bbox, outer) for p, outer in boxes):
                continue
        try:
            text = (serializer.serialize(item=item).text or "").strip()
        except Exception:
            text = (getattr(item, "text", None) or "").strip()
        if not text:
            continue
        text = _ANY_IMAGE_PLACEHOLDER_RE.sub("", text).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _png_bytes_from_pil(pil_image) -> bytes:
    image = pil_image.copy()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


_CROP_RENDER_SCALE = 2.0


def _crop_region_from_pdf(
    pdf,
    *,
    page_no: int,
    bbox,
    doc_page_size,
) -> bytes | None:
    """Crop ``bbox`` (top-left page coords) from a pypdfium2 document. ``page_no`` is 1-based."""
    index = page_no - 1
    if index < 0 or index >= len(pdf):
        return None
    page = pdf[index]
    try:
        pdf_w, pdf_h = page.get_size()
        doc_w = getattr(doc_page_size, "width", None) or pdf_w
        doc_h = getattr(doc_page_size, "height", None) or pdf_h
        x_scale = pdf_w / doc_w if doc_w else 1.0
        y_scale = pdf_h / doc_h if doc_h else 1.0
        left = float(bbox.l) * x_scale
        top = float(bbox.t) * y_scale
        right = float(bbox.r) * x_scale
        bottom = float(bbox.b) * y_scale
        bitmap = page.render(scale=_CROP_RENDER_SCALE)
        pil = bitmap.to_pil()
        pixel = (
            max(0, int(left * _CROP_RENDER_SCALE)),
            max(0, int(top * _CROP_RENDER_SCALE)),
            min(pil.width, int(right * _CROP_RENDER_SCALE)),
            min(pil.height, int(bottom * _CROP_RENDER_SCALE)),
        )
        if pixel[2] <= pixel[0] or pixel[3] <= pixel[1]:
            return None
        return _png_bytes_from_pil(pil.crop(pixel))
    finally:
        page.close()


def _format_visual_block(visual: PdfVisual, summary: str, vision_model: str) -> str:
    page = visual.page if visual.page is not None else "unknown"
    return (
        f"[Summarized Image: {vision_model}]\n"
        f"page: {page}\n"
        f"bounding box: {visual.bbox}\n"
        f"Summary:\n{summary.strip()}"
    )


def _label_section_placeholders(markdown: str, section_index: int) -> tuple[str, list[str]]:
    """Replace ``<!-- image -->`` placeholders with unique visual tokens in order."""
    visual_ids: list[str] = []
    counter = 0

    def _replace(_match: re.Match[str]) -> str:
        nonlocal counter
        visual_id = f"s{section_index}-v{counter}"
        visual_ids.append(visual_id)
        counter += 1
        return f"<!-- visual:{visual_id} -->"

    labeled = re.sub(re.escape(_IMAGE_PLACEHOLDER), _replace, markdown)
    return labeled, visual_ids


def _apply_visual_summaries(
    markdown: str,
    summaries: dict[str, str],
    *,
    visuals: list[PdfVisual] | None = None,
    vision_model: str = "",
) -> str:
    """Replace labeled visual tokens with model summaries when available."""
    by_id = {visual.visual_id: visual for visual in (visuals or [])}

    def _replace(match: re.Match[str]) -> str:
        visual_id = match.group(1)
        summary = summaries.get(visual_id)
        if not summary:
            return match.group(0)
        visual = by_id.get(visual_id)
        if visual is None or not vision_model:
            return summary.strip()
        return _format_visual_block(visual, summary, vision_model)

    return _VISUAL_TOKEN_RE.sub(_replace, markdown)


def _text_without_image_placeholders(markdown: str) -> str:
    text = _ANY_IMAGE_PLACEHOLDER_RE.sub("", markdown)
    text = _PAGE_BANNER_RE.sub("", text)
    return text.strip()


def _filename_title(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?")[0] or "Document"


def _infer_document_title(sections: list[PdfSection], fallback: str) -> str:
    titled = [s for s in sections if (s.title or "").strip()]
    if not titled:
        return fallback
    min_level = min(s.level for s in titled)
    for section in titled:
        if section.level == min_level:
            return section.title.strip()
    return fallback


def _heading_paths(sections: list[PdfSection]) -> list[tuple[str, ...]]:
    stack: list[tuple[int, str]] = []
    paths: list[tuple[str, ...]] = []
    for section in sections:
        title = (section.title or "").strip()
        if not title:
            paths.append(tuple(t for _, t in stack))
            continue
        while stack and stack[-1][0] >= section.level:
            stack.pop()
        stack.append((section.level, title))
        paths.append(tuple(t for _, t in stack))
    return paths


def _pages_for_line_range(pages: list[PdfPageSpan], start_line: int, end_line: int) -> tuple[int | None, int | None]:
    overlapping = [p for p in pages if p.start_line <= end_line and p.end_line >= start_line]
    if not overlapping:
        return None, None
    return overlapping[0].page, overlapping[-1].page


def _layout_from_markdown(
    markdown: str,
    sections: list[PdfSection],
    *,
    document_title: str,
) -> PdfDocumentLayout:
    """Compute page/section line maps from final bannered markdown."""
    lines = markdown.splitlines()
    page_spans: list[PdfPageSpan] = []
    open_page: int | None = None
    open_start: int | None = None
    for idx, line in enumerate(lines, start=1):
        start_match = _PAGE_START_RE.match(line.strip())
        if start_match:
            if open_page is not None and open_start is not None:
                page_spans.append(PdfPageSpan(page=open_page, start_line=open_start, end_line=idx - 1))
            open_page = int(start_match.group(1))
            open_start = idx
            continue
        end_match = _PAGE_END_RE.match(line.strip())
        if end_match and open_page is not None and open_start is not None:
            page_spans.append(PdfPageSpan(page=open_page, start_line=open_start, end_line=idx))
            open_page = None
            open_start = None
    if open_page is not None and open_start is not None:
        page_spans.append(PdfPageSpan(page=open_page, start_line=open_start, end_line=len(lines)))

    # Map sections onto consecutive blocks in the joined markdown.
    section_spans: list[PdfSectionSpan] = []
    paths = _heading_paths(sections)
    cursor = 1
    nonempty = [(section, path) for section, path in zip(sections, paths) if section.markdown and section.markdown.strip()]
    for i, (section, path) in enumerate(nonempty):
        block_lines = section.markdown.splitlines()
        start_line = cursor
        end_line = cursor + len(block_lines) - 1 if block_lines else cursor
        # Joined markdown inserts a blank line ("\n\n") between sections.
        if i > 0:
            # The join itself is already accounted when we advance cursor by
            # previous block length + 1 blank line below; start_line is correct.
            pass
        page_start, page_end = _pages_for_line_range(page_spans, start_line, end_line)
        if page_start is None:
            page_start = section.page_start
            page_end = section.page_end
        section_spans.append(
            PdfSectionSpan(
                title=section.title,
                level=section.level,
                start_line=start_line,
                end_line=end_line,
                page_start=page_start,
                page_end=page_end,
                heading_path=path,
            )
        )
        cursor = end_line + 2  # blank line separator between joined sections

    return PdfDocumentLayout(
        document_title=document_title,
        pages=tuple(page_spans),
        sections=tuple(section_spans),
    )


def _sections_from_docling_document(document, pdf_bytes: bytes | None = None) -> list[PdfSection]:
    """Build section drafts (markdown + cropped visuals) from a DoclingDocument.

    Text follows newspaper order: physical page, then left-to-right column, then
    top-to-bottom. Page banners wrap content; visual tokens are emitted inline.
    """
    from docling_core.types.doc import ContentLayer, ImageRefMode, PictureItem, SectionHeaderItem, TitleItem
    from PIL import Image as PILImage

    items = list(
        document.iterate_items(
            with_groups=True,
            traverse_pictures=True,
            included_content_layers={ContentLayer.BODY},
        )
    )
    if not items:
        markdown = document.export_to_markdown(image_mode=ImageRefMode.PLACEHOLDER)
        return [
            PdfSection(
                index=0,
                title="",
                level=0,
                page_start=None,
                page_end=None,
                markdown=markdown,
                visuals=[],
            )
        ]

    lefts_by_page: dict[int, list[float]] = {}
    picture_boxes: list[tuple[int, object]] = []
    for item, _level in items:
        located = _top_left_bbox(item, document)
        if located is not None:
            page_no, bbox = located
            page_w = _page_width(document, page_no) or 0.0
            box_w = float(bbox.r) - float(bbox.l)
            # Ignore narrow map/chart labels when detecting columns.
            if page_w and box_w >= 0.18 * page_w:
                lefts_by_page.setdefault(page_no, []).append(float(bbox.l))
        if isinstance(item, PictureItem) and located is not None:
            picture_boxes.append(located)

    centers_by_page = {
        page_no: _column_centers(lefts, _page_width(document, page_no) or 0.0)
        for page_no, lefts in lefts_by_page.items()
    }

    last_key = (1, 0, 0.0, 0.0)
    seq = 0
    positioned: list[tuple[tuple, object, int]] = []
    for idx, (item, level) in enumerate(items):
        xy = _item_page_xy(item, document)
        if xy is not None:
            page_no, top, left = xy
            centers = centers_by_page.get(page_no) or [0.0]
            col = _column_index(left, centers)
            last_key = (page_no, col, top, left)
            seq = 0
        else:
            seq += 1
        positioned.append((_layout_sort_key(*last_key, seq=seq, idx=idx), item, level))
    positioned.sort(key=lambda row: row[0])

    buckets: list[list[tuple[object, int]]] = [[]]
    meta: list[tuple[str, int, int | None, float]] = [("", 0, None, 0.0)]
    for key, item, level in positioned:
        if isinstance(item, (SectionHeaderItem, TitleItem)):
            title = (getattr(item, "text", None) or "").strip()
            heading_level = int(getattr(item, "level", 1) or 1)
            if buckets[-1]:
                buckets.append([])
                meta.append((title, heading_level, key[0], key[2]))
            else:
                meta[-1] = (title, heading_level, key[0], key[2])
        buckets[-1].append((item, level))

    pdfium_doc = None
    if pdf_bytes:
        import pypdfium2 as pdfium

        pdfium_doc = pdfium.PdfDocument(pdf_bytes)
    sections: list[PdfSection] = []
    page_state = _PageBannerState()
    try:
        for bucket_i, (bucket, (title, level, heading_page, _heading_top)) in enumerate(zip(buckets, meta)):
            if not bucket and not title:
                continue
            section_index = len(sections)
            visuals: list[PdfVisual] = []
            visual_tokens_by_item: dict[int, str] = {}
            for item_idx, (item, _lvl) in enumerate(bucket):
                if not isinstance(item, PictureItem):
                    continue
                located = _top_left_bbox(item, document)
                page_no = _page_nos(item)[0] if _page_nos(item) else heading_page
                png_bytes = None
                if located is not None:
                    page_no, bbox = located
                    doc_page = document.pages.get(page_no) if page_no is not None else None
                    doc_size = getattr(doc_page, "size", None) if doc_page is not None else None
                    if pdfium_doc is not None and page_no is not None:
                        png_bytes = _crop_region_from_pdf(
                            pdfium_doc, page_no=page_no, bbox=bbox, doc_page_size=doc_size
                        )
                pil_image = item.get_image(document) if png_bytes is None else None
                if png_bytes is None:
                    if not _is_significant_picture(pil_image):
                        continue
                    png_bytes = _png_bytes_from_pil(pil_image)
                cropped = PILImage.open(io.BytesIO(png_bytes))
                if not _is_significant_picture(cropped):
                    continue
                caption = ""
                try:
                    caption = (item.caption_text(document) or "").strip()
                except Exception:
                    caption = ""
                visual_id = f"s{section_index}-v{len(visuals)}"
                visuals.append(
                    PdfVisual(
                        visual_id=visual_id,
                        png_bytes=png_bytes,
                        page=page_no,
                        caption=caption,
                        width=cropped.size[0],
                        height=cropped.size[1],
                        bbox=_format_bbox(item, document),
                    )
                )
                visual_tokens_by_item[item_idx] = f"<!-- visual:{visual_id} -->"

            markdown = _markdown_from_section_items(
                document,
                bucket,
                picture_boxes,
                page_state=page_state,
                visual_tokens_by_item=visual_tokens_by_item,
            )
            # Close the open page banner at the end of the last section only so
            # consecutive sections on the same page share one wrapper.
            if bucket_i == len(buckets) - 1:
                closing = page_state.close()
                if closing:
                    markdown = f"{markdown}\n\n{closing[0]}" if markdown.strip() else closing[0]

            pages = [p for item, _lvl in bucket for p in _page_nos(item)]
            sections.append(
                PdfSection(
                    index=section_index,
                    title=title,
                    level=level,
                    page_start=heading_page if heading_page is not None else (min(pages) if pages else None),
                    page_end=max(pages) if pages else heading_page,
                    markdown=markdown,
                    visuals=visuals,
                )
            )
        # If the last kept section was not the last bucket, still close banners.
        if sections and page_state.open:
            closing = page_state.close()
            if closing:
                body = sections[-1].markdown.rstrip()
                sections[-1].markdown = f"{body}\n\n{closing[0]}" if body else closing[0]
    finally:
        if pdfium_doc is not None:
            pdfium_doc.close()

    return sections


def _extract_pdf_sections(pdf_bytes: bytes, url: str, max_pages: int) -> list[PdfSection]:
    """Convert PDF bytes with Docling and return serialisable section drafts.

    Must be called under ``_PDF_LOCK`` (or from ``_extract_pdf_sections_locked``).
    """
    import gc

    from docling_core.types.io import DocumentStream

    converter = _get_pdf_converter()
    filename = url.rsplit("/", 1)[-1].split("?")[0] or "document.pdf"
    source = DocumentStream(name=filename, stream=io.BytesIO(pdf_bytes))
    result = converter.convert(source, page_range=(1, max_pages))
    try:
        return _sections_from_docling_document(result.document, pdf_bytes=pdf_bytes)
    finally:
        # Explicit del + gc.collect ensures pypdfium2 child objects (pages) are
        # garbage-collected before their parent PdfDocument.
        del result
        gc.collect()


def _extract_pdf_sections_locked(pdf_bytes: bytes, url: str, max_pages: int) -> list[PdfSection]:
    with _PDF_LOCK:
        return _extract_pdf_sections(pdf_bytes, url, max_pages)


async def _enrich_sections_with_visuals(
    sections: list[PdfSection],
    *,
    vision_model: str,
) -> list[PdfSection]:
    """Describe visuals per section (concurrent) and return updated sections."""
    from . import _vision

    semaphore = asyncio.Semaphore(_VISUAL_ENRICH_CONCURRENCY)

    async def _enrich_one(section: PdfSection) -> PdfSection:
        if not section.visuals:
            return section
        markdown = section.markdown
        if _IMAGE_PLACEHOLDER in markdown and section.visuals:
            markdown, labeled_ids = _label_section_placeholders(markdown, section.index)
            for visual_id, visual in zip(labeled_ids, section.visuals):
                visual.visual_id = visual_id
        visuals_for_call = [
            {
                "visual_id": visual.visual_id,
                "png_bytes": visual.png_bytes,
                "caption": visual.caption,
                "page": visual.page,
                "bbox": visual.bbox,
            }
            for visual in section.visuals
        ]
        async with semaphore:
            summaries, error = await _vision.describe_section_visuals(
                section_markdown=markdown,
                visuals=visuals_for_call,
                vision_model=vision_model,
                section_title=section.title,
            )
            if error:
                logger.warning(
                    "Visual enrichment failed for section %s (%r): %s",
                    section.index,
                    section.title,
                    error,
                )
                return section
            enriched_md = _apply_visual_summaries(
                markdown,
                summaries,
                visuals=section.visuals,
                vision_model=vision_model,
            )
            return PdfSection(
                index=section.index,
                title=section.title,
                level=section.level,
                page_start=section.page_start,
                page_end=section.page_end,
                markdown=enriched_md,
                visuals=section.visuals,
            )

    return list(await asyncio.gather(*[_enrich_one(section) for section in sections]))


def _join_section_markdown(sections: list[PdfSection]) -> str:
    return "\n\n".join(section.markdown for section in sections if section.markdown and section.markdown.strip())


async def _convert_pdf_to_markdown(
    pdf_bytes: bytes,
    url: str,
    max_pages: int,
    *,
    vision_model: str | None = None,
) -> tuple[str, PdfDocumentLayout]:
    """Convert PDF bytes to markdown plus layout metadata."""
    sections = await asyncio.to_thread(_extract_pdf_sections_locked, pdf_bytes, url, max_pages)
    if vision_model and any(section.visuals for section in sections):
        sections = await _enrich_sections_with_visuals(sections, vision_model=vision_model)
    markdown = _join_section_markdown(sections)
    fallback = _filename_title(url)
    document_title = _infer_document_title(sections, fallback)
    layout = _layout_from_markdown(markdown, sections, document_title=document_title)
    return markdown, layout


async def scrape_document(
    url: str,
    *,
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
    known_content_type: str = "",
    known_content_disposition: str = "",
    needs_browser: bool = False,
    prefetched_bytes: bytes | None = None,
    vision_model: str | None = None,
) -> Tuple[SourceArtifact, Optional[str]]:
    """Extract content from a document URL.

    Returns a text ``SourceArtifact`` for documents with a readable text layer,
    or a binary ``SourceArtifact`` (``mime_type="application/pdf"``) for scanned
    PDFs so that callers can optionally apply vision extraction.
    """
    title = _filename_title(url)

    unsupported = unsupported_legacy_document_reason(url, known_content_type, known_content_disposition)
    if unsupported:
        return SourceArtifact(kind="text", title=title), f"Skipped: {unsupported}"

    is_pdf = await _resolve_is_pdf(url, known_content_type, known_content_disposition, needs_browser=needs_browser)

    if is_pdf:
        pdf_bytes = prefetched_bytes if prefetched_bytes and prefetched_bytes.startswith(b"%PDF") else None
        error = None
        if pdf_bytes is None:
            pdf_bytes, error = await download_pdf(url, needs_browser=needs_browser)
        if error or not pdf_bytes:
            return SourceArtifact(kind="text", title=title), error or "PDF download returned empty bytes"

        content, layout = await _convert_pdf_to_markdown(
            pdf_bytes,
            url,
            max_pdf_pages,
            vision_model=vision_model,
        )
        content = append_links(content, None)
        # Recompute layout after append_links only if links were appended (usually none for PDFs).
        title = layout.document_title or title

        if len(_text_without_image_placeholders(content)) < ROUTING_HEURISTICS.min_pdf_text_chars:
            # Scanned / image-only PDF: return raw bytes for optional vision extraction
            return SourceArtifact(
                kind="binary",
                title=title,
                binary_bytes=pdf_bytes,
                mime_type="application/pdf",
            ), None

        return SourceArtifact(kind="text", title=title, text_content=content, layout=layout), None

    # Non-PDF office documents (DOCX, PPTX, XLSX) — let docling fetch and convert
    from docling.document_converter import DocumentConverter

    def _convert_office() -> str:
        converter = DocumentConverter()
        result = converter.convert(url)
        return result.document.export_to_markdown()

    try:
        content = await asyncio.to_thread(_convert_office)
    except Exception as exc:
        return SourceArtifact(kind="text", title=title), f"Document conversion failed: {exc}"

    content = append_links(content, None)
    return SourceArtifact(kind="text", title=title, text_content=content), None
