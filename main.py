from __future__ import annotations

import sys
from pathlib import Path

from config import DEFAULT_MAX_PAGES, DEFAULT_TIMEOUT_MS, START_URL, WRITE_JSON_REPORT, AuditConfig, CrawlConfig
from crawler import crawl_site
from reporting import on_progress, print_summary, write_json_report


def choose_mode() -> str:
    print()
    print("Escolhe o modo de crawl:")
    print("  1) Pagina unica")
    print("  2) Site inteiro")
    while True:
        try:
            selected = input("Opcao [1/2]: ").strip()
        except EOFError:
            return "site"
        if selected == "1":
            return "single"
        if selected == "2":
            return "site"
        print("Opcao invalida. Escolhe 1 ou 2.")


def parse_args():
    import argparse
    parser = argparse.ArgumentParser(description="Auditor de headings HTML com Playwright.")
    parser.add_argument("url", nargs="?", default=START_URL, help="URL inicial do dominio a auditar.")
    parser.add_argument("--mode", choices=("site", "single"), help="Modo de crawl.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--workers", type=int, default=CrawlConfig().crawler_workers)
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--include-subdomains", action="store_true")
    parser.add_argument("--all-headings", action="store_true")
    parser.add_argument("--report-dir", default="relatorios")
    parser.add_argument("--no-report", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.url:
        print("Erro: URL nao especificada.")
        print("  Exemplo: python main.py https://www.exemplo.pt")
        sys.exit(1)

    mode = args.mode or choose_mode()

    crawl_config = CrawlConfig(
        max_pages=1 if mode == "single" else args.max_pages,
        crawler_workers=max(1, args.workers),
        timeout_ms=max(1_000, args.timeout_ms),
        include_subdomains=args.include_subdomains,
    )
    audit_config = AuditConfig(
        validate_visible_only=not args.all_headings,
        report_dir=args.report_dir,
    )

    print(f"Modo selecionado: {'pagina unica' if mode == 'single' else 'site inteiro'}")
    result = crawl_site(args.url, crawl_config, audit_config, on_progress=on_progress)
    print_summary(result)

    if WRITE_JSON_REPORT and not args.no_report:
        output_path = write_json_report(result, audit_config.report_dir)
        print(f"Relatorio: {Path(output_path).resolve()}")


if __name__ == "__main__":
    main()