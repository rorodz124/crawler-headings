from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from playwright.sync_api import sync_playwright

from config import (
    IGNORED_EXTENSIONS,
    IGNORED_SCHEMES,
    TRACKING_PARAMS_EXACT,
    TRACKING_PARAMS_PREFIXES,
    AuditConfig,
    CrawlConfig,
)
from heading_extractor import extract_headings
from heading_rules import validate_headings

EXTRACT_LINKS_SCRIPT = """
() => {
  return Array.from(document.querySelectorAll("a[href]"), (link) => link.getAttribute("href") || "")
    .map((href) => href.trim())
    .filter(Boolean);
}
"""

EXTRACT_PAGINATION_HINTS_SCRIPT = """
() => {
  const selectors = [
    "a[rel='next']",
    "a[aria-label*='next' i]",
    "a[aria-label*='seguinte' i]",
    "a[aria-label*='proxima' i]",
    "a[aria-label*='próxima' i]",
    ".pagination a[href]",
    "nav[aria-label*='pagination' i] a[href]",
    "nav[aria-label*='paginacao' i] a[href]",
  ];
  const nodes = Array.from(document.querySelectorAll(selectors.join(",")));
  return nodes
    .map((link) => (link.getAttribute("href") || "").trim())
    .filter(Boolean);
}
"""


