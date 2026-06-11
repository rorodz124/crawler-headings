import os
from dataclasses import dataclass

CRAWLER_WORKERS = min(max((os.cpu_count() or 4) + 2, 4), 6)
NETWORKIDLE_TIMEOUT_MS = 2_500
SETTLE_DELAY_MS = 750
EXTRA_WAIT_FOR_HEADINGS_MS = 2_500

IGNORED_SCHEMES = ("mailto:", "tel:", "javascript:", "data:")
IGNORED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".exe",
    ".ppt", ".pptx", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".ico", ".css", ".js", ".mjs", ".map", ".json", ".woff", ".woff2",
    ".ttf", ".otf", ".eot", ".kmz", ".kml", ".mp3", ".mp4", ".avi",
    ".mov", ".wmv", ".csv", ".xml",
}

IGNORED_PATH_PREFIXES = ("/_next/", "/assets/", "/uploads/")

TRACKING_PARAMS_PREFIXES = ("utm_",)
TRACKING_PARAMS_EXACT = {"fbclid", "gclid", "mc_cid", "mc_eid", "lang", "device"}


@dataclass(frozen=True)
class CrawlConfig:
    max_pages: int = 0
    crawler_workers: int = CRAWLER_WORKERS
    timeout_ms: int = 15_000
    networkidle_timeout_ms: int = NETWORKIDLE_TIMEOUT_MS
    settle_delay_ms: int = SETTLE_DELAY_MS
    extra_wait_for_headings_ms: int = EXTRA_WAIT_FOR_HEADINGS_MS
    include_subdomains: bool = False
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    blocked_resource_types: tuple[str, ...] = ("font", "image", "media", "websocket", "manifest")


@dataclass(frozen=True)
class AuditConfig:
    require_single_h1: bool = True
    validate_visible_only: bool = True
    ignore_hidden_in_report: bool = False
    empty_text_tokens: tuple[str, ...] = ("\xa0",)
    strip_chars: str = " \t\r\n"