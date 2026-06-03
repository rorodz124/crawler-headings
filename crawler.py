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
    IGNORED_PATH_PREFIXES,
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
  const values = [];
  const add = (value) => {
    if (typeof value === "string" && value.trim()) values.push(value.trim());
  };

  document.querySelectorAll("a[href], area[href]").forEach((node) => {
    add(node.getAttribute("href"));
  });

  document.querySelectorAll("link[href][rel~='canonical'], link[href][rel~='alternate'], link[href][rel~='next'], link[href][rel~='prev']").forEach((node) => {
    add(node.getAttribute("href"));
  });

  return values;
}
"""

ABSOLUTE_URL_RE = re.compile(r"https?:\\?/\\?/[^\\\"'<>\s\])}]+", re.IGNORECASE)
ROOT_PATH_RE = re.compile(r"""["'=(:]\s*(/(?!/|_next/|assets/|uploads/)[A-Za-z0-9][^\\\"'<>\s\])}]*)""")

def should_ignore_url(url: str) -> bool:
    if not url:
        return True
    lowered = url.lower()
    if lowered.startswith(IGNORED_SCHEMES):
        return True
    parsed = urlparse(lowered)
    if any(parsed.path.startswith(prefix) for prefix in IGNORED_PATH_PREFIXES):
        return True
    filename = parsed.path.rsplit("/", 1)[-1]
    if "." in filename:
        return True
    return any(parsed.path.endswith(ext) for ext in IGNORED_EXTENSIONS)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").lower()
    filtered_qs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in TRACKING_PARAMS_EXACT
        and not any(key.startswith(prefix) for prefix in TRACKING_PARAMS_PREFIXES)
    ]
    filtered_qs.sort()
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse(parsed._replace(scheme=scheme, netloc=netloc, fragment="", query=urlencode(filtered_qs, doseq=True), path=path))


def url_identity(url: str) -> str:
    """Canonical key for dedupe without changing the URL that will be fetched."""
    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    return urlunparse(parsed._replace(netloc=canonical_netloc(parsed.netloc)))


def same_domain(url: str, base_netloc: str, include_subdomains: bool) -> bool:
    candidate = canonical_netloc(urlparse(url).netloc)
    base = canonical_netloc(base_netloc)
    if include_subdomains:
        return candidate == base or candidate.endswith("." + base)
    return candidate == base


def canonical_netloc(netloc: str) -> str:
    netloc = (netloc or "").lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def iter_candidate_hrefs(raw_value: str):
    value = (raw_value or "").strip()
    if not value:
        return
    if "<" not in value and len(value) <= 2048:
        yield value
    if "<" not in value and "http" not in value and "/" not in value:
        return
    for match in ABSOLUTE_URL_RE.finditer(value):
        yield match.group(0).replace("\\/", "/")
    for match in ROOT_PATH_RE.finditer(value):
        yield match.group(1).replace("\\/", "/")


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


def load_page(page, url: str, crawl_config: CrawlConfig):
    headings_selector = "h1, h2, h3, h4, h5, h6"
    h1_selector = "h1"
    page.set_default_timeout(crawl_config.timeout_ms)
    page.set_default_navigation_timeout(crawl_config.timeout_ms)
    # Fast path: avoid waiting for analytics/API calls that keep networkidle busy.
    try:
        response = page.goto(url, timeout=crawl_config.timeout_ms, wait_until="domcontentloaded")
    except Exception:
        response = page.goto(url, timeout=crawl_config.timeout_ms, wait_until="commit")

    if crawl_config.settle_delay_ms > 0:
        page.wait_for_timeout(crawl_config.settle_delay_ms)

    # Client-rendered pages can paint secondary headings before the main h1.
    try:
        page.wait_for_selector(h1_selector, timeout=crawl_config.extra_wait_for_headings_ms)
    except Exception:
        pass

    # If there is no h1, still give the page a bounded chance to render headings.
    try:
        page.wait_for_selector(headings_selector, timeout=crawl_config.extra_wait_for_headings_ms)
    except Exception:
        pass

    try:
        page.wait_for_load_state("networkidle", timeout=crawl_config.networkidle_timeout_ms)
    except Exception:
        pass

    if crawl_config.settle_delay_ms > 0:
        page.wait_for_timeout(crawl_config.settle_delay_ms)

    return response


