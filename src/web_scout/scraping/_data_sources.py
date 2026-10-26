"""Dashboard-style data sources: detect pages whose numbers live in charts, maps or BI reports.

``assess_dashboard`` is a pure check on a URL and its fast-HTTP HTML; callers use it to decide
which pages deserve a browser render and data capture. ``DataSource`` models one data file behind
a page (a background response, an inline JSON blob or a download); ``is_data_response``,
``inline_json_sources`` and ``cap_sources`` select which ones are kept.
"""

import dataclasses
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urlsplit

DashboardKind = Literal["", "dashboard", "bi"]
DataSourceKind = Literal["response_json", "response_csv", "inline_json", "download_csv", "download_xlsx"]

_BI_HOSTS = ("app.powerbi.com", "public.tableau.com")

# Matched against the lowercased path plus fragment, so hash-routed SPAs (``#/dashboard/...``) count.
_DASHBOARD_PATHS = {
    "/dashboard": re.compile(r"/dashboards?(?=[/?#]|$)"),
    "/grapher/": re.compile(r"/grapher/"),
    "/databrowser/": re.compile(r"/databrowser/"),
}
_DASHBOARD_HOST_PATHS = ("arcgis.com/apps/dashboards",)

_CHART_LIBRARIES = (
    "highcharts",
    "echarts",
    "plotly",
    "chart.js",
    "d3",
    "vega",
    "leaflet",
    "mapbox-gl",
    "amcharts",
    "apexcharts",
    "gridstack",
)
_LIBRARY_IN_SRC = re.compile(r"(?<![a-z0-9])(" + "|".join(map(re.escape, _CHART_LIBRARIES)) + r")(?![a-z0-9])")
# Bare names in inline code are noisy (a "VEGA" phone model, a minified ``d3`` identifier),
# so inline code must call the library's API.
_LIBRARY_IN_CODE = re.compile(
    r"Highcharts\.(?:chart|stockChart|mapChart)\(|echarts\.init\(|Plotly\.(?:newPlot|react|plot)\("
    r"|new Chart\(|d3\.(?:select|json|csv)\(|vegaEmbed\(|L\.map\(|new (?:mapboxgl|maplibregl)\.Map\("
    r"|am[45]core\.create\(|am5\.Root\.new\(|new ApexCharts\(|GridStack\.init\("
)

_DATA_SCRIPT_MIN_CHARS = 50_000
_DATA_SCRIPT_MIN_NUMBERS = 500
# Strip ``window.__data =`` style assignments so state blobs count as JSON, while code does not.
_JS_ASSIGNMENT = re.compile(r"^\s*(?:(?:window|self|globalThis)\.[\w$.]+|(?:var|let|const)\s+[\w$]+)\s*=\s*")
# Numeric JSON values only, not digits inside strings or identifiers.
_JSON_NUMBER = re.compile(r"[:\[,]\s*-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?=\s*[,\]}])")

# News prose runs at 5-9 sentences per 1,000 visible characters; dashboards and tables fall below 3.
_PROSE_SENTENCES_PER_1K = 3.0
# An empty client-rendered shell has no prose to measure, so it is not evidence of a dashboard.
_PROSE_MIN_CHARS = 1_000
# Count terminators, not sentence spans: a span pattern backtracks quadratically on long unpunctuated tables.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")

_MIN_NUMERIC_LEAVES = 50
_JSON_DECODER = json.JSONDecoder()
_MAX_SOURCES_PER_PAGE = 8
_MAX_BYTES_PER_PAGE = 12_000_000
_MAX_BYTES_PER_SOURCE = 5_000_000

# Whole host or path tokens, so "auth" does not match "authors" and "log" does not match "catalog".
# Grouped by the noise categories: analytics, tag managers, consent, translation, auth, telemetry.
_NOISE_TOKENS = (
    "matomo|piwik|hotjar|mixpanel|doubleclick|adservices|demdex|omtrdc|qualtrics"
    "|gtm|gtag|googletagmanager|tagmanager"
    "|consent|cookieconsent|cookieconsentpub|cookies|onetrust|cookiebot|usercentrics"
    "|i18n|l10n|t9n|locale|locales|translations"
    "|auth|oauth|oauth2|authentication|login|logout|signin|sso|token|session|identity"
    "|telemetry|sentry|envelope|beacon|rum|newrelic|nr-data"
)
_NOISE_URL = re.compile(r"(?<![a-z0-9])(?:" + _NOISE_TOKENS + r")(?![a-z0-9])")
# Generic words name real data APIs in paths (DHIS2 ``/api/analytics.json``, ``/api/track/records``),
# so they mark noise only as host labels such as ``analytics.example.org``.
_NOISE_HOST = re.compile(r"(?<![a-z0-9])(?:analytics|collect|track|tracking)(?![a-z0-9])")
_CSV_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class DashboardAssessment:
    """Whether a page looks like a dashboard, and the signals that decided it."""

    kind: DashboardKind
    reasons: tuple[str, ...]

    @property
    def is_dashboard(self) -> bool:
        return bool(self.kind)


