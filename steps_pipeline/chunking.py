"""Steps & workflows -> Milvus row builder.

Mirrors the row shape and chunk_id scheme that the financial-advisor backend's
core/milvus_steps.py builds and queries (content_type, workflow_path, step_id,
parent_step, modality, raw_json, image_path, ...), so anything Data Studio
publishes here stays readable by that backend's search_steps() and
get_workflow_steps_by_path().

Pure data transform: no Milvus client, no embedding model. Each returned row is
a plain dict with the real schema's fields, plus two transient markers consumed
by the caller (app.py) to actually compute embeddings:
  - "_embed_text": text to run through the text embedding model, or None
  - "_embed_image_rel": Steps-root-relative POSIX path of an image to embed, or None
Strip both before inserting into Milvus.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

KNOWN_STEP_KEYS = {
    "step", "step_label", "text", "image", "fields", "rules", "actions",
    "substeps", "cases", "condition", "options", "option", "result",
}


def stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            parts.append(json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item))
        return ", ".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def normalize_images(image_value: Union[str, list, None]) -> list[str]:
    if image_value is None:
        return []
    if isinstance(image_value, str):
        return [img.strip() for img in image_value.split(",") if img.strip()]
    if isinstance(image_value, list):
        result = []
        for item in image_value:
            if item is None:
                continue
            item_str = str(item).strip()
            if item_str:
                result.append(item_str)
        return result
    item_str = str(image_value).strip()
    return [item_str] if item_str else []


def normalize_step(item: dict) -> dict:
    step_identifier = item.get("step")
    if step_identifier is None and "Note" in item:
        step_identifier = f"Note-{item['Note']}"
    step_identifier = str(step_identifier) if step_identifier is not None else ""
    step_label = item.get("step_label", step_identifier)
    step_label = str(step_label) if step_label is not None else ""
    return {
        "step_id": step_identifier,
        "step_label": step_label,
        "text": item.get("text", ""),
        "images": normalize_images(item.get("image")),
        "fields": item.get("fields", []) or [],
        "rules": item.get("rules", []) or [],
        "actions": item.get("actions", []) or [],
        "substeps": item.get("substeps", []) or [],
        "condition": item.get("condition", ""),
        "raw": item,
    }


def build_step_text(workflow_meta: dict, step: dict, parent_text: str = "") -> str:
    lines = []
    if parent_text:
        lines.append(f"Parent step context: {parent_text}")
        lines.append("")
    if workflow_meta.get("main_topic"):
        lines.append(f"Main topic: {workflow_meta['main_topic']}")
    if workflow_meta.get("path_parts"):
        lines.append(f"Path: {' > '.join(workflow_meta['path_parts'])}")
    if workflow_meta.get("workflow_name"):
        lines.append(f"Workflow: {workflow_meta['workflow_name']}")
    if workflow_meta.get("title"):
        lines.append(f"Title: {workflow_meta['title']}")
    if workflow_meta.get("subtitle"):
        lines.append(f"Subtitle: {workflow_meta['subtitle']}")

    condition = step.get("condition", "")
    if condition:
        lines.append(f"Condition: {condition}")

    step_label = step.get("step_label", "")
    text = step.get("text", "")
    if step_label:
        lines.append(f"Step {step_label}: {text}")
    elif text:
        lines.append(text)

    fields = step.get("fields") or []
    if fields:
        lines.append("Fields:")
        for f in fields:
            if not isinstance(f, dict):
                lines.append(f"- {stringify_value(f)}")
                continue
            desc = f"- {f.get('name', '')}".strip()
            if f.get("type"):
                desc += f" ({f['type']})"
            if f.get("default") is not None:
                desc += f", default: {f['default']}"
            if f.get("options"):
                desc += f", options: {', '.join(map(str, f['options']))}"
            lines.append(desc)
            extra_field_keys = set(f.keys()) - {"name", "type", "default", "options"}
            for key in sorted(extra_field_keys):
                value = stringify_value(f.get(key))
                if value:
                    lines.append(f"  - {key}: {value}")

    rules = step.get("rules") or []
    if rules:
        lines.append("Rules:")
        for r in rules:
            lines.append(f"- {stringify_value(r)}")

    actions = step.get("actions") or []
    if actions:
        lines.append("Actions:")
        for a in actions:
            lines.append(f"- {stringify_value(a)}")

    substeps = step.get("substeps") or []
    if substeps:
        lines.append("Substeps:")
        for sub in substeps:
            if isinstance(sub, dict):
                sub_no = sub.get("step", "")
                sub_text = sub.get("text", "")
                if sub_no and sub_text:
                    lines.append(f"- {sub_no}: {sub_text}")
                else:
                    lines.append(f"- {json.dumps(sub, ensure_ascii=False)}")
            else:
                lines.append(f"- {stringify_value(sub)}")

    extra_keys = set(step["raw"].keys()) - KNOWN_STEP_KEYS
    if extra_keys:
        lines.append("Additional Data:")
        for key in sorted(extra_keys):
            value = stringify_value(step["raw"].get(key))
            if value:
                lines.append(f"- {key}: {value}")

    return "\n".join(line for line in lines if str(line).strip())


def build_workflow_summary_text(workflow_meta: dict, workflow_data: dict) -> str:
    lines = [
        f"Main topic: {workflow_meta['main_topic']}",
        f"Path: {' > '.join(workflow_meta['path_parts'])}",
        f"Workflow: {workflow_meta['workflow_name']}",
    ]
    if workflow_data.get("topic"):
        lines.append(f"Topic field: {workflow_data['topic']}")
    if workflow_data.get("title"):
        lines.append(f"Title: {workflow_data['title']}")
    if workflow_data.get("subtitle"):
        lines.append(f"Subtitle: {workflow_data['subtitle']}")
    steps = workflow_data.get("steps", [])
    if steps:
        lines.append("Steps summary:")
        for item in steps:
            if not isinstance(item, dict):
                continue
            if "step" in item:
                lines.append(f"- Step {item.get('step')}: {item.get('text', '')}")
            elif "Note" in item:
                lines.append(f"- Note {item.get('Note')}: {item.get('text', '')}")
    return "\n".join(lines)


def make_safe_key(value: str) -> str:
    value = (value or "").lower().strip()
    result = []
    for ch in value:
        result.append(ch if ch.isalnum() else "_")
    key = "".join(result)
    while "__" in key:
        key = key.replace("__", "_")
    return key.strip("_") or "option"


def build_option_children(*, parent_step_id: str, options: list, parent_option_label: str = "", level: int = 1) -> list[tuple[str, dict, str]]:
    children = []
    for opt_idx, opt in enumerate(options or [], start=1):
        if not isinstance(opt, dict):
            continue
        option_name = str(opt.get("option", f"Option {opt_idx}")).strip()
        option_key = make_safe_key(option_name)
        if parent_option_label:
            display_label = f"{parent_step_id} - {parent_option_label} > {option_name}"
            text = f"Parent option: {parent_option_label}. Option: {option_name}. Result: {opt.get('result', '')}"
            step_id = f"{parent_step_id}_option_{make_safe_key(parent_option_label)}_{option_key}"
        else:
            display_label = f"{parent_step_id} - {option_name}"
            text = f"Option: {option_name}. Result: {opt.get('result', '')}"
            step_id = f"{parent_step_id}_option_{option_key}"
        option_payload = {
            "step": step_id, "step_label": display_label, "text": text.strip(),
            "image": opt.get("image"), "option": option_name, "result": opt.get("result", ""),
            "raw_option": opt,
        }
        children.append(("option", option_payload, parent_step_id))
        nested_options = opt.get("options", []) or []
        if nested_options:
            children.extend(build_option_children(parent_step_id=parent_step_id, options=nested_options, parent_option_label=option_name, level=level + 1))
    return children


def flatten_json_for_search(value: Any, prefix: str = "") -> list[str]:
    lines = []
    if isinstance(value, dict):
        for key, val in value.items():
            clean_key = str(key).replace("_", " ").strip()
            next_prefix = f"{prefix} > {clean_key}" if prefix else clean_key
            if isinstance(val, (dict, list)):
                lines.append(f"{next_prefix}:")
                lines.extend(flatten_json_for_search(val, next_prefix))
            else:
                text_val = stringify_value(val)
                if text_val:
                    lines.append(f"{next_prefix}: {text_val}")
    elif isinstance(value, list):
        for idx, item in enumerate(value, start=1):
            next_prefix = f"{prefix} item {idx}" if prefix else f"item {idx}"
            if isinstance(item, (dict, list)):
                lines.extend(flatten_json_for_search(item, next_prefix))
            else:
                text_val = stringify_value(item)
                if text_val:
                    lines.append(f"{next_prefix}: {text_val}")
    else:
        text_val = stringify_value(value)
        if text_val:
            lines.append(f"{prefix}: {text_val}" if prefix else text_val)
    return lines


def extract_overview_items(data: dict) -> list[dict]:
    items = []
    if not isinstance(data, dict):
        return items
    for idx, item in enumerate(data.get("services", []) or [], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("service", f"Service {idx}")).strip()
        details = item.get("description", [])
        detail_lines = [str(x).strip() for x in details if str(x).strip()] if isinstance(details, list) else ([str(details).strip()] if details else [])
        items.append({"kind": "service", "title": title, "details": detail_lines, "raw": item})
    for idx, item in enumerate(data.get("features", []) or [], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("name", f"Feature {idx}")).strip()
        details = item.get("details", [])
        detail_lines = [str(x).strip() for x in details if str(x).strip()] if isinstance(details, list) else ([str(details).strip()] if details else [])
        items.append({"kind": "feature", "title": title, "details": detail_lines, "raw": item})
    return items


def _base_row(*, modality: str, content_type: str, workflow_meta: dict, step_label: str, step_id: str,
              parent_step: str, order_index: int, chunk_id: str, content: str, raw_json: str,
              image_rel: str | None, embed_text: str | None) -> dict:
    return {
        "modality": modality,
        "content_type": content_type,
        "main_topic": workflow_meta["main_topic"][:128],
        "workflow_name": workflow_meta["workflow_name"][:256],
        "workflow_path": workflow_meta["workflow_path"][:512],
        "path_json": safe_json_dumps(workflow_meta["path_parts"])[:2048],
        "topic": workflow_meta["main_topic"][:128],
        "subtopic": " / ".join(workflow_meta["subtopics"])[:256],
        "step_label": str(step_label or "")[:120],
        "step_id": str(step_id or "")[:120],
        "parent_step": str(parent_step or "")[:120],
        "order_index": order_index,
        "chunk_id": str(chunk_id or "")[:500],
        "content": content,
        "raw_json": raw_json[:32000],
        "image_path": (f"data/Steps/{image_rel}" if image_rel else "")[:1024],
        "_embed_text": embed_text,
        "_embed_image_rel": image_rel,
    }


def resolve_image_rel(image_name: str, workflow_dir_rel: str, steps_root: Path) -> str | None:
    """Find an image referenced by a step and return its Steps-root-relative POSIX path."""
    image_name = str(image_name or "").strip()
    if not image_name:
        return None
    assets_dir = (steps_root / workflow_dir_rel) if workflow_dir_rel else steps_root
    possible_names = [image_name]
    if "." not in Path(image_name).name:
        possible_names.extend([f"{image_name}.png", f"{image_name}.jpg", f"{image_name}.jpeg", f"{image_name}.webp"])
    candidates = []
    for name in possible_names:
        candidates.extend([assets_dir / name, assets_dir / "images" / name, assets_dir.parent / name, assets_dir.parent / "images" / name])
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve().relative_to(steps_root.resolve()).as_posix()
    for name in possible_names:
        matches = list(assets_dir.rglob(name)) if assets_dir.is_dir() else []
        if matches:
            return matches[0].resolve().relative_to(steps_root.resolve()).as_posix()
    return None


def build_entry_rows(*, workflow_meta: dict, content_type: str, step_id: str, parent: str, order: int,
                      step_payload: dict, workflow_dir_rel: str, steps_root: Path, parent_text: str = "") -> list[dict]:
    rows = []
    step_label = step_payload.get("step_label", "")
    full_text = build_step_text(workflow_meta, step_payload, parent_text=parent_text)
    raw_json = safe_json_dumps(step_payload["raw"])
    if full_text.strip():
        rows.append(_base_row(
            modality="text", content_type=content_type, workflow_meta=workflow_meta, step_label=step_label,
            step_id=step_id, parent_step=parent, order_index=order,
            chunk_id=f"{workflow_meta['workflow_path']}::{content_type}::{step_id}::text",
            content=full_text, raw_json=raw_json, image_rel=None, embed_text=full_text,
        ))
    for idx, img in enumerate(step_payload.get("images", []), start=1):
        image_rel = resolve_image_rel(img, workflow_dir_rel, steps_root)
        if not image_rel:
            continue
        rows.append(_base_row(
            modality="image", content_type=content_type, workflow_meta=workflow_meta, step_label=step_label,
            step_id=step_id, parent_step=parent, order_index=order,
            chunk_id=f"{workflow_meta['workflow_path']}::{content_type}::{step_id}::image::{idx}",
            content=full_text, raw_json=raw_json, image_rel=image_rel, embed_text=None,
        ))
    return rows


def workflow_meta_for_dir(workflow_dir_rel: str, data: dict) -> dict:
    path_parts = [p for p in workflow_dir_rel.split("/") if p] if workflow_dir_rel else []
    main_topic = path_parts[0] if path_parts else "general"
    subtopics = path_parts[1:] if len(path_parts) > 1 else ["general"]
    workflow_name = path_parts[-1] if path_parts else "workflow"
    return {
        "main_topic": main_topic, "subtopics": subtopics, "workflow_name": workflow_name,
        "workflow_path": workflow_dir_rel, "path_parts": path_parts,
        "title": data.get("title", "") if isinstance(data, dict) else "",
        "subtitle": data.get("subtitle", "") if isinstance(data, dict) else "",
        "topic_field": data.get("topic", "") if isinstance(data, dict) else "",
    }


def build_workflow_rows(*, workflow_dir_rel: str, data: dict, steps_root: Path) -> list[dict]:
    """workflow.json -> rows. workflow_dir_rel is the folder's path relative to the Steps root."""
    if not isinstance(data, dict):
        return []
    workflow_meta = workflow_meta_for_dir(workflow_dir_rel, data)
    rows: list[dict] = []
    order = 0

    summary_text = build_workflow_summary_text(workflow_meta, data)
    rows.append(_base_row(
        modality="text", content_type="workflow", workflow_meta=workflow_meta, step_label="", step_id="workflow",
        parent_step="", order_index=0, chunk_id=f"{workflow_meta['workflow_path']}::workflow",
        content=summary_text, raw_json=safe_json_dumps(data), image_rel=None, embed_text=summary_text,
    ))

    for item in data.get("steps", []):
        if not isinstance(item, dict):
            continue
        order += 1

        if "step" in item:
            step = normalize_step(item)
            parent_full_text = build_step_text(workflow_meta, step)
            rows.extend(build_entry_rows(workflow_meta=workflow_meta, content_type="step", step_id=step["step_id"],
                                          parent="", order=order, step_payload=step, workflow_dir_rel=workflow_dir_rel,
                                          steps_root=steps_root))

            nested_children = []
            for sub in item.get("substeps", []) or []:
                if isinstance(sub, dict):
                    nested_children.append(("substep", sub, step["step_id"]))
            for nested in item.get("steps", []) or []:
                if isinstance(nested, dict):
                    nested_children.append(("substep", nested, step["step_id"]))
            nested_children.extend(build_option_children(parent_step_id=step["step_id"], options=item.get("options", []) or []))

            for case_idx, case in enumerate(item.get("cases", []) or [], start=1):
                if not isinstance(case, dict):
                    continue
                condition = case.get("condition", "").strip()
                case_steps = case.get("steps", []) or []
                condition_lower = condition.lower()
                if "jod" in condition_lower:
                    branch_key, branch_label = "jod", "JOD"
                elif "usd" in condition_lower:
                    branch_key, branch_label = "usd", "USD"
                else:
                    branch_key, branch_label = f"case_{case_idx}", f"Case {case_idx}"

                condition_summary = {
                    "step": f"{step['step_id']}_{branch_key}_branch", "step_label": f"{step['step_id']} ({branch_label})",
                    "text": f"{condition}. This branch applies after parent step {step['step_id']}: {item.get('text', '')}",
                    "image": item.get("image", ""), "condition": condition, "case_index": case_idx, "case_steps": case_steps,
                }
                nested_children.append(("condition", condition_summary, condition))

                for case_substep in case_steps:
                    if not isinstance(case_substep, dict):
                        continue
                    case_substep_copy = dict(case_substep)
                    original_step_id = str(case_substep_copy.get("step", "")).strip()
                    already_unique = branch_key in original_step_id.lower() or original_step_id.startswith(f"{step['step_id']}.")
                    internal_step_id = original_step_id if already_unique else f"{step['step_id']}_{branch_key}_{original_step_id}"
                    if branch_key in {"jod", "usd"}:
                        display_label = original_step_id if original_step_id.startswith(f"{step['step_id']}.") else f"{step['step_id']}.{original_step_id}"
                        display_label = display_label.replace("_jod", "").replace("_usd", "").strip()
                        display_label = f"{display_label} ({branch_label})"
                    else:
                        display_label = f"{step['step_id']}.{original_step_id} ({branch_label})"
                    case_substep_copy["step"] = internal_step_id
                    case_substep_copy["step_label"] = display_label
                    case_substep_copy["condition"] = condition
                    nested_children.append(("condition_step", case_substep_copy, condition))

            for child_type, child, third_value in nested_children:
                order += 1
                child_step = normalize_step(child)
                condition = third_value if child_type in {"condition", "condition_step"} else child_step.get("condition", "")
                child_step["condition"] = condition or child_step.get("condition", "")
                condition_parent_text = parent_full_text
                if condition:
                    condition_parent_text = f"{parent_full_text}\n\nSelected condition / branch: {condition}"
                rows.extend(build_entry_rows(workflow_meta=workflow_meta, content_type=child_type, step_id=child_step["step_id"],
                                              parent=step["step_id"], order=order, step_payload=child_step,
                                              workflow_dir_rel=workflow_dir_rel, steps_root=steps_root,
                                              parent_text=condition_parent_text))

        elif "Note" in item:
            note_step = normalize_step(item)
            rows.extend(build_entry_rows(workflow_meta=workflow_meta, content_type="note", step_id=note_step["step_id"],
                                          parent="", order=order, step_payload=note_step, workflow_dir_rel=workflow_dir_rel,
                                          steps_root=steps_root))

    return rows