def extract_links(page, current_url: str, base_netloc: str, crawl_config: CrawlConfig) -> set[str]:
    current_identity = url_identity(current_url)
    links = set()
    for raw_value in page.evaluate(EXTRACT_LINKS_SCRIPT):
        for href in iter_candidate_hrefs(raw_value):
            absolute = normalize_url(urljoin(current_url, href))
            if (
                url_identity(absolute) != current_identity
                and not should_ignore_url(absolute)
                and same_domain(absolute, base_netloc, crawl_config.include_subdomains)
            ):
                links.add(absolute)
    return links


def crawl_site(
    url_base: str,
    crawl_config: CrawlConfig,
    audit_config: AuditConfig,
    on_progress=None,
    should_cancel=None,
) -> dict:
    crawl_started_at = time.perf_counter()
    base_url = normalize_url(url_base)
    base_netloc = urlparse(base_url).netloc
    should_cancel = should_cancel or (lambda: False)
    has_page_limit = crawl_config.max_pages > 0

    queue = deque([base_url])
    known_urls = {url_identity(base_url)}
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

    state_condition = threading.Condition()
    report_lock = threading.Lock()
    timing_lock = threading.Lock()

    def take_next_url():
        with state_condition:
            while True:
                reached_page_limit = (
                    has_page_limit
                    and len(visited) + len(in_progress) >= crawl_config.max_pages
                )
                if should_cancel() or reached_page_limit:
                    return None

                if queue:
                    url = queue.popleft()
                    url_id = url_identity(url)
                    if url_id in visited or url_id in in_progress:
                        continue
                    in_progress.add(url_id)
                    return url

                if not in_progress:
                    return None

                state_condition.wait(timeout=0.25)

    def worker():
        # Playwright sync API não permite partilhar objetos entre threads.
        # Cada worker abre o seu próprio browser dentro da própria thread.
        with open_browser() as browser:
            page = new_page(browser, crawl_config)
            try:
                while True:
                    url = take_next_url()
                    if url is None:
                        break

                    try:
                        if should_cancel():
                            with state_condition:
                                in_progress.discard(url_identity(url))
                                state_condition.notify_all()
                            break

                        page_started_at = time.perf_counter()

                        load_started_at = time.perf_counter()
                        response = load_page(page, url, crawl_config)
                        load_seconds = time.perf_counter() - load_started_at
                        final_url = normalize_url(page.url)
                        status = response.status if response else None
                        if status and status >= 400:
                            raise RuntimeError(f"HTTP {status}")

                        with state_condition:
                            url_id = url_identity(url)
                            final_id = url_identity(final_url)
                            duplicate_after_redirect = (
                                final_id != url_id
                                and (final_id in visited or final_id in in_progress)
                            )
                            if duplicate_after_redirect:
                                in_progress.discard(url_id)
                                state_condition.notify_all()
                                continue
                            if final_id != url_id:
                                known_urls.add(final_id)
                                in_progress.discard(url_id)
                                in_progress.add(final_id)

                        headings_started_at = time.perf_counter()
                        page_data = extract_headings(page)
                        page_data["url"] = final_url
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
                            pages_with_issues = sum(1 for item in reports if not item["valid"])
                        with timing_lock:
                            timing_totals["load_seconds"] += load_seconds
                            timing_totals["headings_seconds"] += headings_seconds
                            timing_totals["validation_seconds"] += validation_seconds
                            timing_totals["links_seconds"] += links_seconds
                            timing_totals["page_total_seconds"] += page_total_seconds

                        with state_condition:
                            url_id = url_identity(url)
                            final_id = url_identity(final_url)
                            in_progress.discard(url_id)
                            in_progress.discard(final_id)
                            visited.add(final_id)
                            if not should_cancel():
                                for link in new_links:
                                    link_id = url_identity(link)
                                    if link_id not in visited and link_id not in in_progress and link_id not in known_urls:
                                        queue.append(link)
                                        known_urls.add(link_id)
                            current_count = len(visited)
                            state_condition.notify_all()

                        if on_progress:
                            on_progress(
                                current_count,
                                crawl_config.max_pages,
                                report["url"],
                                report["summary"]["issue_count"],
                                report["timings"],
                                pages_with_issues,
                                report=report,
                            )
                    except Exception as exc:
                        error_report = {
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
                        with state_condition:
                            url_id = url_identity(url)
                            in_progress.discard(url_id)
                            visited.add(url_id)
                            current_count = len(visited)
                            state_condition.notify_all()
                        with report_lock:
                            reports.append(error_report)
                            pages_with_issues = sum(1 for item in reports if not item["valid"])
                        if on_progress:
                            on_progress(
                                current_count,
                                crawl_config.max_pages,
                                url,
                                1,
                                {},
                                pages_with_issues,
                                report=error_report,
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