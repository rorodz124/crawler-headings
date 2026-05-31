from __future__ import annotations

import json
import re
from html import escape
from datetime import datetime, timezone
from pathlib import Path


def on_progress(current, total, url, issue_count, timings):
    page_total = timings.get("page_total_seconds", 0.0) if isinstance(timings, dict) else 0.0
    load = timings.get("load_seconds", 0.0) if isinstance(timings, dict) else 0.0
    headings = timings.get("headings_seconds", 0.0) if isinstance(timings, dict) else 0.0
    links = timings.get("links_seconds", 0.0) if isinstance(timings, dict) else 0.0
    print(
        f"[{current}/{total}] {url} | problemas: {issue_count} | "
        f"tempo={page_total:.3f}s "
        f"(load={load:.3f}s, headings={headings:.3f}s, links={links:.3f}s)"
    )


def print_summary(result: dict) -> None:
    timings = result.get("timings", {})
    print()
    print(f"Paginas analisadas: {result['pages_crawled']}")
    print(f"Paginas com problemas: {result['pages_with_issues']}")
    if timings.get("total_elapsed_seconds") is not None:
        print(f"Tempo total: {timings['total_elapsed_seconds']:.2f}s")

    invalid = [r for r in result["reports"] if not r["valid"]]
    if not invalid:
        print("Nenhum problema de headings encontrado.")
        return

    print()
    print("Paginas com problemas:")
    for report in invalid:
        print(f"- {report['url']}")
        for issue in report["issues"]:
            print(f"  [{issue['rule']}] {issue['message']}")
        if report.get("timings"):
            t = report["timings"]
            print(f"  tempo={t['page_total_seconds']:.3f}s  load={t['load_seconds']:.3f}s  headings={t['headings_seconds']:.3f}s  links={t['links_seconds']:.3f}s")


def make_report_basename(result: dict) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _report_basename(result, timestamp=timestamp)


def write_json_report(result: dict, report_dir: str, basename: str | None = None) -> Path:
    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)
    output_path = destination / f"{basename or make_report_basename(result)}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def write_html_report(result: dict, report_dir: str, basename: str | None = None) -> Path:
    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)
    output_path = destination / f"{basename or make_report_basename(result)}.html"
    output_path.write_text(_render_html_report(result), encoding="utf-8")
    return output_path


def _report_basename(result: dict, timestamp: str) -> str:
    base_url = result.get("base_url") or "auditoria"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", base_url).strip("_").lower()
    slug = slug[:70] or "auditoria"
    return f"relatorio_headings_{slug}_{timestamp}"


