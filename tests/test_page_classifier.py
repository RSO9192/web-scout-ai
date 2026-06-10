"""Unit tests for structural page-shape classification."""

from web_scout._page_classifier import (
    classify_html_page_shape,
    classify_prefetched_page_shape,
)


def test_classify_html_rich_publication_page_with_download_is_content():
    prose = (
        "The page already contains substantial narrative findings on rainfall trends, "
        "regional contrasts, recent anomalies, and climate impacts. "
        "It explains how the recent observations compare with longer-term climatology "
        "and seasonal performance across Kenya. "
    ) * 10
    html = (
        """
    <html>
      <head><title>State of Climate Publication</title></head>
      <body>
        <h1>State of Climate Publication</h1>
        <p>Abstract: This publication summarises Kenya precipitation variability,
        observed trends, recent anomalies, and seasonal outlooks.</p>
        <p>Authors: Kenya Meteorological Department. Published: 2025.</p>
        <p><a href="/files/state-of-climate.pdf">Download PDF</a></p>
        <p>%s</p>
      </body>
    </html>
    """
        % prose
    )

    shape = classify_html_page_shape(html)

    assert shape.page_type == "content_page"
    assert shape.content_score > shape.record_score


def test_classify_html_sparse_repository_record_is_record_page():
    html = """
    <html>
      <body>
        <h1>Repository record</h1>
        <p>Authors: Example Author.</p>
        <p>Published: 2024.</p>
        <p>Abstract: Summary page only.</p>
        <p>Citation: Example Journal.</p>
        <a href="/download/report.pdf">Full text PDF</a>
      </body>
    </html>
    """

    shape = classify_html_page_shape(html)

    assert shape.page_type == "record_page"
    assert shape.record_score >= shape.content_score + 2


def test_classify_prefetched_spa_shell_is_interactive():
    content = "Navigation shell\n\n[SPA: URL fragment detected — current content may be the wrong tab/view.]"

    shape = classify_prefetched_page_shape(content)

    assert shape.page_type == "interactive_shell"
    assert shape.interactive_score >= 5


def test_classify_prefetched_article_with_reference_pdf_stays_content():
    content = (
        "This article explains procurement policy and cites a background PDF "
        "https://example.org/background.pdf. "
        + "Specific content with rules, certification references, and factual context. "
        * 80
    )

    shape = classify_prefetched_page_shape(content)

    assert shape.page_type == "content_page"
    assert shape.content_score > shape.record_score
