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
        # Consistently paywalled academic publishers (thin HTML without subscription)
        "sciencedirect.com",
        "springer.com",
        "link.springer.com",
        "wiley.com",
        "onlinelibrary.wiley.com",
        "tandfonline.com",
        "sagepub.com",
        "cambridge.org",
        "jstor.org",
        # NOTE: open-access publishers (frontiersin.org, mdpi.com, journals.plos.org) and
        # abstract-available publishers (researchgate.net, nature.com, academic.oup.com)
        # are intentionally NOT blocked — they yield useful content for research queries.
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

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
