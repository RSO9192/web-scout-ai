"""Pure parts of the old-versus-new comparison runner in ``scripts/compare_versions.py``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from compare_versions import (  # noqa: E402
    bucket_summary,
    check_figures_in_body,
    claim_figures,
    parse_log_counters,
    parse_log_urls,
)

LOG = (
    "[2026-10-26 18:00:00] INFO: [pipeline] open-mode follow-up on 2 links: "
    "['https://t.org/a', 'https://t.org/b']\n"
    "[2026-10-26 18:00:01] INFO: [extract] data_source_opened D1 https://t.org/a.json\n"
    "[2026-10-26 18:00:02] INFO: [extract] data_quote_dropped D7 unknown source\n"
)


def test_log_counters():
    assert parse_log_counters(LOG) == {
        "followup_waves": 1,
        "followup_links": 2,
        "data_sources_opened": 1,
        "data_quotes_recorded": 0,
        "data_quotes_dropped": 1,
    }


def test_log_counters_default_to_zero_for_old_logs():
    old_log = "[2026-10-26 18:00:00] INFO: [pipeline] Round 1 search\n"
    assert parse_log_counters(old_log) == dict.fromkeys(
        ["followup_waves", "followup_links", "data_sources_opened", "data_quotes_recorded", "data_quotes_dropped"], 0
    )


def test_log_counters_count_recorded_quotes_and_waves():
    log = (
        "[pipeline] open-mode follow-up on 1 links: ['https://x.org/1']\n"
        "[pipeline] open-mode follow-up on 3 links: ['https://x.org/2', 'https://x.org/3', 'https://x.org/4']\n"
        "[extract] data_quote_recorded D1 https://x.org/d.json\n"
        "[extract] data_quote_recorded D2 https://x.org/e.csv\n"
    )
    counters = parse_log_counters(log)
    assert counters["followup_waves"] == 2
    assert counters["followup_links"] == 4
    assert counters["data_quotes_recorded"] == 2


def test_log_urls():
    assert parse_log_urls(LOG) == {
        "followup_links": ["https://t.org/a", "https://t.org/b"],
        "data_sources_opened": ["https://t.org/a.json"],
    }


def test_data_file_citation_check_uses_raw_body():
    assert check_figures_in_body(["155.8", "999.9"], '{"area": 155.79873}') == {"155.8": True, "999.9": False}


def test_figure_check_tries_both_separator_readings():
    body = "municipio;area\nAltamira;1234.5\nPorto Velho;98765\n"
    assert check_figures_in_body(["1,234.5", "1.234,5", "98,765", "98.765", "1234"], body) == {
        "1,234.5": True,
        "1.234,5": True,
        "98,765": True,
        "98.765": True,
        "1234": False,
    }


def test_integer_figure_does_not_match_by_rounding():
    assert check_figures_in_body(["156"], '{"area": 155.79873}') == {"156": False}


def test_claim_figures_skip_years():
    claim = "In 2025, Altamira lost 155.8 km² and São Félix do Xingu lost 1,234 km² of forest."
    assert claim_figures(claim) == ["155.8", "1,234"]


def test_bucket_summary():
    summary = bucket_summary({"scraped": [{"url": "a"}], "scraped_irrelevant": [{"url": "b"}, {"url": "c"}]})
    assert summary == {"scraped": 1, "scraped_irrelevant": 2}


def test_claim_figures_skip_units_codes_and_crop_years():
    claim = "Region R12 lost 5,816 km2 in 2025/26, with PM2.5 levels rising."
    assert claim_figures(claim) == ["5,816"]


def test_claim_figures_keep_full_number_before_glued_unit():
    assert claim_figures("Altamira lost 155.8km² in 2025.") == ["155.8"]
    assert claim_figures("Funding reached US$1.2bn total.") == ["1.2"]
    assert claim_figures("It built 5,816km of road.") == ["5,816"]
