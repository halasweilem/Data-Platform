"""Canonical schema and compatibility converters for Retail product documents."""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

SCHEMA_VERSION = 1
NODE_TYPES = {"group", "paragraph", "bullet_list", "numbered_list", "section", "table"}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:48] or "content"


def _node_id(kind: str, path: list[str], occurrence: int = 0) -> str:
    identity = "/".join([*path, str(occurrence)])
    return f"{kind}-{_slug(path[-1] if path else kind)}-{hashlib.sha256(identity.encode()).hexdigest()[:8]}"


def blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        if block.get("type") == "paragraph":
            if _text(block.get("text")):
                parts.append(_text(block.get("text")))
            continue
        items = [_text(item) for item in block.get("items", []) if _text(item)]
        if not items:
            continue
        numbered = block.get("type") == "numbered_list"
        body = "\n".join(f"{index + 1}. {item}" if numbered else f"• {item}" for index, item in enumerate(items))
        label = _text(block.get("label"))
        parts.append(f"{label}:\n{body}" if label else body)
    return "\n\n".join(parts)


def document_from_sections(title: str, sections: list[dict[str, Any]], source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert legacy/editable leaf sections into the canonical nested document."""
    name = _text(title) or "Retail product"
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": _node_id("product", [name]),
        "name": name,
        "source": deepcopy(source or {}),
        "content": [],
    }
    occurrences: dict[str, int] = {}

    def group(parent: list[dict[str, Any]], label: str, path: list[str]) -> dict[str, Any]:
        for node in parent:
            if node.get("type") == "group" and _text(node.get("title")).casefold() == label.casefold():
                return node
        node = {"id": _node_id("group", path), "type": "group", "title": label, "children": []}
        parent.append(node)
        return node

    for section in sections:
        if not isinstance(section, dict):
            continue
        stored_path = section.get("path")
        path = [_text(part).strip(" :") for part in stored_path if _text(part).strip(" :")] if isinstance(stored_path, list) else [_text(part).strip(" :") for part in str(section.get("section_title", "")).split(">") if _text(part).strip(" :")]
        if path and path[0].casefold() == name.casefold():
            path = path[1:]
        if not path:
            path = [_text(section.get("display_title") or section.get("section")) or "Content"]
        children = document["content"]
        walked: list[str] = [name]
        for label in path[:-1]:
            walked.append(label)
            children = group(children, label, walked)["children"]
        full_path = [name, *path]
        key = "/".join(part.casefold() for part in full_path)
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        if section.get("variant") == "table":
            rows = section.get("table_rows")
            if not isinstance(rows, list):
                rows = [[_text(cell) for cell in line.split("|")] for line in str(section.get("content", "")).splitlines() if _text(line)]
            leaf = {"id": section.get("id") or _node_id("table", full_path, occurrence), "type": "table", "title": path[-1], "rows": deepcopy(rows)}
        else:
            blocks = deepcopy(section.get("content_blocks") or [{"type": "paragraph", "text": _text(section.get("content"))}])
            node_type = blocks[0].get("type") if len(blocks) == 1 and blocks[0].get("type") in NODE_TYPES else "section"
            leaf = {"id": section.get("id") or _node_id(node_type, full_path, occurrence), "type": node_type, "title": path[-1], "blocks": blocks}
        leaf["source"] = {key: deepcopy(section[key]) for key in ("page", "images", "language") if key in section}
        children.append(leaf)
    return document


def sections_from_document(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Create the editable/Milvus-compatible leaf view from a canonical document."""
    title = _text(document.get("name")) or "Retail product"
    sections: list[dict[str, Any]] = []

    def visit(nodes: list[Any], trail: list[str]) -> None:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            node_title = _text(node.get("title")) or "Content"
            if node_type == "group":
                visit(node.get("children", []), [*trail, node_title])
                continue
            path = [title, *trail, node_title]
            source = node.get("source") if isinstance(node.get("source"), dict) else {}
            section: dict[str, Any] = {
                "id": node.get("id"), "topic": title, "section": node_title,
                "section_title": " > ".join(path), "path": path, "display_title": node_title,
                "variant": "table" if node_type == "table" else node_type,
                "language": source.get("language", "en"), "images": deepcopy(source.get("images", [])),
            }
            if "page" in source:
                section["page"] = source["page"]
            if node_type == "table":
                rows = deepcopy(node.get("rows", []))
                section["table_rows"] = rows
                section["content"] = "\n".join(" | ".join(_text(cell) for cell in row) for row in rows)
            else:
                blocks = deepcopy(node.get("blocks", []))
                section["content_blocks"] = blocks
                section["content"] = blocks_to_text(blocks)
            sections.append(section)
    visit(document.get("content", []), [])
    return sections


def validate_document(document: dict[str, Any]) -> list[str]:
    """Return human-readable schema errors; an empty list means valid."""
    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not _text(document.get("id")):
        errors.append("document id is required")
    if not _text(document.get("name")):
        errors.append("document name is required")
    if not isinstance(document.get("content"), list):
        errors.append("document content must be a list")
        return errors
    seen: set[str] = set()

    def visit(nodes: list[Any], location: str) -> None:
        for index, node in enumerate(nodes):
            here = f"{location}[{index}]"
            if not isinstance(node, dict):
                errors.append(f"{here} must be an object")
                continue
            node_id = _text(node.get("id"))
            if not node_id:
                errors.append(f"{here}.id is required")
            elif node_id in seen:
                errors.append(f"{here}.id is duplicated")
            seen.add(node_id)
            if node.get("type") not in NODE_TYPES:
                errors.append(f"{here}.type is invalid")
            if not _text(node.get("title")):
                errors.append(f"{here}.title is required")
            if node.get("type") == "group":
                if not isinstance(node.get("children"), list):
                    errors.append(f"{here}.children must be a list")
                else:
                    visit(node["children"], f"{here}.children")
            elif node.get("type") == "table" and not isinstance(node.get("rows"), list):
                errors.append(f"{here}.rows must be a list")
            elif node.get("type") != "table" and not isinstance(node.get("blocks"), list):
                errors.append(f"{here}.blocks must be a list")
    visit(document["content"], "content")
    return errors
