from __future__ import annotations
from config import AuditConfig

def _has_content(h: dict) -> bool:
    return bool((h.get("text") or "").strip()) or bool(h.get("hasImage"))

def _is_empty(h: dict) -> bool:
    return not _has_content(h)

def validate_headings(page_data: dict, audit_config: AuditConfig) -> dict:
    raw = page_data.get("headings", [])

    headings = []
    for i, h in enumerate(raw, start=1):
        text = " ".join((h.get("text") or "").split())
        headings.append({
            "index":     i,
            "tag":       h["tag"],
            "level":     h["level"],
            "text":      text,
            "hasImage":  h.get("hasImage", False),
            "imageType": h.get("imageType"),
            "imageAlt":  h.get("imageAlt", ""),
            "visible":   h.get("visible", False),
            "is_empty":  _is_empty({"text": text, "hasImage": h.get("hasImage", False)}),
        })

    considered = [h for h in headings if h["visible"] or not audit_config.validate_visible_only]
    issues = []

    # Regra — h1 único
    h1s = [h for h in considered if h["level"] == 1]
    h1_count = len(h1s)

    if h1_count == 0:
        issues.append({"rule": "missing_h1", "message": "A página não tem h1. Cada página deve ter exactamente um h1 que descreva o seu conteúdo."})
    elif h1_count > 1:
        labels = ", ".join(f'"{h["text"]}"' if h["text"] else "(sem texto)" for h in h1s)
        issues.append({"rule": "multiple_h1", "message": f"A página tem {h1_count} elementos h1 — deve existir apenas um. Encontrados: {labels}."})

    for h in h1s:
        if _is_empty(h):
            issues.append({"rule": "empty_h1", "message": "h1 vazio — não tem texto nem imagem. O h1 deve identificar o conteúdo da página.", "heading_index": h["index"]})

    # Regra — hierarquia
    prev_level: int | None = None
    for h in considered:
        level, tag = h["level"], h["tag"]

        if _is_empty(h) and level != 1:
            issues.append({"rule": "empty_heading", "message": f"{tag} vazio — o heading não tem texto nem imagem.", "heading_index": h["index"]})

        if prev_level is None:
            if level > 1 and h1_count > 0:
                missing = " → ".join(f"h{n}" for n in range(1, level))
                issues.append({"rule": "starts_too_deep", "message": f"A estrutura de headings começa em {tag} sem {missing} antes.", "heading_index": h["index"]})
        elif level - prev_level > 1:
            issues.append({"rule": "hierarchy_skip", "message": f"Salto de h{prev_level} para h{level} — falta o nível h{prev_level + 1} entre eles.", "heading_index": h["index"]})

        prev_level = level

    return {
        "url":                 page_data.get("url", ""),
        "title":               page_data.get("title", ""),
        "headings":            headings if not audit_config.ignore_hidden_in_report else considered,
        "considered_headings": considered,
        "issues":              issues,
        "valid":               not issues,
        "summary": {
            "total_headings":      len(headings),
            "considered_headings": len(considered),
            "h1_count":            h1_count,
            "issue_count":         len(issues),
        },
    }