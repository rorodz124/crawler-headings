from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from config import (
    IGNORED_EXTENSIONS, IGNORED_PATH_PREFIXES, IGNORED_SCHEMES,
    TRACKING_PARAMS_EXACT, TRACKING_PARAMS_PREFIXES, CrawlConfig,
)

PAGINATION_PATTERNS = [
    re.compile(r"[?&]page=\d+", re.IGNORECASE),
    re.compile(r"[?&]p=\d+", re.IGNORECASE),
    re.compile(r"[?&]paged=\d+", re.IGNORECASE),
    re.compile(r"[?&]offset=\d+", re.IGNORECASE),
    re.compile(r"/page/\d+", re.IGNORECASE),
    re.compile(r"/pagina/\d+", re.IGNORECASE),
    re.compile(r"/p/\d+", re.IGNORECASE),
]


def canonical_host(netloc: str) -> str:
    netloc = (netloc or "").lower().strip()
    return netloc[4:] if netloc.startswith("www.") else netloc


def normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
    except Exception:
        return url
    clean_qs = sorted(
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k not in TRACKING_PARAMS_EXACT
        and not any(k.startswith(pfx) for pfx in TRACKING_PARAMS_PREFIXES)
    )
    path = (p.path or "/").rstrip("/") or "/"
    return urlunparse(p._replace(
        scheme=p.scheme.lower(), netloc=p.netloc.lower(),
        path=path, query=urlencode(clean_qs, doseq=True), fragment="",
    ))


def url_key(url: str) -> str:
    #Chave de deduplicação — normalizado + sem www.
    p = urlparse(normalize_url(url))
    return urlunparse(p._replace(netloc=canonical_host(p.netloc)))


def is_same_domain(url: str, base_host: str, include_subdomains: bool) -> bool:
    c = canonical_host(urlparse(url).netloc)
    b = canonical_host(base_host)
    return c == b or (include_subdomains and c.endswith("." + b))


def should_skip(url: str) -> bool:
    if not url:
        return True
    lower = url.lower()
    if any(lower.startswith(s) for s in IGNORED_SCHEMES):
        return True
    try:
        p = urlparse(lower)
    except Exception:
        return True
    if any(p.path.startswith(pfx) for pfx in IGNORED_PATH_PREFIXES):
        return True
    last = p.path.rsplit("/", 1)[-1]
    if "." in last and ("." + last.rsplit(".", 1)[-1]) in IGNORED_EXTENSIONS:
        return True
    return False


def is_pagination(url: str) -> bool:
    return any(pat.search(url) for pat in PAGINATION_PATTERNS)


def fetch_sitemap_urls(base_url: str, page, crawl_config: CrawlConfig) -> list[str]:
    #Tenta /sitemap.xml
    parsed = urlparse(base_url)
    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    base_host = parsed.netloc
    found: list[str] = []

    def _parse_urlset(content: str) -> list[str]:
        clean = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", content)
        m = re.search(r"(<\?xml.*|<sitemapindex|<urlset).*", clean, re.DOTALL)
        if not m:
            return []
        root = ET.fromstring(m.group(0))
        return [loc.text.strip() for loc in root.iter("loc") if loc.text]

    try:
        page.set_default_timeout(crawl_config.timeout_ms)
        r = page.goto(sitemap_url, timeout=crawl_config.timeout_ms, wait_until="domcontentloaded")
        if not r or r.status >= 400:
            return []

        locs = _parse_urlset(page.content())
        # Sitemap index: os locs apontam para outros sitemaps
        if any(u.endswith(".xml") for u in locs):
            for child in locs[:10]:
                try:
                    r2 = page.goto(child, timeout=crawl_config.timeout_ms, wait_until="domcontentloaded")
                    if r2 and r2.status < 400:
                        found.extend(_parse_urlset(page.content()))
                except Exception:
                    continue
        else:
            found = locs

    except Exception:
        return []

    return [u for u in found if is_same_domain(u, base_host, crawl_config.include_subdomains)]