def _render_html_report(result: dict) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    reports = result.get("reports") or []
    pages_crawled = result.get("pages_crawled") or len(reports)
    pages_with_issues = result.get("pages_with_issues") or 0
    pages_ok = max(0, pages_crawled - pages_with_issues)
    total_issues = sum(len(report.get("issues") or []) for report in reports)
    timings = result.get("timings") or {}

    page_sections = "\n".join(_render_page(report) for report in reports)
    if not page_sections:
        page_sections = '<p class="empty">Nenhuma pagina foi analisada.</p>'

    return f"""<!DOCTYPE html>
<html lang="pt">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relatorio de headings</title>
  <style>
    *{{box-sizing:border-box}}
    :root{{
      --bg:#f4f5f7;--surface:#fff;--surface-2:#f9fafb;--border:#e2e5ea;--border-2:#cdd2da;
      --text:#1c2333;--muted:#6b7585;--accent:#2563eb;--accent-soft:#eff4ff;
      --green:#15803d;--green-bg:#f0fdf4;--red:#dc2626;--red-bg:#fef2f2;
      --yellow:#b45309;--yellow-bg:#fffbeb;--mono:Consolas,Monaco,monospace;
    }}
    body{{margin:0;background:var(--bg);color:var(--text);font-family:Arial,Helvetica,sans-serif;line-height:1.5;font-size:14px}}
    main{{max-width:1220px;margin:0 auto;padding:34px 24px 56px}}
    header{{margin-bottom:28px}}
    h1{{font-family:var(--mono);font-size:32px;line-height:1.15;margin:0 0 8px;font-weight:700}}
    h2{{font-size:16px;margin:0 0 14px}}
    h3{{font-size:13px;margin:14px 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}}
    a{{color:#1d4ed8;word-break:break-all}}
    .muted{{color:var(--muted)}}
    .url{{font-family:var(--mono);font-size:13px}}
    .panel{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:18px;margin-bottom:16px;box-shadow:0 1px 4px rgba(28,35,51,.06)}}
    .summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}}
    .metric{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}}
    .metric strong{{display:block;font-family:var(--mono);font-size:24px}}
    .metric span{{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}}
    .pages-header{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}}
    .pages-title{{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted)}}
    .filter-row{{display:flex;gap:6px}}
    .filter-btn{{appearance:none;border:none;cursor:pointer;font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;padding:6px 11px;border-radius:6px;background:transparent;color:var(--muted);border:1px solid transparent;font-weight:700}}
    .filter-btn.active{{background:var(--accent-soft);color:var(--accent);border-color:rgba(37,99,235,.15)}}
    .page{{background:var(--surface);border:1px solid var(--border);border-radius:10px;margin-bottom:8px;overflow:hidden}}
    .page.has-issues{{border-color:rgba(220,38,38,.25)}}
    .page.hidden{{display:none}}
    summary{{cursor:pointer;display:flex;gap:10px;align-items:center;padding:10px 14px;background:var(--surface-2);user-select:none}}
    summary::-webkit-details-marker{{display:none}}
    .chevron{{font-size:10px;color:var(--muted);transition:transform .2s;flex-shrink:0}}
    details[open] .chevron{{transform:rotate(90deg)}}
    .summary-url{{flex:1;min-width:0;font-family:var(--mono);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
    .copy-url-btn{{appearance:none;border:none;background:transparent;color:var(--muted);cursor:pointer;font-size:13px;padding:1px 4px;line-height:1;border-radius:4px;flex-shrink:0}}
    .copy-url-btn:hover{{color:var(--accent);background:var(--accent-soft)}}
    .page-badge{{display:inline-flex;align-items:center;gap:4px;font-family:var(--mono);font-size:10px;font-weight:700;padding:3px 8px;border-radius:20px;white-space:nowrap}}
    .badge-ok{{background:var(--green-bg);color:var(--green)}}
    .badge-err{{background:var(--red-bg);color:var(--red)}}
    .page-body{{padding:14px;background:var(--surface)}}
    .issue{{display:flex;gap:8px;align-items:flex-start;background:var(--red-bg);border-radius:6px;padding:7px 9px;margin:5px 0;color:var(--red);font-size:12px}}
    .issue-label,.h-issue-badge{{font-family:var(--mono);font-size:9px;font-weight:700;padding:2px 7px;border-radius:20px;background:var(--red-bg);color:var(--red);border:1px solid rgba(220,38,38,.2);white-space:nowrap}}
    .warn{{background:var(--yellow-bg);color:var(--yellow);border-color:rgba(180,83,9,.2)}}
    .heading-list{{display:grid;gap:3px}}
    .h-row{{display:flex;gap:8px;align-items:flex-start;padding:5px 8px;border-radius:6px}}
    .h-row:hover{{background:var(--surface-2)}}
    .h-row.h-error{{background:var(--red-bg)}}
    .h-tag{{font-family:var(--mono);font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;min-width:26px;text-align:center;background:var(--surface-2);border:1px solid var(--border-2);color:var(--muted)}}
    .h-tag.h1{{background:#eff4ff;color:var(--accent);border-color:rgba(37,99,235,.2)}}
    .h-tag.h2{{background:#f0fdf4;color:var(--green);border-color:rgba(21,128,61,.2)}}
    .h-tag.h3{{background:#fffbeb;color:var(--yellow);border-color:rgba(180,83,9,.2)}}
    .h-text{{flex:1;font-size:12px;word-break:break-word}}
    .h-text.empty-text{{color:var(--muted);font-style:italic}}
    .h-issues{{display:flex;flex-direction:column;gap:3px;flex-shrink:0}}
    .source{{font-size:11px;color:var(--muted);margin-left:6px}}
    .empty{{color:var(--muted);font-style:italic;padding:8px 0}}
    @media(max-width:760px){{main{{padding:20px 12px 44px}}.summary{{grid-template-columns:repeat(2,minmax(0,1fr))}}.pages-header{{align-items:flex-start;flex-direction:column}}summary{{align-items:flex-start;flex-direction:column}}.summary-url{{white-space:normal}}}}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Relatorio de auditoria de headings</h1>
      <div class="muted">Gerado em {escape(generated_at)}</div>
      <div class="url"><a href="{escape(result.get("base_url") or "")}">{escape(result.get("base_url") or "")}</a></div>
    </header>

    <section class="summary">
      <div class="metric"><strong>{pages_crawled}</strong><span>Paginas analisadas</span></div>
      <div class="metric"><strong>{pages_with_issues}</strong><span>Com problemas</span></div>
      <div class="metric"><strong>{pages_ok}</strong><span>Sem problemas</span></div>
      <div class="metric"><strong>{total_issues}</strong><span>Problemas totais</span></div>
    </section>

    <section class="panel">
      <h2>Resumo tecnico</h2>
      <p class="muted">Tempo total: {escape(str(timings.get("total_elapsed_seconds", "-")))}s · Media por pagina: {escape(str(timings.get("average_page_seconds", "-")))}s · Paginas/s: {escape(str(timings.get("pages_per_second", "-")))}</p>
    </section>

    <section>
      <div class="pages-header">
        <span class="pages-title">Paginas auditadas</span>
        <div class="filter-row" aria-label="Filtro de paginas">
          <button class="filter-btn active" type="button" data-filter="all">Todas</button>
          <button class="filter-btn" type="button" data-filter="errors">Só erros</button>
        </div>
      </div>
      <p class="empty" id="emptyFilterMessage" hidden>Nenhuma pagina com problemas.</p>
      {page_sections}
    </section>
  </main>
  <script>
    const buttons = document.querySelectorAll("[data-filter]");
    const pages = Array.from(document.querySelectorAll(".page"));
    const emptyMessage = document.getElementById("emptyFilterMessage");

    function applyFilter(filter) {{
      let visibleCount = 0;
      for (const page of pages) {{
        const show = filter === "all" || page.dataset.valid === "false";
        page.classList.toggle("hidden", !show);
        if (show) visibleCount += 1;
      }}
      emptyMessage.hidden = visibleCount !== 0;
      buttons.forEach((button) => button.classList.toggle("active", button.dataset.filter === filter));
    }}

    buttons.forEach((button) => button.addEventListener("click", () => applyFilter(button.dataset.filter)));
  </script>
</body>
</html>
"""