def build_tips_rows(*, workflow_dir_rel: str, data: Any) -> list[dict]:
    tips = data.get("tips", []) if isinstance(data, dict) else data
    if not isinstance(tips, list):
        return []
    workflow_meta = workflow_meta_for_dir(workflow_dir_rel, {})
    rows = []
    for i, tip in enumerate(tips, start=1):
        if isinstance(tip, dict):
            text, raw = tip.get("text", ""), tip
        else:
            text, raw = str(tip), {"text": str(tip)}
        if not text:
            continue
        full_text = f"Main topic: {workflow_meta['main_topic']}\nPath: {' > '.join(workflow_meta['path_parts'])}\nTip:\n{text}"
        rows.append(_base_row(
            modality="text", content_type="tip", workflow_meta=workflow_meta, step_label="", step_id=f"tip_{i}",
            parent_step="", order_index=i, chunk_id=f"{workflow_meta['workflow_path']}::tip::{i}",
            content=full_text, raw_json=safe_json_dumps(raw), image_rel=None, embed_text=full_text,
        ))
    return rows


def build_faq_rows(*, workflow_dir_rel: str, data: Any) -> list[dict]:
    if not isinstance(data, list):
        return []
    workflow_meta = workflow_meta_for_dir(workflow_dir_rel, {})
    rows = []
    for i, faq in enumerate(data, start=1):
        if not isinstance(faq, dict):
            continue
        question, answer = faq.get("question", ""), faq.get("answer", "")
        if not question and not answer:
            continue
        combined = f"Main topic: {workflow_meta['main_topic']}\nPath: {' > '.join(workflow_meta['path_parts'])}\nFAQ:\nQ: {question}\nA: {answer}".strip()
        rows.append(_base_row(
            modality="text", content_type="faq", workflow_meta=workflow_meta, step_label="", step_id=f"faq_{i}",
            parent_step="", order_index=i, chunk_id=f"{workflow_meta['workflow_path']}::faq::{i}",
            content=combined, raw_json=safe_json_dumps(faq), image_rel=None, embed_text=combined,
        ))
    return rows


