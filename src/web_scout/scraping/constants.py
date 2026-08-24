BLOCKED_DOMAINS = frozenset(
    {
        # Social media and video platforms
        "youtube.com",
        "youtu.be",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "tiktok.com",
        "reddit.com",
        # Search engines
        "scholar.google.com",
        # Hard-paywalled publishers (login required even for abstracts)
        "jstor.org",
        # NOTE: open-access publishers (frontiersin.org, mdpi.com, journals.plos.org) and
        # abstract-available publishers (researchgate.net, nature.com, academic.oup.com)
        # are intentionally NOT blocked — they yield useful content for research queries.
        # Cloudflare-protected publishers (wiley, sciencedirect, cambridge, tandfonline,
        # springer, sagepub) were unblocked 2026-08-21: shared stealth sessions
        # (_stealth_session.py) scrape them reliably, validated under parallel load.
    }
)

BINARY_CONTENT_TYPES = (
    "video/",
    "audio/",
    "application/zip",
    "application/octet-stream",
    "application/x-tar",
    "application/x-rar",
)

IMAGE_CONTENT_TYPES = ("image/",)

JSON_CONTENT_TYPES = (
    "application/json",
    "application/geo+json",
    "application/ld+json",
    "application/vnd.api+json",
    "text/json",
)

SUPPORTED_DOC_CONTENT_TYPES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument",
)

UNSUPPORTED_LEGACY_DOC_CONTENT_TYPES = (
    "application/msword",
    "application/vnd.ms-",
)

DOC_CONTENT_TYPES = SUPPORTED_DOC_CONTENT_TYPES + UNSUPPORTED_LEGACY_DOC_CONTENT_TYPES

SUPPORTED_DOC_EXTENSIONS = (".pdf", ".docx", ".pptx", ".xlsx")
UNSUPPORTED_LEGACY_DOC_EXTENSIONS = (".doc", ".xls", ".ppt")
DOC_EXTENSIONS = SUPPORTED_DOC_EXTENSIONS + UNSUPPORTED_LEGACY_DOC_EXTENSIONS

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

FETCH_HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

LINKS_SECTION_HEADING = "### Links on Page:"

PDF_MAGIC_BYTES = b"%PDF"
