import os
from dataclasses import dataclass, field


START_URL = "https://cm-barrancos.pt/municipio"
CRAWLER_WORKERS = min(os.cpu_count() or 4, 4)
DEFAULT_MAX_PAGES = 200
DEFAULT_TIMEOUT_MS = 30_000
SETTLE_DELAY_MS = 750
INCLUDE_SUBDOMAINS = False
VALIDATE_VISIBLE_ONLY = True
WRITE_JSON_REPORT = True
REPORT_DIR = "relatorios"

IGNORED_SCHEMES = ("mailto:", "tel:", "javascript:", "data:")
IGNORED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
    ".rar",
    ".exe",
    ".ppt",
    ".pptx",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
}
TRACKING_PARAMS_PREFIXES = ("utm_",)
TRACKING_PARAMS_EXACT = {"fbclid", "gclid", "mc_cid", "mc_eid"}


@dataclass(frozen=True)
class CrawlConfig:
    max_pages: int = DEFAULT_MAX_PAGES
    crawler_workers: int = CRAWLER_WORKERS
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    settle_delay_ms: int = SETTLE_DELAY_MS
    include_subdomains: bool = INCLUDE_SUBDOMAINS
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    blocked_resource_types: tuple[str, ...] = ("font", "media", "websocket", "manifest")


@dataclass(frozen=True)
class AuditConfig:
    require_single_h1: bool = True
    validate_visible_only: bool = VALIDATE_VISIBLE_ONLY
    ignore_hidden_in_report: bool = False
    empty_text_tokens: tuple[str, ...] = ("\xa0",)
    strip_chars: str = " \t\r\n"
    report_dir: str = field(default=REPORT_DIR)