def build_general_json_rows(*, relative: str, data: Any) -> list[dict]:
    """Any Steps .json that isn't workflow.json/tips.json/faq.json. relative includes the filename."""
    path_parts_all = [p for p in relative.split("/") if p]
    workflow_dir_rel = "/".join(path_parts_all[:-1])
    filename = path_parts_all[-1] if path_parts_all else relative
    workflow_name = Path(filename).stem

    dir_parts = [p for p in workflow_dir_rel.split("/") if p] if workflow_dir_rel else []
    main_topic = dir_parts[0] if dir_parts else workflow_name
    workflow_meta = {
        "main_topic": main_topic, "workflow_name": workflow_name, "workflow_path": relative,
        "path_parts": dir_parts + [filename], "subtopics": dir_parts[1:] if len(dir_parts) > 1 else ["overview"],
        "title": workflow_name.replace("_", " ").title(), "subtitle": "", "topic_field": "",
    }

    if not isinstance(data, dict):
        return []

    rows = []
    topic = data.get("topic", "")
    section = data.get("section", "")
    file_type = data.get("type", "")
    description = data.get("description", "")

    lines = [
        f"Main topic: {workflow_meta['main_topic']}",
        f"Path: {' > '.join(workflow_meta['path_parts'])}",
        f"JSON file: {filename}",
        f"JSON name: {workflow_name}",
    ]
    if topic:
        lines.append(f"Topic: {topic}")
    if section:
        lines.append(f"Section: {section}")
    if file_type:
        lines.append(f"Type: {file_type}")
    if description:
        lines.append(f"Description: {description}")
    lines.append("")
    lines.append("JSON content:")
    lines.extend(flatten_json_for_search(data))
    full_text = "\n".join(line for line in lines if str(line).strip())[:7800]

    if full_text.strip():
        rows.append(_base_row(
            modality="text", content_type="overview", workflow_meta=workflow_meta, step_label="overview",
            step_id="overview", parent_step="", order_index=0, chunk_id=f"{relative}::overview",
            content=full_text, raw_json=safe_json_dumps(data)[:32000], image_rel=None, embed_text=full_text,
        ))

    for idx, item in enumerate(extract_overview_items(data), start=1):
        item_lines = [f"Main topic: {workflow_meta['main_topic']}", f"Path: {' > '.join(workflow_meta['path_parts'])}"]
        if topic:
            item_lines.append(f"Topic: {topic}")
        if section:
            item_lines.append(f"Section: {section}")
        if file_type:
            item_lines.append(f"Type: {file_type}")
        if description:
            item_lines.append(f"Overview description: {description}")
        item_lines.extend([f"{item['kind'].title()}: {item['title']}", "", "Details:"])
        for detail in item["details"]:
            item_lines.append(f"- {detail}")
        item_text = "\n".join(line for line in item_lines if str(line).strip())[:7800]
        rows.append(_base_row(
            modality="text", content_type="overview", workflow_meta=workflow_meta, step_label=item["title"],
            step_id=f"{item['kind']}_{idx}", parent_step="overview", order_index=idx,
            chunk_id=f"{relative}::overview::{item['kind']}::{idx}", content=item_text,
            raw_json=safe_json_dumps(item["raw"])[:32000], image_rel=None, embed_text=item_text,
        ))

    return rows


def build_rows_for_source(*, relative: str, data: Any, steps_root: Path) -> list[dict]:
    """Dispatch by filename, matching the four ingestion paths in the real backend."""
    filename = relative.rsplit("/", 1)[-1]
    workflow_dir_rel = relative.rsplit("/", 1)[0] if "/" in relative else ""
    if filename == "workflow.json":
        return build_workflow_rows(workflow_dir_rel=workflow_dir_rel, data=data, steps_root=steps_root)
    if filename == "tips.json":
        return build_tips_rows(workflow_dir_rel=workflow_dir_rel, data=data)
    if filename == "faq.json":
        return build_faq_rows(workflow_dir_rel=workflow_dir_rel, data=data)
    return build_general_json_rows(relative=relative, data=data)
