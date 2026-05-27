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
from pagination_crawler import crawl_pagination
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
    parser.add_argument(
        "--mode",
        choices=("site", "single", "pagination"),
        help="Modo de crawl: 'site' para dominio inteiro, 'single' para apenas uma pagina, ou 'pagination' para paginas com paginação.",
    )
    return parser


def choose_mode_from_menu() -> str:
    print()
    print("Escolhe o modo de crawl:")
    print("  1) Pagina unica")
    print("  2) Site inteiro")
    print("  3) Paginação")
    while True:
        try:
            selected = input("Opcao [1/3]: ").strip()
        except EOFError:
            return "site"
        if selected == "1":
            return "single"
        if selected == "2":
            return "site"
        if selected == "3":
            return "pagination"
        print("Opcao invalida. Escolhe 1, 2 ou 3.")


def print_terminal_summary(result: dict) -> None:
    timings = result.get("timings", {})

    print()
    print(f"Paginas analisadas: {result['pages_crawled']}")
    print(f"Paginas com problemas: {result['pages_with_issues']}")
    if timings and timings.get("total_elapsed_seconds") is not None:
        print(f"Tempo total: {timings['total_elapsed_seconds']:.2f}s")

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
        if report.get("timings"):
            print(
                "  "
                f"tempo={report['timings']['page_total_seconds']:.3f}s "
                f"load={report['timings']['load_seconds']:.3f}s "
                f"headings={report['timings']['headings_seconds']:.3f}s "
                f"links={report['timings']['links_seconds']:.3f}s"
            )


def main() -> None:
    args = build_parser().parse_args()
    mode = args.mode or choose_mode_from_menu()
    max_pages = 1 if mode == "single" else args.max_pages

    crawl_config = CrawlConfig(
        max_pages=max_pages,
        crawler_workers=max(1, args.workers),
        timeout_ms=max(1_000, args.timeout_ms),
        include_subdomains=args.include_subdomains,
    )
    audit_config = AuditConfig(
        validate_visible_only=not args.all_headings,
        report_dir=args.report_dir,
    )

    def on_progress(current, total, url, issue_count, timings):
        page_total_seconds = timings.get("page_total_seconds", 0.0) if isinstance(timings, dict) else 0.0
        load_seconds = timings.get("load_seconds", 0.0) if isinstance(timings, dict) else 0.0
        headings_seconds = timings.get("headings_seconds", 0.0) if isinstance(timings, dict) else 0.0
        links_seconds = timings.get("links_seconds", 0.0) if isinstance(timings, dict) else 0.0
        print(
            f"[{current}/{total}] {url} | problemas: {issue_count} | "
            f"tempo={page_total_seconds:.3f}s "
            f"(load={load_seconds:.3f}s, "
            f"headings={headings_seconds:.3f}s, "
            f"links={links_seconds:.3f}s)"
        )

    def on_pagination_discovery(step, url):
        if step == 0:
            print(f"A iniciar paginação {url}")
            return
        print(f"Pagina {step} descoberta: {url}")

    def on_pagination_phase_change(phase, count):
        if phase == "discovery_done":
            print(f"Paginação descoberta: {count} paginas")
        elif phase == "validation_start":
            print("A iniciar validação de headings...")

    if mode == "pagination":
        try:
            pagination_start_url = input(f"URL inicial da paginação: ").strip() or args.url
        except EOFError:
            pagination_start_url = args.url
        print("Modo selecionado: paginação")
        print("A descobrir paginas da paginação...")
        result = crawl_pagination(
            pagination_start_url,
            crawl_config,
            audit_config,
            on_progress=on_progress,
            on_discovery_progress=on_pagination_discovery,
            on_phase_change=on_pagination_phase_change,
        )
    else:
        print(f"Modo selecionado: {'pagina unica' if mode == 'single' else 'site inteiro'}")
        result = crawl_site(args.url, crawl_config, audit_config, on_progress=on_progress)
    print_terminal_summary(result)

    should_write_report = WRITE_JSON_REPORT and not args.no_report
    if should_write_report:
        output_path = write_json_report(result, audit_config.report_dir)
        print(f"Relatorio: {Path(output_path).resolve()}")


if __name__ == "__main__":
    main()