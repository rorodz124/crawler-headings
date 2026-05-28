from __future__ import annotations

import threading
import time
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


EXTRACT_LINKS_SCRIPT = """
() => {
  return Array.from(document.querySelectorAll("a[href]"), (link) => link.getAttribute("href") || "")
    .map((href) => href.trim())
    .filter(Boolean);
}
"""


def should_ignore_url(url: str) -> bool:
    if not url:
        return True
    lowered = url.lower()
    if lowered.startswith(IGNORED_SCHEMES):
        return True
    return any(urlparse(lowered).path.endswith(ext) for ext in IGNORED_EXTENSIONS)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    filtered_qs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in TRACKING_PARAMS_EXACT
        and not any(key.startswith(prefix) for prefix in TRACKING_PARAMS_PREFIXES)
    ]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse(parsed._replace(fragment="", query=urlencode(filtered_qs, doseq=True), path=path))


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

    if page.locator(headings_selector).count() > 0:
        if crawl_config.settle_delay_ms > 0:
            page.wait_for_timeout(crawl_config.settle_delay_ms)
        return

    page.wait_for_timeout(crawl_config.extra_wait_for_headings_ms)
    if page.locator(headings_selector).count() == 0 and crawl_config.settle_delay_ms > 0:
        page.wait_for_timeout(crawl_config.settle_delay_ms)


def extract_links(page, current_url: str, base_netloc: str, crawl_config: CrawlConfig) -> set[str]:
    current_normalized = normalize_url(current_url)
    links = set()
    for href in page.evaluate(EXTRACT_LINKS_SCRIPT):
        absolute = normalize_url(urljoin(current_url, href))
        if (
            absolute != current_normalized
            and not should_ignore_url(absolute)
            and same_domain(absolute, base_netloc, crawl_config.include_subdomains)
        ):
            links.add(absolute)
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
        # Playwright sync API não permite partilhar objetos entre threads.
        # Cada worker abre o seu próprio browser dentro da própria thread.
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
                            reports.append({
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
                            })
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