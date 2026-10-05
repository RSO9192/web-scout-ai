"""Unit tests for the Jev link selector. No network or model calls."""

import asyncio

from web_scout.jev_link_selector import (
    JEV_RELEVANT_MIN,
    build_jev_questions,
    build_jev_state,
    candidate_links,
    link_label,
    select_links_with_jev,
    urls_above_threshold,
)


def test_link_label_reads_the_path_as_words():
    url = "https://www.bbc.com/news/topics/c9grgwq2krzt?page=2"
    assert link_label(url) == "bbc.com news topics c9grgwq2krzt page 2"


def test_link_label_uses_the_anchor_text():
    url = "https://openknowledge.fao.org/items/d6e3823c-b0d2-4ff9-9ead-c28bd2d9375b"
    content = f"[In brief to The State of the World's Forests 2026]({url})"
    assert link_label(url, content) == "In brief to The State of the World's Forests 2026"


def test_link_label_uses_the_heading_when_the_link_text_is_not_a_title():
    url = "https://www.bbc.com/news/articles/c6lyez3q3j9wo"
    content = (
        "## Pitch and putt saved as important for tourism\n\n"
        "15 Sept 2026](/news/articles/c6lyez3q3j9wo)\n"
    )
    assert link_label(url, content) == "Pitch and putt saved as important for tourism"


def test_link_label_skips_a_download_anchor_for_the_report_heading():
    url = "https://openknowledge.fao.org/bitstreams/2266fca9-6b6b-4296-855e-4a93e57f4118/download"
    content = (
        "## The State of the World's Forests 2026\n\n"
        "[Download PDF](/bitstreams/2266fca9-6b6b-4296-855e-4a93e57f4118/download)\n"
    )
    assert link_label(url, content) == "The State of the World's Forests 2026"


def test_candidate_links_drop_the_parent_and_keep_the_next_page():
    parent = "https://www.bbc.com/news/topics/c9grgwq2krzt"
    links = candidate_links(
        [
            parent,
            "https://bbc.com/news/topics/c9grgwq2krzt/",
            "https://www.bbc.com/news/topics/c9grgwq2krzt?page=2",
            "https://www.bbc.com/news/articles/c6lyez3q3j9wo",
            "",
        ],
        parent,
    )
    assert links == [
        "https://www.bbc.com/news/topics/c9grgwq2krzt?page=2",
        "https://www.bbc.com/news/articles/c6lyez3q3j9wo",
    ]


def test_each_link_is_its_own_yes_or_no_question():
    questions = build_jev_questions(2)
    instructions = questions["link_0"].instructions
    assert list(questions) == ["link_0", "link_1"]
    assert "A relevant link is a news story, article, report, document, or dataset" in instructions
    assert "An irrelevant link is social media, an icon, a chart, a map, a dashboard, a search page" in instructions
    assert "Prefer a link that adds facts about the query." in instructions
    assert "Prefer a link the page recommends as related." in instructions
    assert "not a reason to answer no" in instructions
    assert "`links[0].recommendation`" in instructions
    assert "`links[1].url`" in questions["link_1"].instructions
    assert "programme page the page recommends as related is a yes" in questions["link_0"].criteria["true"]
    assert "search or catalogue page is irrelevant" in questions["link_0"].criteria["false"]
    assert "YouTube" in questions["link_0"].criteria["false"]
    assert "photo gallery or campaign photo page is irrelevant" in questions["link_0"].criteria["false"]


def test_state_marks_a_link_the_page_lists_as_related():
    content = """##### Related links:

[Climate action](https://www.fao.org/climate-change/)

[Anticipatory action](https://www.fao.org/emergencies/our-focus/anticipatory-action/en)

## News

[A story](https://www.fao.org/climate-change/news/story/en)
"""
    state = build_jev_state(
        "el nino",
        "https://www.fao.org/el-nino/en",
        content,
        [
            "https://www.fao.org/climate-change/",
            "https://www.fao.org/climate-change/news/story/en",
        ],
    )

    assert state["links"][0]["recommendation"] == "The page recommends this link as related."
    assert state["links"][1]["recommendation"] == ""


def test_a_thumbnail_in_the_related_section_is_not_a_recommendation():
    content = """### Related items

[In brief to The State of the World's Forests 2026](https://openknowledge.fao.org/items/d6e3823c-b0d2-4ff9-9ead-c28bd2d9375b)

[Thumbnail Image](https://openknowledge.fao.org/server/api/core/bitstreams/7890cb42-9845-41f3-9404-742df35d75f6/content)
"""
    state = build_jev_state(
        "forestry",
        "https://openknowledge.fao.org/items/3758005c-1267-40be-a267-75d442633f62",
        content,
        [
            "https://openknowledge.fao.org/items/d6e3823c-b0d2-4ff9-9ead-c28bd2d9375b",
            "https://openknowledge.fao.org/server/api/core/bitstreams/7890cb42-9845-41f3-9404-742df35d75f6/content",
        ],
    )
    assert state["links"][0]["recommendation"] == "The page recommends this link as related."
    assert state["links"][1]["recommendation"] == ""


def test_state_gives_jev_a_label_and_keeps_the_url_for_the_caller():
    state = build_jev_state(
        "forestry",
        "https://openknowledge.fao.org/items/3758005c-1267-40be-a267-75d442633f62",
        "The State of the World's Forests 2026.",
        ["https://openknowledge.fao.org/items/d6e3823c-b0d2-4ff9-9ead-c28bd2d9375b"],
    )
    assert state["query"] == "forestry"
    assert state["links"][0]["url"].endswith("d6e3823c-b0d2-4ff9-9ead-c28bd2d9375b")
    assert "d6e3823c" in state["links"][0]["label"]


def test_urls_above_threshold_keep_only_clear_yes_answers():
    links = ["https://example.org/a", "https://example.org/b", "https://example.org/c"]
    selected = urls_above_threshold(
        links,
        {"link_0": JEV_RELEVANT_MIN, "link_1": JEV_RELEVANT_MIN + 0.01},
    )
    assert selected == ["https://example.org/b"]


def test_select_links_with_jev_skips_the_api_when_there_are_no_links():
    selected, error = asyncio.run(
        select_links_with_jev(
            query="el nino",
            parent_url="https://www.fao.org/el-nino/en",
            parent_content="El Niño",
            candidates=["https://www.fao.org/el-nino/en"],
        )
    )
    assert selected == []
    assert error is None
