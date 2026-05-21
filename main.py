from __future__ import annotations

import argparse
from pathlib import Path

from config import (
    DEFAULT_MAX_PAGES,
    DEFAULT_TIMEOUT_MS,
    START_URL,
    WRITE_JSON_REPORT,
    AuditConfig,
    CrawlConfig,
)
from crawler import crawl_site
from reporting import write_json_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auditor de headings HTML com Playwright.")
    parser.add_argument("url", nargs="?", default=START_URL, help="URL inicial do dominio a auditar.")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help="Numero maximo de paginas a visitar.",
    )
    parser.add_argument("--workers", type=int, default=CrawlConfig().crawler_workers, help="Numero de workers paralelos.")
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help="Timeout por pagina em milissegundos.",
    )
    parser.add_argument(
        "--include-subdomains",
        action="store_true",
        help="Inclui subdominios do dominio inicial no crawl.",
    )
    parser.add_argument(
        "--all-headings",
        action="store_true",
        help="Valida tambem headings escondidos, nao apenas os visiveis.",
    )
    parser.add_argument(
        "--report-dir",
        default="relatorios",
        help="Diretorio onde o relatorio JSON sera gravado.",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Nao grava ficheiro JSON; mostra apenas o resultado no terminal.",
    )
    return parser


def print_terminal_summary(result: dict) -> None:
    print()
    print(f"Paginas analisadas: {result['pages_crawled']}")
    print(f"Paginas com problemas: {result['pages_with_issues']}")

    invalid_reports = [report for report in result["reports"] if not report["valid"]]
    if not invalid_reports:
        print("Nenhum problema de headings encontrado.")
        return

    print()
    print("Paginas com problemas:")
    for report in invalid_reports:
        print(f"- {report['url']}")
        for issue in report["issues"]:
            print(f"  [{issue['rule']}] {issue['message']}")


def main() -> None:
    args = build_parser().parse_args()

    crawl_config = CrawlConfig(
        max_pages=args.max_pages,
        crawler_workers=max(1, args.workers),
        timeout_ms=max(1_000, args.timeout_ms),
        include_subdomains=args.include_subdomains,
    )
    audit_config = AuditConfig(
        validate_visible_only=not args.all_headings,
        report_dir=args.report_dir,
    )

    def on_progress(current, total, url, issue_count):
        print(f"[{current}/{total}] {url} | problemas: {issue_count}")

    result = crawl_site(args.url, crawl_config, audit_config, on_progress=on_progress)
    print_terminal_summary(result)

    should_write_report = WRITE_JSON_REPORT and not args.no_report
    if should_write_report:
        output_path = write_json_report(result, audit_config.report_dir)
        print(f"Relatorio: {Path(output_path).resolve()}")


if __name__ == "__main__":
    main()