def _render_page(report: dict) -> str:
    issues = report.get("issues") or []
    headings = report.get("considered_headings") or []
    valid = report.get("valid")
    badge_class = "badge-ok" if valid else "badge-err"
    badge_text = "OK" if valid else f"{len(issues)} problema(s)"
    issues_html = "\n".join(_render_issue(issue) for issue in issues)
    if not issues_html:
        issues_html = '<p class="empty">Nenhum problema encontrado nesta pagina.</p>'
    issues_by_heading = _issues_by_heading(issues)
    headings_html = "\n".join(_render_heading(heading, issues_by_heading) for heading in headings)
    if not headings_html:
        headings_html = '<p class="empty">Nenhum heading considerado.</p>'
    page_class = "page has-issues" if not valid else "page"
    is_valid = "true" if valid else "false"

    return f"""
      <details class="{page_class}" data-valid="{is_valid}" {"open" if not valid else ""}>
        <summary>
          <span class="chevron">▶</span>
          <span class="summary-url">{escape(report.get("url") or "")}</span>
          <span class="page-badge {badge_class}">{escape(badge_text)}</span>
        </summary>
        <div class="page-body">
          <p><strong>Titulo:</strong> {escape(report.get("title") or "-")}</p>
          <h3>Problemas</h3>
          {issues_html}
          <h3>Headings considerados</h3>
          <div class="heading-list">{headings_html}</div>
        </div>
      </details>
    """


def _render_issue(issue: dict) -> str:
    rule = _issue_label(issue)
    issue_class = "issue-label warn" if issue.get("rule") in {"hierarchy_skip", "starts_too_deep"} else "issue-label"
    message = issue.get("message") or ""
    return f'<div class="issue"><span class="{issue_class}">{escape(rule)}</span><span>{escape(message)}</span></div>'


def _render_heading(heading: dict, issues_by_heading: dict[int, list[dict]]) -> str:
    level = int(heading.get("level") or 1)
    indent = max(0, level - 1) * 18
    text = heading.get("text") or heading.get("accessible_text") or ""
    if not text:
        text = "(heading vazio)"
    source = ""
    if heading.get("has_image") and heading.get("accessible_text") and not heading.get("raw_text"):
        source = '<span class="source">texto vindo da imagem</span>'
    heading_issues = issues_by_heading.get(heading.get("index"), [])
    issue_badges = "".join(_render_heading_issue_badge(issue) for issue in heading_issues)
    issues_html = f'<div class="h-issues">{issue_badges}</div>' if issue_badges else ""
    row_class = "h-row h-error" if heading_issues else "h-row"
    text_class = "h-text empty-text" if heading.get("is_empty") else "h-text"
    return (
        f'<div class="{row_class}">'
        f'<span style="width:{indent}px;flex-shrink:0"></span>'
        f'<span class="h-tag {escape(heading.get("tag") or "")}">{escape(heading.get("tag") or "")}</span>'
        f'<span class="{text_class}">{escape(text)}{source}</span>'
        f'{issues_html}'
        f'</div>'
    )


def _issues_by_heading(issues: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for issue in issues:
        heading_index = issue.get("heading_index")
        if heading_index is None:
            continue
        grouped.setdefault(heading_index, []).append(issue)
    return grouped


def _render_heading_issue_badge(issue: dict) -> str:
    klass = "h-issue-badge warn" if issue.get("rule") in {"hierarchy_skip", "starts_too_deep"} else "h-issue-badge"
    return f'<span class="{klass}">{escape(_issue_label(issue))}</span>'


def _issue_label(issue: dict) -> str:
    labels = {
        "single_h1": "H1",
        "empty_heading": "Vazio",
        "hierarchy_skip": "Salto",
        "starts_too_deep": "Inicio profundo",
        "page_error": "Erro de pagina",
    }
    return labels.get(issue.get("rule"), issue.get("rule") or "?")