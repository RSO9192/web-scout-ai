"""Follow-up URL selection with TypeSafe Jev.

Jev answers a yes-or-no question with a probability. It does not write text,
so this selector never asks it to emit URLs. Each candidate is one independent
question in a single request, and several links can be relevant at once. The
code copies the original URL when the yes-probability is above the threshold.

The label Jev reads is the link text, or the heading above the link when the
text is only "Download" or a date. A bare URL is the fallback. Jev is literal,
and an article id such as ``c6lyez3q3j9wo`` does not say what the page is about.
Links are judged in small batches. A long list of unrelated links in one state
pulls every yes-probability down.
"""

import re
from urllib.parse import unquote, urlparse

from typesafe_sdk import AsyncTypeSafeClient, Noul

# Pin the model. The jev-latest alias can move, and a new version would change
# these probabilities without a code change.
JEV_MODEL = "jev-1.13.0"
# More likely yes than no. The cutoff lives here, not in the question.
JEV_RELEVANT_MIN = 0.5
JEV_TIMEOUT_SECONDS = 180
# Above this, unrelated links in the same state drag relevant ones below the cutoff.
JEV_BATCH_SIZE = 16
# A short excerpt tells Jev what the page is about. The rest of the page is
# navigation and repeated chrome, which pulls the judgment off the link.
PARENT_EXCERPT_CHARS = 800

_MARKDOWN_LINK = re.compile(r"\[([^\[\]]+)\]\(([^)\s]+)\)")
_HEADING_LINE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_HEADING_IN_PAGE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
_DATE_LINE = re.compile(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}")
_RELATED_TITLES = frozenset(
    {
        "related",
        "related links",
        "related items",
        "related content",
        "related resources",
        "see also",
        "further reading",
        "recommended links",
    }
)
_RECOMMENDED = "The page recommends this link as related."

_GENERIC_LABELS = frozenset(
    {
        "download",
        "download pdf",
        "read online",
        "read more",
        "click here",
        "pdf",
        "en",
        "home",
        "here",
        "link",
        "related links",
        "related items",
    }
)


_TRUE = (
    "A relevant link is a news story, article, report, document, dataset, or detail page "
    "that adds facts about the query. "
    "The download of a report, and a companion brief or guideline the page points to, are relevant. "
    "A separate news story about the query is relevant. "
    "A programme page the page recommends as related is a yes."
    "A link to the next page of the pagination is relevant."
)
_FALSE = (
    "An irrelevant link does not add a new source about the query. "
    "Social media, a YouTube link, an icon, a thumbnail, an image file such as jpg, png, or svg, "
    "and a share button are irrelevant. "
    "A search or catalogue page is irrelevant. "
    "A url that contains the word search, youtube, twitter, facebook, jpg, or png, "
    "or that ends in /full, is irrelevant. "
    "A chart, a map, a dashboard, or a monitoring tool is irrelevant. "
    "A photo gallery or campaign photo page is irrelevant. "
    "Another view of this same page is irrelevant. "
    "A press release or interactive page that only retells the document already open is irrelevant. "
    "A section index, or a tab of this page such as news, publications, photos, videos, or home, "
    "is irrelevant."
)


def _url_words(url: str) -> str:
    parsed = urlparse(unquote(url.strip()))
    host = parsed.netloc.lower().removeprefix("www.")
    path = unquote(parsed.path).strip("/").replace("/", " ").replace("-", " ").replace("_", " ")
    query = unquote(parsed.query).replace("&", " ").replace("=", " ")
    return " ".join(f"{host} {path} {query}".split())


def _useful_label(text: str) -> bool:
    cleaned = " ".join(text.replace("*", "").split())
    if len(cleaned) < 8 or _DATE_LINE.fullmatch(cleaned):
        return False
    return cleaned.lower().strip("[] ") not in _GENERIC_LABELS


def _same_target(href: str, url: str) -> bool:
    href = unquote(href.strip())
    target = urlparse(url)
    if href.startswith(("http://", "https://")):
        return _page_key(href) == _page_key(url)
    href_path = unquote(urlparse(href).path).rstrip("/")
    target_path = unquote(target.path).rstrip("/")
    return bool(target_path) and href_path == target_path


def _markdown_anchor(url: str, content: str) -> str | None:
    for match in _MARKDOWN_LINK.finditer(content):
        if _same_target(match.group(2), url):
            return " ".join(match.group(1).split())
    return None


def _preceding_heading(url: str, content: str) -> str | None:
    path = unquote(urlparse(url).path)
    if len(path.strip("/")) < 2:
        return None
    index = content.find(path)
    if index < 0:
        return None
    heading = None
    for line in content[max(0, index - 700) : index].splitlines():
        match = _HEADING_LINE.match(line.strip())
        if match and _useful_label(match.group(1)):
            heading = " ".join(match.group(1).split())
    return heading


def _heading_is_related(title: str) -> bool:
    cleaned = " ".join(title.replace("*", "").split()).lower().strip(" :")
    return cleaned in _RELATED_TITLES or cleaned.startswith("related ")


def _related_spans(content: str) -> list[tuple[int, int]]:
    """Slices of the page that sit under a related-links heading."""
    headings = list(_HEADING_IN_PAGE.finditer(content))
    spans: list[tuple[int, int]] = []
    for index, match in enumerate(headings):
        if not _heading_is_related(match.group(1)):
            continue
        level = len(match.group(0)) - len(match.group(0).lstrip("#"))
        end = len(content)
        for later in headings[index + 1 :]:
            later_level = len(later.group(0)) - len(later.group(0).lstrip("#"))
            if later_level <= level:
                end = later.start()
                break
        spans.append((match.end(), end))
    return spans


