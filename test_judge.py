import re

def _judge_synthesis(synthesis: str, valid_urls: set[str]) -> list[str]:
    issues = []
    
    # 1. Detect bare URLs. We replace valid md links with empty string, then look for https?://
    # We use a non-greedy or simple match for the link.
    text_without_md_links = re.sub(r'\[[^\]]*\]\((https?://[^\s\)]+)\)', '', synthesis)
    
    # Find remaining bare URLs, stripping trailing punctuation often matched by \S
    bare_urls = [
        m.group().rstrip('.,;)') 
        for m in re.finditer(r'https?://\S+', text_without_md_links)
    ]
    # Filter out empty strings if any
    bare_urls = [u for u in bare_urls if u]

    if bare_urls:
        issues.append(
            "Bare URLs found (must be wrapped as markdown links [Title](URL)): "
            + ", ".join(bare_urls[:5])
        )

    # 2. Detect hallucinated URLs
    md_link_urls = set(re.findall(r'\[[^\]]*\]\((https?://[^\s\)]+)\)', synthesis))
    
    # We need a dummy normalize for testing
    def normalize(u): return u.rstrip('/')
    valid_norm = {normalize(u) for u in valid_urls}
    
    hallucinated = [u for u in md_link_urls if normalize(u) not in valid_norm]
    if hallucinated:
        issues.append(
            "URLs cited that are NOT in the available sources (remove or replace): "
            + ", ".join(hallucinated[:5])
        )

    return issues

print(_judge_synthesis("Text [link](http://valid.com) and bare http://bare.com.", {"http://valid.com/"}))
print(_judge_synthesis("Text [link](http://invalid.com/)", {"http://valid.com/"}))
