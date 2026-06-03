from __future__ import annotations

import json
import re
from html import escape
from datetime import datetime, timezone
from pathlib import Path


def on_progress(current, total, url, issue_count, timings, *args, **kwargs):
    page_total = timings.get("page_total_seconds", 0.0) if isinstance(timings, dict) else 0.0
    load = timings.get("load_seconds", 0.0) if isinstance(timings, dict) else 0.0
    headings = timings.get("headings_seconds", 0.0) if isinstance(timings, dict) else 0.0
    links = timings.get("links_seconds", 0.0) if isinstance(timings, dict) else 0.0
    print(
        f"[{current}/{total}] {url} | problemas: {issue_count} | "
        f"tempo={page_total:.3f}s "
        f"(load={load:.3f}s, headings={headings:.3f}s, links={links:.3f}s)"
    )
    
    # Se houver problemas, imprime-os imediatamente
    report = kwargs.get("report")
    if report and not report.get("valid") and report.get("issues"):
        for issue in report["issues"]:
            print(f"  -> [{issue['rule']}] {issue['message']}")


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

    elapsed = timings.get("total_elapsed_seconds", "-")
    avg     = timings.get("average_page_seconds", "-")
    pps     = timings.get("pages_per_second", "-")

    return f"""<!DOCTYPE html>
<html lang="pt">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relatorio de headings</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    :root{{
      --bg:#f4f5f7;--surface:#fff;--surface-2:#f9fafb;--border:#e2e5ea;--border-2:#cdd2da;
      --text:#1c2333;--muted:#6b7585;--accent:#2563eb;--accent-soft:#eff4ff;
      --green:#15803d;--green-bg:#f0fdf4;--red:#dc2626;--red-bg:#fef2f2;
      --yellow:#b45309;--yellow-bg:#fffbeb;--mono:Consolas,Monaco,monospace;
    }}
    body{{background:var(--bg);color:var(--text);font-family:Arial,Helvetica,sans-serif;line-height:1.5;font-size:14px}}
    main{{max-width:1100px;margin:0 auto;padding:32px 24px 56px}}
    .report-header{{margin-bottom:24px}}
    .report-header h1{{font-family:var(--mono);font-size:18px;font-weight:700;margin-bottom:4px}}
    .report-header .meta{{font-size:12px;color:var(--muted);margin-bottom:2px}}
    .report-header .base-url{{font-family:var(--mono);font-size:12px;word-break:break-all}}
    .report-header .base-url a{{color:#1d4ed8;text-decoration:none}}
    .summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:14px}}
    .s-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px;text-align:center;box-shadow:0 1px 3px rgba(28,35,51,.05)}}
    .s-num{{font-family:var(--mono);font-size:26px;font-weight:700;line-height:1.1;margin-bottom:2px}}
    .s-num.red{{color:var(--red)}}
    .s-num.green{{color:var(--green)}}
    .s-label{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}}
    .timings-bar{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:11px 16px;margin-bottom:14px;font-size:12px;color:var(--muted);font-family:var(--mono);box-shadow:0 1px 3px rgba(28,35,51,.05)}}
    .pages-header{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}}
    .pages-header-title{{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);font-weight:700}}
    .filter-row{{display:flex;gap:6px}}
    .filter-btn{{appearance:none;border:none;cursor:pointer;font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;padding:6px 11px;border-radius:6px;background:transparent;color:var(--muted);border:1px solid transparent;font-weight:700}}
    .filter-btn.active{{background:var(--accent-soft);color:var(--accent);border-color:rgba(37,99,235,.15)}}
    .page-item{{background:var(--surface);border:1px solid var(--border);border-radius:10px;margin-bottom:8px;overflow:hidden;box-shadow:0 1px 3px rgba(28,35,51,.05)}}
    .page-item.has-issues{{border-color:rgba(220,38,38,.3)}}
    .page-item.hidden{{display:none}}
    .page-item-head{{display:flex;gap:10px;align-items:center;padding:10px 14px;background:var(--surface-2);cursor:pointer;user-select:none}}
    .page-chevron{{font-size:10px;color:var(--muted);transition:transform .2s;flex-shrink:0}}
    .page-item.open .page-chevron{{transform:rotate(90deg)}}
    .page-url{{flex:1;min-width:0;font-family:var(--mono);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text)}}
    .copy-url-btn{{appearance:none;border:none;background:transparent;color:var(--muted);cursor:pointer;font-size:13px;padding:2px 5px;line-height:1;border-radius:4px;flex-shrink:0}}
    .copy-url-btn:hover{{color:var(--accent);background:var(--accent-soft)}}
    .page-badge{{display:inline-flex;align-items:center;gap:4px;font-family:var(--mono);font-size:10px;font-weight:700;padding:3px 9px;border-radius:20px;white-space:nowrap;flex-shrink:0}}
    .badge-ok{{background:var(--green-bg);color:var(--green)}}
    .badge-err{{background:var(--red-bg);color:var(--red)}}
    .page-content{{display:none;padding:14px;background:var(--surface)}}
    .page-item.open .page-content{{display:block}}
    .page-title{{font-size:12px;color:var(--muted);margin-bottom:10px}}
    .section-label{{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);font-weight:700;margin:12px 0 6px}}
    .issue{{display:flex;gap:8px;align-items:flex-start;background:var(--red-bg);border-radius:6px;padding:7px 9px;margin:4px 0;color:var(--red);font-size:12px}}
    .issue-label,.h-issue-badge{{font-family:var(--mono);font-size:9px;font-weight:700;padding:2px 7px;border-radius:20px;background:var(--red-bg);color:var(--red);border:1px solid rgba(220,38,38,.2);white-space:nowrap}}
    .warn{{background:var(--yellow-bg);color:var(--yellow);border-color:rgba(180,83,9,.2)}}
    .heading-list{{display:grid;gap:3px}}
    .h-row{{display:flex;gap:8px;align-items:flex-start;padding:5px 8px;border-radius:6px}}
    .h-row:hover{{background:var(--surface-2)}}
    .h-row.h-error{{background:var(--red-bg)}}
    .h-tag{{font-family:var(--mono);font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;min-width:26px;text-align:center;background:var(--surface-2);border:1px solid var(--border-2);color:var(--muted);flex-shrink:0}}
    .h-tag.h1{{background:#eff4ff;color:var(--accent);border-color:rgba(37,99,235,.2)}}
    .h-tag.h2{{background:#f0fdf4;color:var(--green);border-color:rgba(21,128,61,.2)}}
    .h-tag.h3{{background:#fffbeb;color:var(--yellow);border-color:rgba(180,83,9,.2)}}
    .h-text{{flex:1;font-size:12px;word-break:break-word}}
    .h-text.empty-text{{color:var(--muted);font-style:italic}}
    .h-issues{{display:flex;flex-direction:column;gap:3px;flex-shrink:0}}
    .source{{font-size:11px;color:var(--muted);margin-left:6px}}
    .empty{{color:var(--muted);font-style:italic;padding:6px 0;font-size:12px}}
    #emptyFilterMsg{{display:none;color:var(--muted);font-style:italic;padding:8px 0;font-size:12px}}
    @media(max-width:700px){{
      .summary{{grid-template-columns:repeat(2,minmax(0,1fr))}}
      .pages-header{{flex-direction:column;align-items:flex-start}}
    }}
  </style>
</head>
<body>
  <main>
    <div class="report-header">
      <h1>Relatorio de auditoria de headings</h1>
      <div class="meta">Gerado em {escape(generated_at)}</div>
      <div class="base-url"><a href="{escape(result.get("base_url") or "")}">{escape(result.get("base_url") or "")}</a></div>
    </div>

    <div class="summary">
      <div class="s-card"><div class="s-num">{pages_crawled}</div><div class="s-label">Paginas</div></div>
      <div class="s-card"><div class="s-num red">{pages_with_issues}</div><div class="s-label">Com erros</div></div>
      <div class="s-card"><div class="s-num green">{pages_ok}</div><div class="s-label">Sem erros</div></div>
      <div class="s-card"><div class="s-num">{total_issues}</div><div class="s-label">Problemas totais</div></div>
    </div>

    <div class="timings-bar">
      Tempo total: {escape(str(elapsed))}s &nbsp;-&nbsp; Media por pagina: {escape(str(avg))}s &nbsp;-&nbsp; Paginas/s: {escape(str(pps))}
    </div>

    <div class="pages-header">
      <span class="pages-header-title">Paginas auditadas</span>
      <div class="filter-row">
        <button class="filter-btn active" type="button" data-filter="all">Todas</button>
        <button class="filter-btn" type="button" data-filter="errors">So erros</button>
      </div>
    </div>
    <div id="emptyFilterMsg">Nenhuma pagina com problemas.</div>
    {page_sections}
  </main>
  <script>
    // Filter buttons
    const filterBtns = document.querySelectorAll("[data-filter]");
    const pageItems  = Array.from(document.querySelectorAll(".page-item"));
    const emptyMsg   = document.getElementById("emptyFilterMsg");

    function applyFilter(f) {{
      let visible = 0;
      pageItems.forEach(el => {{
        const show = f === "all" || el.dataset.valid === "false";
        el.classList.toggle("hidden", !show);
        if (show) visible++;
      }});
      emptyMsg.style.display = (f === "errors" && visible === 0) ? "block" : "none";
      filterBtns.forEach(b => b.classList.toggle("active", b.dataset.filter === f));
    }}

    filterBtns.forEach(b => b.addEventListener("click", () => applyFilter(b.dataset.filter)));

    // Page expand/collapse
    document.querySelectorAll(".page-item-head").forEach(head => {{
      head.addEventListener("click", e => {{
        if (e.target.closest(".copy-url-btn")) return;
        head.closest(".page-item").classList.toggle("open");
      }});
    }});

    // Copy URL buttons
    document.querySelectorAll(".copy-url-btn").forEach(btn => {{
      btn.addEventListener("click", async e => {{
        e.stopPropagation();
        try {{
          await navigator.clipboard.writeText(btn.dataset.url);
          const orig = btn.textContent;
          btn.textContent = "✓";
          setTimeout(() => btn.textContent = orig, 1200);
        }} catch (_) {{}}
      }});
    }});
  </script>
</body>
</html>
"""