def _link_is_recommended(url: str, content: str) -> bool:
    """True when a related-links section gives this URL a real title.

    A path that only appears as a thumbnail or a language toggle is not the
    page recommending the link.
    """
    for start, end in _related_spans(content):
        anchor = _markdown_anchor(url, content[start:end])
        if not anchor or not _useful_label(anchor):
            continue
        if "thumbnail" in anchor.lower():
            continue
        return True
    return False


def page_recommendation(url: str, content: str) -> str:
    """Literal note when the page lists the link as related.

    An empty string when it does not. A negative sentence here makes Jev reject
    articles that are the body of the page rather than a related-links list.
    """
    if content and _link_is_recommended(url, content):
        return _RECOMMENDED
    return ""


def link_label(url: str, content: str = "") -> str:
    """Short description of the page for Jev to judge.

    Prefer the words the page uses for the link. Fall back to the heading
    above it, then to words taken from the URL. The caller keeps the URL.
    """
    if content:
        anchor = _markdown_anchor(url, content)
        if anchor and _useful_label(anchor):
            return anchor
        heading = _preceding_heading(url, content)
        if heading:
            return heading
    return _url_words(url)


def _page_key(url: str) -> str:
    parsed = urlparse(unquote(url.strip()))
    host = parsed.netloc.lower().removeprefix("www.")
    path = unquote(parsed.path).rstrip("/")
    query = unquote(parsed.query)
    return f"{host}{path}?{query}" if query else f"{host}{path}"


def candidate_links(candidates: list[str], parent_url: str) -> list[str]:
    """Drop blanks, the parent page, and duplicate URLs. Keep the first spelling."""
    parent_key = _page_key(parent_url)
    seen: set[str] = set()
    kept: list[str] = []
    for url in candidates:
        if not url or not url.strip():
            continue
        key = _page_key(url)
        if not key or key == parent_key or key in seen:
            continue
        seen.add(key)
        kept.append(url)
    return kept


def build_jev_state(query: str, parent_url: str, parent_content: str, links: list[str]) -> dict:
    """Structured state. Each link is an object Jev can point at by path."""
    excerpt = " ".join(parent_content.split())[:PARENT_EXCERPT_CHARS]
    return {
        "query": query,
        "parent_page": parent_url,
        "parent_excerpt": excerpt,
        "links": [
            {
                "url": url,
                "label": link_label(url, parent_content),
                "recommendation": page_recommendation(url, parent_content),
            }
            for url in links
        ],
    }


def build_jev_questions(link_count: int) -> dict[str, Noul]:
    """One yes-or-no question per link.

    The question name is only a key for the response. Jev does not see it, so
    the instruction names ``links[i]`` directly.
    """
    questions: dict[str, Noul] = {}
    for index in range(link_count):
        questions[f"link_{index}"] = Noul(
            instructions=(
                f"Does `links[{index}]` add information about `query`? "
                f"A relevant link is a news story, article, report, document, or dataset a reader would open next. "
                f"An irrelevant link is social media, an icon, a chart, a map, a dashboard, a search page, "
                f"a photo gallery, a section index, or another view of this same page. "
                f"Prefer a link that adds facts about the query. "
                f"Prefer a link the page recommends as related. "
                f"When `links[{index}].recommendation` says the page recommends this link as related, answer yes, "
                f"unless that link is irrelevant. "
                f"An empty `links[{index}].recommendation` is not a reason to answer no. "
                f"Judge `links[{index}].label` and `links[{index}].url`."
            ),
            criteria={"true": _TRUE, "false": _FALSE},
        )
    return questions


def urls_above_threshold(
    links: list[str],
    probabilities: dict[str, float],
    threshold: float = JEV_RELEVANT_MIN,
) -> list[str]:
    """Keep links whose yes-probability is above the cutoff, in candidate order."""
    selected: list[str] = []
    for index, url in enumerate(links):
        probability = probabilities.get(f"link_{index}")
        if probability is not None and probability > threshold:
            selected.append(url)
    return selected


async def select_links_with_jev(
    *,
    query: str,
    parent_url: str,
    parent_content: str,
    candidates: list[str],
    model: str = JEV_MODEL,
    threshold: float = JEV_RELEVANT_MIN,
) -> tuple[list[str], str | None]:
    """Return candidate URLs that Jev judges relevant to ``query``.

    Reads ``TYPESAFE_API_KEY`` from the environment. URLs outside ``candidates``
    cannot appear: Jev only returns a probability for each link the caller listed.
    """
    links = candidate_links(candidates, parent_url)
    if not links:
        return [], None

    selected: list[str] = []
    try:
        async with AsyncTypeSafeClient(model=model, timeout=JEV_TIMEOUT_SECONDS) as client:
            for start in range(0, len(links), JEV_BATCH_SIZE):
                batch = links[start : start + JEV_BATCH_SIZE]
                response = await client.system_one(
                    build_jev_state(query, parent_url, parent_content, batch),
                    build_jev_questions(len(batch)),
                    timeout=JEV_TIMEOUT_SECONDS,
                )
                probabilities = {name: answer.noul for name, answer in response.nouls.items()}
                selected.extend(urls_above_threshold(batch, probabilities, threshold))
    except Exception as exc:
        return [], str(exc)

    return selected, None
