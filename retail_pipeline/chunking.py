"""Convert canonical Retail content blocks into Milvus-ready readable text."""
from __future__ import annotations

import re
from typing import Any


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def blocks_to_text(blocks: Any) -> str:
    """Preserve headings and list boundaries when flattening structured blocks."""
    parts: list[str] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "paragraph":
            paragraph = _text(block.get("text"))
            if paragraph:
                parts.append(paragraph)
            continue
        items = [_text(item) for item in block.get("items", []) if _text(item)] if isinstance(block.get("items"), list) else []
        if not items:
            continue
        label = _text(block.get("label"))
        marker = "\n".join(f"{index + 1}. {item}" for index, item in enumerate(items)) if block.get("type") == "numbered_list" else "\n".join(f"• {item}" for item in items)
        parts.append(f"{label}:\n{marker}" if label else marker)
    return "\n\n".join(parts)