@dataclass(frozen=True)
class DataSource:
    """One data file behind a page, numbered ``D1``, ``D2``... in page order.

    ``size`` is the body size in bytes; ``body`` is empty for ``download_xlsx``.
    """

    source_id: str
    url: str
    kind: DataSourceKind
    content_type: str
    body: str
    size: int


class _PageScan(HTMLParser):
    """Collect script sources, inline script bodies and visible text in one pass."""

    _HIDDEN = {"style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.script_srcs: list[str] = []
        self.scripts: list[str] = []
        self.script_attrs: list[dict[str, str | None]] = []
        self.text: list[str] = []
        self._script: list[str] | None = None
        self._attrs: dict[str, str | None] = {}
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._attrs = dict(attrs)
            src = self._attrs.get("src")
            if src:
                self.script_srcs.append(src)
            self._script = []
        elif tag in self._HIDDEN:
            self._hidden_depth += 1

    def handle_endtag(self, tag):
        if tag == "script":
            if self._script is not None:
                self.scripts.append("".join(self._script))
                self.script_attrs.append(self._attrs)
            self._script = None
        elif tag in self._HIDDEN and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        elif not self._hidden_depth:
            self.text.append(data)


def _url_reasons(url: str) -> list[str]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    route = (parts.path + "#" + parts.fragment).lower()
    reasons = [f"dashboard path: {label}" for label, pattern in _DASHBOARD_PATHS.items() if pattern.search(route)]
    reasons += [f"dashboard host path: {hp}" for hp in _DASHBOARD_HOST_PATHS if hp in host + parts.path.lower()]
    return reasons


def _script_json_text(script: str) -> str:
    """Return a script's text from its leading JSON object or array, or ``""`` if it does not start with one."""
    body = _JS_ASSIGNMENT.sub("", script, count=1).lstrip()
    return body if body[:1] in ("{", "[") else ""


def _data_script_reason(scripts: list[str]) -> str:
    for script in scripts:
        if len(script) < _DATA_SCRIPT_MIN_CHARS:
            continue
        body = _script_json_text(script)
        if not body:
            continue
        numbers = len(_JSON_NUMBER.findall(body))
        if numbers >= _DATA_SCRIPT_MIN_NUMBERS:
            return f"data script: {len(script)} chars, {numbers} numbers"
    return ""


def _content_reasons(html: str) -> list[str]:
    scan = _PageScan()
    scan.feed(html)
    scan.close()
    reasons = []

    libraries = sorted(
        {m.group(1) for src in scan.script_srcs for m in _LIBRARY_IN_SRC.finditer(src.lower())}
        | {m.group(0).rstrip("(") for code in scan.scripts for m in _LIBRARY_IN_CODE.finditer(code)}
    )
    if libraries:
        reasons.append("chart library: " + ", ".join(libraries))

    data_reason = _data_script_reason(scan.scripts)
    if data_reason:
        reasons.append(data_reason)

    text = " ".join(" ".join(scan.text).split())
    sentences = len(_SENTENCE_END.findall(text))
    if len(text) >= _PROSE_MIN_CHARS and sentences * 1000 / len(text) < _PROSE_SENTENCES_PER_1K:
        reasons.append(f"low prose: {sentences} sentences in {len(text)} chars")
    return reasons


def assess_dashboard(url: str, html: str) -> DashboardAssessment:
    """Classify a page from its URL and fast-HTTP HTML, without network access.

    Returns kind ``"bi"`` for Power BI and Tableau hosts, ``"dashboard"`` when the URL has a
    dashboard route or at least two content signals agree (chart library, large numeric JSON
    script, low prose density over at least 1,000 visible characters), and ``""`` otherwise.
    ``reasons`` lists every signal that fired.
    """
    host = (urlsplit(url).hostname or "").lower()
    if host in _BI_HOSTS:
        return DashboardAssessment("bi", (f"bi host: {host}",))

    url_reasons = _url_reasons(url)
    content_reasons = _content_reasons(html) if html else []
    reasons = tuple(url_reasons + content_reasons)
    # One content signal is common on articles (an embedded map, a short page); two agreeing is not.
    if url_reasons or len(content_reasons) >= 2:
        return DashboardAssessment("dashboard", reasons)
    return DashboardAssessment("", reasons)


def _site(host: str) -> str:
    return ".".join(host.lower().rstrip(".").split(".")[-2:])


def is_data_response(url: str, content_type: str, page_url: str) -> bool:
    """Whether a background response is worth keeping as page data.

    True when the response is on the page's registrable site (last two host labels), has a JSON or
    CSV content type, and its host and path carry no analytics, tag-manager, consent, translation,
    auth or telemetry token.
    """
    media_type = content_type.split(";", 1)[0].strip().lower()
    if "json" not in media_type and "csv" not in media_type:
        return False
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not host or _site(host) != _site(urlsplit(page_url).hostname or ""):
        return False
    return not (_NOISE_HOST.search(host.lower()) or _NOISE_URL.search(host.lower() + parts.path.lower()))


def _decode_json(text: str) -> tuple[Any, int] | None:
    """Decode the JSON value at the start of ``text`` as ``(value, end)``, or ``None`` if it is not valid JSON."""
    try:
        return _JSON_DECODER.raw_decode(text)
    # Page-controlled input: thousands of nested brackets overflow the decoder's recursion.
    except (ValueError, RecursionError):
        return None


def is_data_body(body: str, content_type: str) -> bool:
    """Whether a captured response body holds data rather than configuration.

    JSON bodies must parse in full and have at least 50 numeric leaves; CSV bodies need a header
    line plus at least one data row containing a digit. Other content types are not data.
    """
    media_type = content_type.split(";", 1)[0].strip().lower()
    if "json" in media_type:
        text = body.strip()
        decoded = _decode_json(text)
        return (
            decoded is not None and decoded[1] == len(text) and count_numeric_leaves(decoded[0]) >= _MIN_NUMERIC_LEAVES
        )
    if "csv" in media_type:
        rows = [line for line in body.splitlines() if line.strip()]
        return any(_CSV_DIGIT.search(row) for row in rows[1:])
    return False


def count_numeric_leaves(value: Any) -> int:
    """Count int and float leaves in parsed JSON; booleans and numeric strings do not count."""
    count = 0
    # Iterative walk: deeply nested state blobs would hit the recursion limit.
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            count += 1
    return count


def inline_json_sources(html: str, page_url: str) -> list[DataSource]:
    """Extract data-like JSON embedded in a page's inline scripts, in page order.

    Reads ``<script type="application/json">`` blocks (including ``__NEXT_DATA__``) and scripts that
    start with a ``window.<name> = {...}`` or ``[...]`` assignment. Each body is the verbatim JSON
    text, kept only if it parses and has at least 50 numeric leaves. Sources are numbered ``D1``...
    """
    scan = _PageScan()
    scan.feed(html)
    scan.close()
    sources: list[DataSource] = []
    for script, attrs in zip(scan.scripts, scan.script_attrs):
        script_type = (attrs.get("type") or "").strip().lower()
        if script_type == "application/json" or attrs.get("id") == "__NEXT_DATA__":
            text = script.strip()
        else:
            text = _script_json_text(script)
        if not text:
            continue
        # Decoding stops at the end of the value, so trailing code after the assignment is ignored.
        decoded = _decode_json(text)
        if decoded is None:
            continue
        value, end = decoded
        if count_numeric_leaves(value) < _MIN_NUMERIC_LEAVES:
            continue
        body = text[:end]
        sources.append(
            DataSource(
                f"D{len(sources) + 1}", page_url, "inline_json", "application/json", body, len(body.encode("utf-8"))
            )
        )
    return sources


def cap_sources(sources: list[DataSource]) -> tuple[DataSource, ...]:
    """Keep sources in order within the per-page caps and renumber them ``D1``, ``D2``...

    A source above 5 MB is dropped; a source that would push the page total above 12 MB is
    skipped; at most 8 sources are kept.
    """
    kept: list[DataSource] = []
    total = 0
    for source in sources:
        if len(kept) == _MAX_SOURCES_PER_PAGE:
            break
        if source.size > _MAX_BYTES_PER_SOURCE or total + source.size > _MAX_BYTES_PER_PAGE:
            continue
        total += source.size
        kept.append(dataclasses.replace(source, source_id=f"D{len(kept) + 1}"))
    return tuple(kept)
