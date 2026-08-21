"""Tests for _html_to_markdown: non-content elements must not leak into text.

Regression test for the publisher-page bug where <script>/<style> text filled
the truncation window and starved the content extractor of article prose.
"""

from web_scout.scraping._html import _html_to_markdown

_HTML = """<html><head>
<title>Article title</title>
<script>var consentJunk = {"a": 1}; function f() { return 42; }</script>
<style>.cls { color: red; font-size: 12px; }</style>
</head><body>
<noscript>Please enable JavaScript to continue.</noscript>
<p>Real article prose that should survive conversion.</p>
<svg><text>svg axis label</text></svg>
<h2>Methods</h2>
<p>More prose in a second paragraph.</p>
</body></html>"""


def test_html_to_markdown_keeps_prose():
    out = _html_to_markdown(_HTML)
    assert "Real article prose that should survive conversion." in out
    assert "More prose in a second paragraph." in out
    assert "## Methods" in out


def test_html_to_markdown_excludes_script_content():
    out = _html_to_markdown(_HTML)
    assert "consentJunk" not in out


def test_html_to_markdown_excludes_style_content():
    out = _html_to_markdown(_HTML)
    assert "color: red" not in out


def test_html_to_markdown_excludes_noscript_content():
    out = _html_to_markdown(_HTML)
    assert "enable JavaScript" not in out


def test_html_to_markdown_excludes_svg_content():
    out = _html_to_markdown(_HTML)
    assert "svg axis label" not in out
