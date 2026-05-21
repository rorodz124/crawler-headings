from __future__ import annotations

import threading
from collections import deque
from contextlib import contextmanager
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
    try:
        page.goto(url, timeout=crawl_config.timeout_ms, wait_until="networkidle")
    except Exception:
        page.goto(url, timeout=max(crawl_config.timeout_ms // 2, 10_000), wait_until="domcontentloaded")

    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(crawl_config.settle_delay_ms)


def extract_links(page, current_url: str, base_netloc: str, crawl_config: CrawlConfig) -> set[str]:
    links = set()
    current_normalized = normalize_url(current_url)

    for link in page.query_selector_all("a[href]"):
        href = (link.get_attribute("href") or "").strip()
        if not href:
            continue

        absolute = normalize_url(urljoin(current_url, href))
        if (
            absolute != current_normalized
            and not should_ignore_url(absolute)
            and same_domain(absolute, base_netloc, crawl_config.include_subdomains)
        ):
            links.add(absolute)

    return links


def crawl_site(url_base: str, crawl_config: CrawlConfig, audit_config: AuditConfig, on_progress=None) -> dict:
    base_url = normalize_url(url_base)
    base_netloc = urlparse(base_url).netloc

    queue = deque([base_url])
    queued = {base_url}
    visited = set()
    in_progress = set()
    reports = []

    state_lock = threading.Lock()
    report_lock = threading.Lock()

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
                        load_page(page, url, crawl_config)
                        page_data = extract_headings(page)
                        report = validate_headings(page_data, audit_config)
                        new_links = extract_links(page, page.url, base_netloc, crawl_config)

                        with report_lock:
                            reports.append(report)

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
                                }
                            )
            finally:
                page.context.close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(crawl_config.crawler_workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    reports.sort(key=lambda item: item["url"])
    return {
        "base_url": base_url,
        "pages_crawled": len(reports),
        "pages_with_issues": sum(1 for report in reports if not report["valid"]),
        "reports": reports,
    }