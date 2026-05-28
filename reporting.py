from __future__ import annotations

import json
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


def write_json_report(result: dict, report_dir: str) -> Path:
    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = destination / f"relatorio_headings_{timestamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path