from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def write_json_report(result: dict, report_dir: str) -> Path:
    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = destination / f"relatorio_headings_{timestamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path