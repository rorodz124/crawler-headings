from __future__ import annotations

from config import AuditConfig


def normalize_heading_text(text: str, audit_config: AuditConfig) -> str:
    value = text or ""
    for token in audit_config.empty_text_tokens:
        value = value.replace(token, " ")
    return " ".join(value.strip(audit_config.strip_chars).split())


def validate_headings(page_data: dict, audit_config: AuditConfig) -> dict:
    raw_headings = page_data.get("headings", [])
    normalized_headings = []

    for index, heading in enumerate(raw_headings, start=1):
        normalized = {
            "index": index,
            "tag": heading["tag"],
            "level": heading["level"],
            "text": normalize_heading_text(heading.get("text", ""), audit_config),
            "visible": heading.get("visible", False),
            "hiddenByAttr": heading.get("hiddenByAttr", False),
            "hiddenByStyle": heading.get("hiddenByStyle", False),
        }
        normalized["is_empty"] = not normalized["text"]
        normalized_headings.append(normalized)

    considered = [
        heading
        for heading in normalized_headings
        if heading["visible"] or not audit_config.validate_visible_only
    ]

    issues = []
    h1_count = sum(1 for heading in considered if heading["level"] == 1)

    if audit_config.require_single_h1:
        if h1_count == 0:
            issues.append(
                {
                    "rule": "single_h1",
                    "message": "A página não contém nenhum h1 válido.",
                }
            )
        elif h1_count > 1:
            issues.append(
                {
                    "rule": "single_h1",
                    "message": f"A página contém {h1_count} headings h1; deve existir apenas um.",
                }
            )

    for heading in considered:
        if heading["is_empty"]:
            issues.append(
                {
                    "rule": "empty_heading",
                    "message": f"{heading['tag']} vazio na posição {heading['index']}.",
                    "heading_index": heading["index"],
                }
            )

    previous_level = None
    for heading in considered:
        current_level = heading["level"]
        if previous_level is None:
            if current_level > 1:
                issues.append(
                    {
                        "rule": "starts_too_deep",
                        "message": (
                            f"A página começa a estrutura de headings com {heading['tag']}, "
                            "sem níveis anteriores."
                        ),
                        "heading_index": heading["index"],
                    }
                )
        elif current_level > previous_level + 1:
            issues.append(
                {
                    "rule": "hierarchy_skip",
                    "message": (
                        f"Salto inválido de h{previous_level} para h{current_level} "
                        f"na posição {heading['index']}."
                    ),
                    "heading_index": heading["index"],
                }
            )
        previous_level = current_level

    return {
        "url": page_data.get("url", ""),
        "title": page_data.get("title", ""),
        "headings": normalized_headings if not audit_config.ignore_hidden_in_report else considered,
        "considered_headings": considered,
        "issues": issues,
        "valid": not issues,
        "summary": {
            "total_headings": len(normalized_headings),
            "considered_headings": len(considered),
            "h1_count": h1_count,
            "issue_count": len(issues),
        },
    }