def _render_page(report: dict) -> str:
    issues = report.get("issues") or []
    headings = report.get("considered_headings") or []
    valid = report.get("valid")
    badge_class = "badge-ok" if valid else "badge-err"
    badge_text = "✓ OK" if valid else f"⚠ {len(issues)} {'erro' if len(issues) == 1 else 'erros'}"
    issues_html = "\n".join(_render_issue(issue) for issue in issues)
    if not issues_html:
        issues_html = '<p class="empty">Nenhum problema encontrado nesta pagina.</p>'
    issues_by_heading = _issues_by_heading(issues)
    headings_html = "\n".join(_render_heading(heading, issues_by_heading) for heading in headings)
    if not headings_html:
        headings_html = '<p class="empty">Nenhum heading considerado.</p>'
    page_class = "page-item has-issues" if not valid else "page-item"
    is_valid = "true" if valid else "false"
    # Pages with issues start open
    open_class = " open" if not valid else ""
    url = report.get("url") or ""
    title = report.get("title") or "-"

    return f"""
      <div class="{page_class}{open_class}" data-valid="{is_valid}">
        <div class="page-item-head">
          <span class="page-chevron">▶</span>
          <span class="page-url" title="{escape(url)}">{escape(url)}</span>
          <button class="copy-url-btn" data-url="{escape(url)}" title="Copiar link" type="button">⎘</button>
          <span class="page-badge {badge_class}">{escape(badge_text)}</span>
        </div>
        <div class="page-content">
          <div class="page-title"><strong>Titulo:</strong> {escape(title)}</div>
          <div class="section-label">Problemas</div>
          {issues_html}
          <div class="section-label">Headings considerados</div>
          <div class="heading-list">{headings_html}</div>
        </div>
      </div>
    """


def _render_issue(issue: dict) -> str:
    rule = _issue_label(issue)
    issue_class = "issue-label warn" if issue.get("rule") in {"hierarchy_skip", "starts_too_deep"} else "issue-label"
    message = issue.get("message") or ""
    return f'<div class="issue"><span class="{issue_class}">{escape(rule)}</span><span>{escape(message)}</span></div>'


def _render_heading(heading: dict, issues_by_heading: dict[int, list[dict]]) -> str:
    level = int(heading.get("level") or 1)
    indent = max(0, level - 1) * 18
    has_image = heading.get("hasImage", False)
    text = heading.get("text") or ""
    # Heading com imagem e sem texto: mostrar label descritivo em vez de vazio
    if has_image and not text:
        text = f'{heading.get("tag")} com imagem'
    elif not text:
        text = "(heading vazio)"
    source = ""
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
        "missing_h1":      "H1 em falta",
        "multiple_h1":     "H1 múltiplo",
        "empty_h1":        "H1 vazio",
        "empty_heading":   "Vazio",
        "hierarchy_skip":  "Salto",
        "starts_too_deep": "Início profundo",
        "page_error":      "Erro de página",
    }
    return labels.get(issue.get("rule"), issue.get("rule") or "?")