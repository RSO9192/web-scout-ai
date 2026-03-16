import re
from urllib.parse import urlparse, urlunparse

def _normalize_url(url: str) -> str:
    # A standalone version of tracker._normalize_url
    p = urlparse(url)
    scheme = "https" if p.scheme in ("http", "https") else p.scheme
    return urlunparse(
        (scheme, p.netloc.lower(), p.path.rstrip("/"), p.params, p.query, "")
    )

def _judge_synthesis(synthesis: str, valid_urls: set[str]) -> list[str]:
    """Return a list of issue descriptions, empty if synthesis passes."""
    issues = []

    # 1. Detect bare URLs
    # Strip out valid markdown links: [Title](URL)
    text_without_md_links = re.sub(r'\[[^\]]*\]\((https?://[^\s\)]+)\)', '', synthesis)
    
    # Find any remaining http(s):// strings, cleaning up trailing punctuation
    bare_urls = [
        m.group().rstrip('.,;)"\'')
        for m in re.finditer(r'https?://\S+', text_without_md_links)
    ]
    # Remove empty strings in case regex leaves any
    bare_urls = [u for u in bare_urls if u]

    if bare_urls:
        issues.append(
            "Bare URLs found (must be wrapped as markdown links [Title](URL)): "
            + ", ".join(bare_urls[:5])
        )

    # 2. Detect hallucinated URLs
    md_link_urls = set(re.findall(r'\[[^\]]*\]\((https?://[^\s\)]+)\)', synthesis))
    
    valid_norm = {_normalize_url(u) for u in valid_urls}
    
    hallucinated = [u for u in md_link_urls if _normalize_url(u) not in valid_norm]
    if hallucinated:
        issues.append(
            "URLs cited that are NOT in the available sources (remove or replace): "
            + ", ".join(hallucinated[:5])
        )

    return issues

print(_judge_synthesis("Good [Link](https://valid.com). Bare https://bare.com", {"https://valid.com/"}))
