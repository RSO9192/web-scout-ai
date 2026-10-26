"""Dashboard-style data sources: detect pages whose numbers live in charts, maps or BI reports.

``assess_dashboard`` is a pure check on a URL and its fast-HTTP HTML; callers use it to decide
which pages deserve a browser render and data capture.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlsplit

DashboardKind = Literal["", "dashboard", "bi"]

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


@dataclass(frozen=True)
class DashboardAssessment:
    """Whether a page looks like a dashboard, and the signals that decided it."""

    kind: DashboardKind
    reasons: tuple[str, ...]

    @property
    def is_dashboard(self) -> bool:
        return bool(self.kind)


class _PageScan(HTMLParser):
    """Collect script sources, inline script bodies and visible text in one pass."""

    _HIDDEN = {"style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.script_srcs: list[str] = []
        self.scripts: list[str] = []
        self.text: list[str] = []
        self._script: list[str] | None = None
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            src = dict(attrs).get("src")
            if src:
                self.script_srcs.append(src)
            self._script = []
        elif tag in self._HIDDEN:
            self._hidden_depth += 1

    def handle_endtag(self, tag):
        if tag == "script":
            if self._script is not None:
                self.scripts.append("".join(self._script))
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


def _data_script_reason(scripts: list[str]) -> str:
    for script in scripts:
        if len(script) < _DATA_SCRIPT_MIN_CHARS:
            continue
        body = _JS_ASSIGNMENT.sub("", script, count=1).lstrip()
        if body[:1] not in ("{", "["):
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
