from __future__ import annotations

from config import AuditConfig


def _is_empty(heading: dict) -> bool:
    """Um heading está vazio se não tem texto E não tem nenhuma imagem."""
    has_text = bool((heading.get("text") or "").strip())
    has_image = heading.get("hasImage", False)
    return not has_text and not has_image


def validate_headings(page_data: dict, audit_config: AuditConfig) -> dict:
    raw = page_data.get("headings", [])

    # Atribuir índice (posição na página, começa em 1) e normalizar texto.
    headings = []
    for i, h in enumerate(raw, start=1):
        text = " ".join((h.get("text") or "").split())
        headings.append({
            "index":    i,
            "tag":      h["tag"],
            "level":    h["level"],
            "text":     text,
            "hasImage": h.get("hasImage", False),
            "visible":  h.get("visible", False),
            "is_empty": _is_empty({"text": text, "hasImage": h.get("hasImage", False)}),
        })

    # Apenas os headings visíveis entram nas regras (quando validate_visible_only=True).
    considered = [
        h for h in headings
        if h["visible"] or not audit_config.validate_visible_only
    ]

    issues = []

    # ------------------------------------------------------------------
    # Regra 2.1 — h1 único e não vazio
    # ------------------------------------------------------------------
    h1s = [h for h in considered if h["level"] == 1]
    h1_count = len(h1s)

    if h1_count == 0:
        issues.append({
            "rule":    "missing_h1",
            "message": "A página não contém nenhum h1.",
        })
    elif h1_count > 1:
        issues.append({
            "rule":    "multiple_h1",
            "message": f"A página contém {h1_count} headings h1; deve existir apenas um.",
        })

    for h in h1s:
        if h["is_empty"]:
            issues.append({
                "rule":          "empty_h1",
                "message":       f"O h1 na posição {h['index']} está vazio (sem texto nem imagem).",
                "heading_index": h["index"],
            })

    # ------------------------------------------------------------------
    # Regra 2.2 — hierarquia de títulos + headings vazios (todos os níveis)
    # ------------------------------------------------------------------
    prev_level = None
    for h in considered:
        level = h["level"]

        # Heading vazio (qualquer nível, incluindo h1 já tratado acima)
        if h["is_empty"] and level != 1:
            issues.append({
                "rule":          "empty_heading",
                "message":       f"{h['tag']} vazio na posição {h['index']}.",
                "heading_index": h["index"],
            })

        # Primeiro heading da página: não deve começar num nível demasiado profundo.
        # Só faz sentido quando existe um h1 — se não há h1, o erro já foi reportado
        # em missing_h1 e não duplicamos.
        if prev_level is None:
            if level > 1 and h1_count > 0:
                issues.append({
                    "rule":          "starts_too_deep",
                    "message":       (
                        f"A estrutura de headings começa em {h['tag']} "
                        "sem níveis anteriores."
                    ),
                    "heading_index": h["index"],
                })
        elif level > prev_level + 1:
            issues.append({
                "rule":          "hierarchy_skip",
                "message":       (
                    f"Salto inválido de h{prev_level} para h{level} "
                    f"na posição {h['index']}."
                ),
                "heading_index": h["index"],
            })

        prev_level = level

    return {
        "url":                  page_data.get("url", ""),
        "title":                page_data.get("title", ""),
        "headings":             headings if not audit_config.ignore_hidden_in_report else considered,
        "considered_headings":  considered,
        "issues":               issues,
        "valid":                not issues,
        "summary": {
            "total_headings":       len(headings),
            "considered_headings":  len(considered),
            "h1_count":             h1_count,
            "issue_count":          len(issues),
        },
    }