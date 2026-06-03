from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

from config import AuditConfig, CrawlConfig
from heading_extractor import extract_headings
from heading_rules import validate_headings
from url_utils import ( fetch_sitemap_urls, is_pagination, is_same_domain,
    normalize_url, should_skip, url_key,)

_EXTRACT_LINKS_JS = """
() => {
  const s = new Set();
  document.querySelectorAll("a[href], area[href]").forEach(el => {
    const v = (el.getAttribute("href") || "").trim();
    if (v) s.add(v);
  });
  document.querySelectorAll(
    "link[rel~='canonical'], link[rel~='alternate'], link[rel~='next'], link[rel~='prev']"
  ).forEach(el => { const v = (el.getAttribute("href") || "").trim(); if (v) s.add(v); });
  return Array.from(s);
}
"""

@contextmanager
def _browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def _new_page(browser, cfg: CrawlConfig):
    ctx = browser.new_context(user_agent=cfg.user_agent, viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.route("**/*", lambda r: r.abort() if r.request.resource_type in cfg.blocked_resource_types else r.continue_())
    return page


def _load_page(page, url: str, cfg: CrawlConfig):
    page.set_default_timeout(cfg.timeout_ms)
    page.set_default_navigation_timeout(cfg.timeout_ms)
    try:
        resp = page.goto(url, timeout=cfg.timeout_ms, wait_until="domcontentloaded")
    except Exception:
        try:
            resp = page.goto(url, timeout=cfg.timeout_ms, wait_until="commit")
        except Exception as exc:
            raise RuntimeError(f"Falha ao navegar: {exc}") from exc

    if cfg.settle_delay_ms > 0:
        page.wait_for_timeout(cfg.settle_delay_ms)
    for sel in ("h1", "h1, h2, h3, h4, h5, h6"):
        try:
            page.wait_for_selector(sel, timeout=cfg.extra_wait_for_headings_ms)
            break
        except Exception:
            pass
    try:
        page.wait_for_load_state("networkidle", timeout=cfg.networkidle_timeout_ms)
    except Exception:
        pass
    if cfg.settle_delay_ms > 0:
        page.wait_for_timeout(cfg.settle_delay_ms)
    return resp


def _extract_links(page, current_url: str, base_host: str, cfg: CrawlConfig) -> set[str]:
    cur_key = url_key(current_url)
    links: set[str] = set()
    try:
        hrefs = page.evaluate(_EXTRACT_LINKS_JS)
    except Exception:
        return links
    for href in hrefs:
        try:
            abs_url = normalize_url(urljoin(current_url, href))
        except Exception:
            continue
        if url_key(abs_url) == cur_key:
            continue
        if should_skip(abs_url) or is_pagination(abs_url):
            continue
        if not is_same_domain(abs_url, base_host, cfg.include_subdomains):
            continue
        links.add(abs_url)
    return links


def crawl_site(
    url_base: str,
    crawl_config: CrawlConfig,
    audit_config: AuditConfig,
    on_progress=None,
    should_cancel=None,
) -> dict:
    crawl_start = time.perf_counter()
    base_url = normalize_url(url_base)
    base_host = urlparse(base_url).netloc
    should_cancel = should_cancel or (lambda: False)
    has_limit = crawl_config.max_pages > 0

    visited:    set[str]   = set()
    in_progress: set[str]  = set()
    known_keys: set[str]   = {url_key(base_url)}
    queue:      deque[str] = deque([base_url])
    reports:    list[dict] = []

    state_lock   = threading.Condition()
    reports_lock = threading.Lock()
    timing_lock  = threading.Lock()
    totals = {"load": 0.0, "headings": 0.0, "links": 0.0, "validation": 0.0, "page_total": 0.0}

    # Fase 0 — sitemap (pré-crawl, browser dedicado)
    if crawl_config.max_pages != 1:
        try:
            with _browser() as b:
                p = _new_page(b, crawl_config)
                try:
                    for su in fetch_sitemap_urls(base_url, p, crawl_config):
                        k, n = url_key(su), normalize_url(su)
                        if k not in known_keys and not is_pagination(n) and not should_skip(n):
                            queue.append(n)
                            known_keys.add(k)
                finally:
                    p.context.close()
        except Exception:
            pass

    def take_next() -> str | None:
        with state_lock:
            while True:
                if should_cancel():
                    return None
                if has_limit and (len(visited) + len(in_progress)) >= crawl_config.max_pages:
                    return None
                if queue:
                    u = queue.popleft()
                    k = url_key(u)
                    if k in visited or k in in_progress:
                        continue
                    in_progress.add(k)
                    return u
                if not in_progress:
                    return None
                state_lock.wait(timeout=0.3)

    def release(key: str, visited_flag: bool = True) -> None:
        with state_lock:
            in_progress.discard(key)
            if visited_flag:
                visited.add(key)
            state_lock.notify_all()

    def enqueue(links: set[str]) -> None:
        with state_lock:
            for link in links:
                k = url_key(link)
                if k not in visited and k not in in_progress and k not in known_keys:
                    queue.append(link)
                    known_keys.add(k)
            state_lock.notify_all()

    def worker() -> None:
        with _browser() as browser:
            page = _new_page(browser, crawl_config)
            try:
                while True:
                    url = take_next()
                    if url is None:
                        break
                    ukey = url_key(url)
                    if should_cancel():
                        release(ukey, False)
                        break

                    t0_page = time.perf_counter()
                    try:
                        t0 = time.perf_counter()
                        resp = _load_page(page, url, crawl_config)
                        t_load = time.perf_counter() - t0

                        if resp and resp.status >= 400:
                            raise RuntimeError(f"HTTP {resp.status}")

                        final_url = normalize_url(page.url)
                        fkey = url_key(final_url)

                        if fkey != ukey:
                            with state_lock:
                                if fkey in visited or fkey in in_progress:
                                    in_progress.discard(ukey)
                                    state_lock.notify_all()
                                    continue
                                in_progress.discard(ukey)
                                in_progress.add(fkey)
                                known_keys.add(fkey)

                        t0 = time.perf_counter()
                        page_data = extract_headings(page)
                        page_data["url"] = final_url
                        t_headings = time.perf_counter() - t0

                        t0 = time.perf_counter()
                        report = validate_headings(page_data, audit_config)
                        t_validation = time.perf_counter() - t0

                        t0 = time.perf_counter()
                        if crawl_config.max_pages != 1:
                            enqueue(_extract_links(page, page.url, base_host, crawl_config))
                        t_links = time.perf_counter() - t0

                        t_page = time.perf_counter() - t0_page
                        report["timings"] = {
                            "load_seconds":       round(t_load, 4),
                            "headings_seconds":   round(t_headings, 4),
                            "validation_seconds": round(t_validation, 4),
                            "links_seconds":      round(t_links, 4),
                            "page_total_seconds": round(t_page, 4),
                        }

                        with reports_lock:
                            reports.append(report)
                            pages_with_issues = sum(1 for r in reports if not r["valid"])
                        with timing_lock:
                            totals["load"]       += t_load
                            totals["headings"]   += t_headings
                            totals["validation"] += t_validation
                            totals["links"]      += t_links
                            totals["page_total"] += t_page

                        release(fkey)
                        if fkey != ukey:
                            release(ukey, False)

                        if on_progress:
                            with reports_lock:
                                cnt = len(visited)
                            on_progress(cnt, crawl_config.max_pages, report["url"],
                                        report["summary"]["issue_count"], report["timings"],
                                        pages_with_issues, report=report)

                    except Exception as exc:
                        err = {
                            "url": url, "title": "", "headings": [],
                            "considered_headings": [],
                            "issues": [{"rule": "page_error", "message": str(exc)}],
                            "valid": False,
                            "summary": {"total_headings": 0, "considered_headings": 0, "h1_count": 0, "issue_count": 1},
                            "timings": {},
                        }
                        with reports_lock:
                            reports.append(err)
                            pages_with_issues = sum(1 for r in reports if not r["valid"])
                        release(ukey)
                        if on_progress:
                            with reports_lock:
                                cnt = len(visited)
                            on_progress(cnt, crawl_config.max_pages, url, 1, {},
                                        pages_with_issues, report=err)
            finally:
                page.context.close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(crawl_config.crawler_workers)]
    for t in threads: t.start()
    for t in threads: t.join()

    elapsed = time.perf_counter() - crawl_start
    n = len(reports)
    reports.sort(key=lambda r: r["url"])
    return {
        "base_url":          base_url,
        "pages_crawled":     n,
        "pages_with_issues": sum(1 for r in reports if not r["valid"]),
        "timings": {
            "total_elapsed_seconds":       round(elapsed, 4),
            "average_page_seconds":        round(totals["page_total"] / n if n else 0, 4),
            "pages_per_second":            round(n / elapsed if elapsed else 0, 4),
            "load_seconds":                round(totals["load"], 4),
            "headings_seconds":            round(totals["headings"], 4),
            "validation_seconds":          round(totals["validation"], 4),
            "links_seconds":               round(totals["links"], 4),
            "measured_page_total_seconds": round(totals["page_total"], 4),
        },
        "reports": reports,
    }