def _extract_page_number(url: str) -> int | None:
    parsed = urlparse(url)
    query_values = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key in ("page", "pagina", "paged", "p"):
        value = query_values.get(key)
        if value and value.isdigit():
            return int(value)

    path = parsed.path.rstrip("/")
    for pattern in (r"/page/(\\d+)$", r"/pagina/(\\d+)$", r"-(\\d+)$", r"/(\\d+)$"):
        match = re.search(pattern, path, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _build_next_pagination_candidates(current_url: str) -> list[str]:
    parsed = urlparse(current_url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(query_pairs)
    current_page_num = _extract_page_number(current_url) or 1
    next_page_num = current_page_num + 1
    candidates = []

    for key in ("page", "pagina", "paged", "p"):
        if key in query:
            query[key] = str(next_page_num)
            candidates.append(
                normalize_url(urlunparse(parsed._replace(query=urlencode(query, doseq=True))))
            )

    if not any(key in query for key in ("page", "pagina", "paged", "p")):
        query_with_page = dict(query)
        query_with_page["page"] = str(next_page_num)
        candidates.append(
            normalize_url(urlunparse(parsed._replace(query=urlencode(query_with_page, doseq=True))))
        )

    path = parsed.path.rstrip("/")
    path_candidates = (
        (r"/page/\\d+$", f"/page/{next_page_num}"),
        (r"/pagina/\\d+$", f"/pagina/{next_page_num}"),
        (r"-(\\d+)$", f"-{next_page_num}"),
        (r"/(\\d+)$", f"/{next_page_num}"),
    )
    path_replaced = False
    for pattern, replacement in path_candidates:
        updated_path = re.sub(pattern, replacement, path, flags=re.IGNORECASE)
        if updated_path != path:
            candidates.append(normalize_url(urlunparse(parsed._replace(path=updated_path))))
            path_replaced = True
            break

    if not path_replaced:
        candidates.append(normalize_url(urlunparse(parsed._replace(path=f"{path}/page/{next_page_num}"))))

    unique = []
    seen = set()
    for candidate in candidates:
        if candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique



def should_ignore_url(url: str) -> bool:
    if not url:
        return True
    lowered = url.lower()
    if lowered.startswith(IGNORED_SCHEMES):
        return True
    return any(urlparse(lowered).path.endswith(ext) for ext in IGNORED_EXTENSIONS)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    filtered_qs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in TRACKING_PARAMS_EXACT:
            continue
        if any(key.startswith(prefix) for prefix in TRACKING_PARAMS_PREFIXES):
            continue
        filtered_qs.append((key, value))

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    normalized = parsed._replace(
        fragment="",
        query=urlencode(filtered_qs, doseq=True),
        path=path,
    )
    return urlunparse(normalized)


def same_domain(url: str, base_netloc: str, include_subdomains: bool) -> bool:
    candidate = urlparse(url).netloc.lower()
    base = base_netloc.lower()
    if include_subdomains:
        return candidate == base or candidate.endswith("." + base)
    return candidate == base


@contextmanager
def open_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


def new_page(browser, crawl_config: CrawlConfig):
    context = browser.new_context(
        user_agent=crawl_config.user_agent,
        viewport={"width": 1280, "height": 800},
        java_script_enabled=True,
    )
    page = context.new_page()
    page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in crawl_config.blocked_resource_types
        else route.continue_(),
    )
    return page


def load_page(page, url: str, crawl_config: CrawlConfig) -> None:
    headings_selector = "h1, h2, h3, h4, h5, h6"

    try:
        page.goto(
            url,
            timeout=min(crawl_config.timeout_ms, crawl_config.networkidle_timeout_ms),
            wait_until="networkidle",
        )
    except Exception:
        page.goto(url, timeout=max(crawl_config.timeout_ms // 2, 10_000), wait_until="domcontentloaded")

    heading_count = page.locator(headings_selector).count()
    if heading_count > 0:
        if crawl_config.settle_delay_ms > 0:
            page.wait_for_timeout(crawl_config.settle_delay_ms)
        return

    page.wait_for_timeout(crawl_config.extra_wait_for_headings_ms)
    heading_count = page.locator(headings_selector).count()
    if heading_count == 0 and crawl_config.settle_delay_ms > 0:
        page.wait_for_timeout(crawl_config.settle_delay_ms)


def extract_links(page, current_url: str, base_netloc: str, crawl_config: CrawlConfig) -> set[str]:
    links = set()
    current_normalized = normalize_url(current_url)
    hrefs = page.evaluate(EXTRACT_LINKS_SCRIPT)
    pagination_hints = page.evaluate(EXTRACT_PAGINATION_HINTS_SCRIPT)
    discovered_hrefs = list(hrefs) + list(pagination_hints)

    for href in discovered_hrefs:
        absolute = normalize_url(urljoin(current_url, href))
        if (
            absolute != current_normalized
            and not should_ignore_url(absolute)
            and same_domain(absolute, base_netloc, crawl_config.include_subdomains)
        ):
            links.add(absolute)

    # Fallback para listagens onde o "next" depende de JS.
    if pagination_hints:
        for candidate in _build_next_pagination_candidates(current_url):
            if (
                candidate != current_normalized
                and not should_ignore_url(candidate)
                and same_domain(candidate, base_netloc, crawl_config.include_subdomains)
            ):
                links.add(candidate)

    return links


def crawl_site(url_base: str, crawl_config: CrawlConfig, audit_config: AuditConfig, on_progress=None) -> dict:
    crawl_started_at = time.perf_counter()
    base_url = normalize_url(url_base)
    base_netloc = urlparse(base_url).netloc

    queue = deque([base_url])
    queued = {base_url}
    visited = set()
    in_progress = set()
    reports = []
    timing_totals = {
        "load_seconds": 0.0,
        "headings_seconds": 0.0,
        "links_seconds": 0.0,
        "validation_seconds": 0.0,
        "page_total_seconds": 0.0,
    }

    state_lock = threading.Lock()
    report_lock = threading.Lock()
    timing_lock = threading.Lock()

    def worker():
        with open_browser() as browser:
            page = new_page(browser, crawl_config)
            try:
                while True:
                    with state_lock:
                        if not queue or len(visited) + len(in_progress) >= crawl_config.max_pages:
                            break
                        url = queue.popleft()
                        if url in visited or url in in_progress:
                            continue
                        in_progress.add(url)

                    try:
                        page_started_at = time.perf_counter()

                        load_started_at = time.perf_counter()
                        load_page(page, url, crawl_config)
                        load_seconds = time.perf_counter() - load_started_at

                        headings_started_at = time.perf_counter()
                        page_data = extract_headings(page)
                        headings_seconds = time.perf_counter() - headings_started_at

                        validation_started_at = time.perf_counter()
                        report = validate_headings(page_data, audit_config)
                        validation_seconds = time.perf_counter() - validation_started_at

                        links_started_at = time.perf_counter()
                        new_links = extract_links(page, page.url, base_netloc, crawl_config)
                        links_seconds = time.perf_counter() - links_started_at

                        page_total_seconds = time.perf_counter() - page_started_at
                        report["timings"] = {
                            "load_seconds": round(load_seconds, 4),
                            "headings_seconds": round(headings_seconds, 4),
                            "validation_seconds": round(validation_seconds, 4),
                            "links_seconds": round(links_seconds, 4),
                            "page_total_seconds": round(page_total_seconds, 4),
                        }

                        with report_lock:
                            reports.append(report)
                        with timing_lock:
                            timing_totals["load_seconds"] += load_seconds
                            timing_totals["headings_seconds"] += headings_seconds
                            timing_totals["validation_seconds"] += validation_seconds
                            timing_totals["links_seconds"] += links_seconds
                            timing_totals["page_total_seconds"] += page_total_seconds

                        with state_lock:
                            in_progress.discard(url)
                            visited.add(url)
                            for link in new_links:
                                if link not in visited and link not in in_progress and link not in queued:
                                    queue.append(link)
                                    queued.add(link)
                            current_count = len(visited)

                        if on_progress:
                            on_progress(
                                current_count,
                                crawl_config.max_pages,
                                report["url"],
                                report["summary"]["issue_count"],
                                report["timings"],
                            )
                    except Exception as exc:
                        with state_lock:
                            in_progress.discard(url)
                            visited.add(url)

                        with report_lock:
                            reports.append(
                                {
                                    "url": url,
                                    "title": "",
                                    "headings": [],
                                    "considered_headings": [],
                                    "issues": [{"rule": "page_error", "message": str(exc)}],
                                    "valid": False,
                                    "summary": {
                                        "total_headings": 0,
                                        "considered_headings": 0,
                                        "h1_count": 0,
                                        "issue_count": 1,
                                    },
                                    "timings": {},
                                }
                            )
            finally:
                page.context.close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(crawl_config.crawler_workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    total_elapsed_seconds = time.perf_counter() - crawl_started_at
    pages_crawled = len(reports)
    average_page_seconds = (timing_totals["page_total_seconds"] / pages_crawled) if pages_crawled else 0.0
    pages_per_second = (pages_crawled / total_elapsed_seconds) if total_elapsed_seconds > 0 else 0.0

    reports.sort(key=lambda item: item["url"])
    return {
        "base_url": base_url,
        "pages_crawled": pages_crawled,
        "pages_with_issues": sum(1 for report in reports if not report["valid"]),
        "timings": {
            "total_elapsed_seconds": round(total_elapsed_seconds, 4),
            "average_page_seconds": round(average_page_seconds, 4),
            "pages_per_second": round(pages_per_second, 4),
            "load_seconds": round(timing_totals["load_seconds"], 4),
            "headings_seconds": round(timing_totals["headings_seconds"], 4),
            "validation_seconds": round(timing_totals["validation_seconds"], 4),
            "links_seconds": round(timing_totals["links_seconds"], 4),
            "measured_page_total_seconds": round(timing_totals["page_total_seconds"], 4),
        },
        "reports": reports,
    }

