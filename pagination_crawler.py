from __future__ import annotations

import re
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from config import AuditConfig, CrawlConfig
from crawler import (
    _extract_page_number,
    extract_headings,
    load_page,
    new_page,
    normalize_url,
    open_browser,
    should_ignore_url,
)
from heading_rules import validate_headings


def _set_page_number_query(url: str, number: int) -> str:
    parsed = urlparse(url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(query_pairs)
    for key in ("page", "pagina", "paged", "p"):
        if key in query:
            query[key] = str(number)
            return normalize_url(urlunparse(parsed._replace(query=urlencode(query, doseq=True))))
    query["page"] = str(number)
    return normalize_url(urlunparse(parsed._replace(query=urlencode(query, doseq=True))))


def _set_page_number_path(url: str, number: int) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    replacements = (
        (r"/pagina/\d+$", f"/pagina/{number}"),
        (r"/page/\d+$", f"/page/{number}"),
        (r"-(\d+)$", f"-{number}"),
        (r"/(\d+)$", f"/{number}"),
    )
    for pattern, replacement in replacements:
        updated = re.sub(pattern, replacement, path, flags=re.IGNORECASE)
        if updated != path:
            return normalize_url(urlunparse(parsed._replace(path=updated)))
    return normalize_url(urlunparse(parsed._replace(path=f"{path}/pagina/{number}")))


def _pagination_fallback_urls(url_base: str, number: int) -> list[str]:
    candidates = []
    for candidate in (_set_page_number_query(url_base, number), _set_page_number_path(url_base, number)):
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _pagination_links(page, current_url: str) -> dict[str, int | None]:
    links: dict[str, int | None] = {}
    for link in page.query_selector_all("a[href]"):
        href = (link.get_attribute("href") or "").strip()
        if not href:
            continue
        destination = normalize_url(urljoin(current_url, href))
        if should_ignore_url(destination):
            continue

        signals = " ".join(
            [
                (link.inner_text() or "").lower(),
                (link.get_attribute("aria-label") or "").lower(),
                (link.get_attribute("class") or "").lower(),
                (link.get_attribute("rel") or "").lower(),
            ]
        )
        page_number = _extract_page_number(destination)
        if page_number is not None:
            links[destination] = page_number
        elif any(token in signals for token in ("proxima", "próxima", "next", "seguinte")):
            links[destination] = None
    return links


def _is_card_link(destination: str, list_url: str) -> bool:
    d = urlparse(normalize_url(destination))
    b = urlparse(normalize_url(list_url))
    return (
        d.netloc == b.netloc
        and d.path.rstrip("/") != b.path.rstrip("/")
        and d.path.startswith(b.path.rstrip("/") + "/")
        and _extract_page_number(normalize_url(destination)) is None
        and not should_ignore_url(normalize_url(destination))
    )


def _collect_card_links(page, list_url: str) -> list[str]:
    seen = set()
    for link in page.query_selector_all("a[href]"):
        href = (link.get_attribute("href") or "").strip()
        if not href:
            continue
        destination = urljoin(page.url, href)
        if _is_card_link(destination, list_url):
            seen.add(normalize_url(destination))
    return sorted(seen)


def _load_discovery_page(page, url: str, crawl_config: CrawlConfig) -> None:
    timeout = min(crawl_config.timeout_ms, 12_000)
    page.goto(url, timeout=timeout, wait_until="domcontentloaded")
    if crawl_config.settle_delay_ms > 0:
        page.wait_for_timeout(min(crawl_config.settle_delay_ms, 500))


def _discover_pagination_urls(page, start_url: str, crawl_config: CrawlConfig, on_discovery_progress=None) -> list[str]:
    visited: list[str] = []
    seen = set()
    seen_card_signatures: set[tuple[str, ...]] = set()
    current = normalize_url(start_url)
    fallback_pending: list[str] = []
    hard_limit = crawl_config.max_pages if crawl_config.max_pages > 0 else 200

    while current and current not in seen and len(visited) < hard_limit:
        _load_discovery_page(page, current, crawl_config)
        current = normalize_url(page.url)
        if current in seen:
            break

        card_links = _collect_card_links(page, start_url)
        card_signature = tuple(card_links[:20])

        if on_discovery_progress:
            on_discovery_progress(len(visited) + 1, current)

        if visited and not card_links:
            current = fallback_pending.pop(0) if fallback_pending else None
            continue

        if card_signature and card_signature in seen_card_signatures:
            break
        seen_card_signatures.add(card_signature)

        seen.add(current)
        visited.append(current)

        current_number = _extract_page_number(current) or len(visited)
        links = _pagination_links(page, current)
        next_url = next((url for url, number in links.items() if number == current_number + 1), None)

        if not next_url:
            fallback_pending = [u for u in _pagination_fallback_urls(start_url, current_number + 1) if u not in seen]
            next_url = fallback_pending.pop(0) if fallback_pending else None

        if next_url in seen:
            break
        current = next_url

    return visited


def crawl_pagination(
    url_start: str,
    crawl_config: CrawlConfig,
    audit_config: AuditConfig,
    on_progress=None,
    on_discovery_progress=None,
    on_phase_change=None,
) -> dict:
    crawl_started_at = time.perf_counter()
    start_url = normalize_url(url_start)
    reports: list[dict] = []

    if on_discovery_progress:
        on_discovery_progress(0, start_url)

    with open_browser() as browser:
        discovery_page = new_page(browser, crawl_config)
        try:
            discovered_urls = _discover_pagination_urls(
                discovery_page,
                start_url,
                crawl_config,
                on_discovery_progress=on_discovery_progress,
            )
        finally:
            try:
                discovery_page.context.close()
            except Exception:
                pass

    if on_phase_change:
        on_phase_change("discovery_done", len(discovered_urls))
        on_phase_change("validation_start", len(discovered_urls))

    with open_browser() as browser:
        page = new_page(browser, crawl_config)
        try:
            total = len(discovered_urls)
            for index, url in enumerate(discovered_urls, start=1):
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

                    page_total_seconds = time.perf_counter() - page_started_at
                    report["timings"] = {
                        "load_seconds": round(load_seconds, 4),
                        "headings_seconds": round(headings_seconds, 4),
                        "validation_seconds": round(validation_seconds, 4),
                        "links_seconds": 0.0,
                        "page_total_seconds": round(page_total_seconds, 4),
                    }
                    reports.append(report)

                    if on_progress:
                        on_progress(index, total, report["url"], report["summary"]["issue_count"], report["timings"])
                except Exception as exc:
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
            try:
                page.context.close()
            except Exception:
                pass

    total_elapsed_seconds = time.perf_counter() - crawl_started_at
    return {
        "base_url": start_url,
        "pages_crawled": len(reports),
        "pages_with_issues": sum(1 for report in reports if not report["valid"]),
        "timings": {"total_elapsed_seconds": round(total_elapsed_seconds, 4)},
        "reports": sorted(reports, key=lambda item: item.get("url", "")),
    }
