"""Data Studio: role-aware editing, local chunk audit, and Milvus synchronization."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from contextlib import closing
from functools import wraps
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import Flask, abort, g, jsonify, redirect, request, send_file, send_from_directory, session
from dotenv import load_dotenv
from retail_pipeline.chunking import blocks_to_text as retail_blocks_text
from retail_pipeline.schema import SCHEMA_VERSION, document_from_sections, sections_from_document, validate_document
from steps_pipeline.chunking import build_rows_for_source


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"
DATA_ROOT = (BASE_DIR / "Data" if DEV_MODE else Path(os.getenv("DATA_ROOT", BASE_DIR / "Data"))).resolve()
RETAIL_ROOT = (DATA_ROOT / "retail").resolve()
AUDIT_ROOT = (BASE_DIR / "data_chunks" if DEV_MODE else Path(os.getenv("AUDIT_ROOT", BASE_DIR / "data_chunks"))).resolve()
HISTORY_ROOT = (BASE_DIR / ".studio" / "history" if DEV_MODE else Path(os.getenv("HISTORY_ROOT", BASE_DIR / ".studio" / "history"))).resolve()
PENDING_ROOT = (BASE_DIR / ".studio" / "pending" if DEV_MODE else Path(os.getenv("PENDING_ROOT", BASE_DIR / ".studio" / "pending"))).resolve()
EXTRACTED_ROOT = (BASE_DIR / ".studio" / "extracted" if DEV_MODE else Path(os.getenv("EXTRACTED_ROOT", BASE_DIR / ".studio" / "extracted"))).resolve()
PUBLISHED_ROOT = (BASE_DIR / ".studio" / "published" if DEV_MODE else Path(os.getenv("PUBLISHED_ROOT", BASE_DIR / ".studio" / "published"))).resolve()
VERSIONS_ROOT = (BASE_DIR / ".studio" / "versions" if DEV_MODE else Path(os.getenv("VERSIONS_ROOT", BASE_DIR / ".studio" / "versions"))).resolve()
TOPIC_LABELS_PATH = (BASE_DIR / ".studio" / "topic-labels.json").resolve()
RETAIL_COLLECTIONS_PATH = (BASE_DIR / ".studio" / "retail-collections.json").resolve()
RETAIL_COLLECTION_LABELS_PATH = (BASE_DIR / ".studio" / "retail-collection-labels.json").resolve()
ORIGINAL_STEPS_ROOT = (DATA_ROOT / "Steps").resolve()
PUBLISHED_STEPS_ROOT = (PUBLISHED_ROOT / "Steps").resolve()
if not PUBLISHED_STEPS_ROOT.exists() and ORIGINAL_STEPS_ROOT.exists():
    PUBLISHED_STEPS_ROOT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ORIGINAL_STEPS_ROOT, PUBLISHED_STEPS_ROOT)
ACTIVE_STEPS_ROOT = ORIGINAL_STEPS_ROOT if DEV_MODE else PUBLISHED_STEPS_ROOT
MILVUS_HOST = os.getenv("MILVUS_HOST", "10.0.30.51")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
HISTORY_ACTION_WINDOW_HOURS = 48

# Retail PDFs route to the financial advisor's real per-product collections (Data/retail/<folder>),
# not a single generic one - these env var names match core/config.py in that backend exactly.
RETAIL_COLLECTIONS = {
    "cards": os.getenv("CARDS_COLLECTION", "cards"),
    "loans": os.getenv("LOANS_COLLECTION", "loans"),
    "deposits": os.getenv("DEPOSITS_COLLECTION", "deposits"),
}
STEPS_MODEL_ID = os.getenv("MODEL_ID", "google/siglip-base-patch16-224")
STEPS_EMBEDDING_DIM = 768  # SigLIP output dim - matches milvus_steps.py; STEPS_EMBEDDING_DIMENSIONS is unused there too.

DOMAINS = {
    "steps": {
        "label": "Steps & workflows", "root": ACTIVE_STEPS_ROOT, "extensions": {".json"},
        "collection": os.getenv("STEPS_COLLECTION", "steps_workflow"),
    },
    "retail": {
        "label": "Retail products", "root": RETAIL_ROOT, "extensions": {".pdf", ".product"},
        "collection": ", ".join(RETAIL_COLLECTIONS.values()),
    },
}


def retail_collection_for(relative: str) -> str | None:
    """Route both collection folders and legacy root-level Retail files to Milvus."""
    normalized = str(relative).replace("\\", "/").strip("/")
    first_segment, separator, _ = normalized.partition("/")
    collection_key = first_segment.strip().lower()
    if not separator:
        collection_key = collection_key.rsplit(".", 1)[0].strip()
    return RETAIL_COLLECTIONS.get(collection_key)


def resolve_collection(domain: str, relative: str) -> str | None:
    """The single real collection a source belongs to. None means "not synced" (e.g. an unrecognized retail folder)."""
    if domain == "retail":
        return retail_collection_for(relative)
    return DOMAINS[domain]["collection"]

app = Flask(__name__, static_folder="ui", static_url_path="")
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", MAX_CONTENT_LENGTH=50 * 1024 * 1024, SEND_FILE_MAX_AGE_DEFAULT=0)

logger = logging.getLogger("data_studio")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logger.propagate = False
if not logger.handlers:
    terminal = logging.StreamHandler(sys.stdout)
    terminal.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(terminal)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json_env(name: str, default: Any) -> Any:
    try:
        return json.loads(os.getenv(name, "")) if os.getenv(name) else default
    except json.JSONDecodeError:
        return default


def env_users(name: str) -> set[str]:
    return {user.strip().lower() for user in os.getenv(name, "").split(",") if user.strip()}


# Production: configure the explicit groups below, or use USER_ROLES_JSON for a custom mapping.
ADMIN_USERS = env_users("ADMIN_USERS")
MILVUS_USERS = ADMIN_USERS | env_users("MILVUS_USERS") | {"halasw"}
RETAIL_USERS = env_users("RETAIL_USERS")
STEPS_USERS = env_users("STEPS_USERS")
USER_ROLES = {str(k).lower(): list(v) for k, v in load_json_env("USER_ROLES_JSON", {
    "steps.admin": ["steps"], "retail.admin": ["retail"], "data.admin": ["steps", "retail"]
}).items()}
for username in RETAIL_USERS:
    USER_ROLES[username] = ["retail"]
for username in STEPS_USERS:
    USER_ROLES[username] = ["steps"]
for username in ADMIN_USERS:
    USER_ROLES[username] = list(DOMAINS)
DEV_PASSWORDS = load_json_env("DEV_USERS_JSON", {})
APPROVER_USERS = env_users("APPROVER_USERS") | RETAIL_USERS | STEPS_USERS | ADMIN_USERS


def safe_target(domain: str, relative: str = "") -> tuple[Path, Path]:
    cfg = DOMAINS.get(domain)
    if not cfg:
        abort(404, "Unknown data area")
    root = cfg["root"].resolve()
    target = (root / str(relative).replace("/", os.sep)).resolve()
    if target != root and root not in target.parents:
        abort(400, "Invalid path")
    return root, target


def current_user() -> dict[str, Any] | None:
    username = session.get("username")
    if not username:
        return None
    normalized = username.lower()
    return {"username": username, "roles": session.get("roles", []), "approver": normalized in APPROVER_USERS, "admin": normalized in ADMIN_USERS, "milvus_admin": normalized in MILVUS_USERS}


def pending_path(domain: str, relative: str) -> Path:
    """Return the private staging path for a proposed source change."""
    root = (PENDING_ROOT / domain).resolve()
    target = (root / relative.replace("/", os.sep)).resolve()
    if target != root and root not in target.parents:
        abort(400, "Invalid path")
    return target


def pending_info(domain: str, relative: str) -> dict[str, Any] | None:
    path = pending_path(domain, relative)
    meta = path.with_name(path.name + ".meta.json")
    if not path.is_file() or not meta.is_file():
        return None
    return json.loads(meta.read_text(encoding="utf-8"))


def topic_label_map() -> dict[str, str]:
    if not TOPIC_LABELS_PATH.is_file():
        return {}
    try:
        stored = json.loads(TOPIC_LABELS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {str(topic): str(stage) for topic, stage in stored.items() if str(stage) in {"pre_login", "post_login"}}


def topic_label_for_path(relative: str, labels: dict[str, str] | None = None) -> str | None:
    topic = Path(relative).parts[0] if Path(relative).parts else ""
    return (labels or topic_label_map()).get(topic)


def retail_collection_roles() -> dict[str, str]:
    try:
        stored = json.loads(RETAIL_COLLECTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(key): str(value) for key, value in stored.items() if value in {"general", "product"}} if isinstance(stored, dict) else {}


def retail_collection_labels() -> dict[str, str]:
    try:
        stored = json.loads(RETAIL_COLLECTION_LABELS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(key): clean_text(value) for key, value in stored.items() if clean_text(value)} if isinstance(stored, dict) else {}


def extracted_path(relative: str, pending: bool = False) -> Path:
    root = PENDING_ROOT / "retail" if pending else EXTRACTED_ROOT / "retail"
    target = (root / (relative + ".extracted.json").replace("/", os.sep)).resolve()
    root = root.resolve()
    if root not in target.parents:
        abort(400, "Invalid path")
    return target


def structured_retail_path(relative: str) -> Path:
    """Return the canonical business JSON stored beside a Retail source file."""
    root, source = safe_target("retail", relative)
    target = source.with_suffix(".structured.json") if source.suffix.lower() == ".pdf" else source.with_name(source.name + ".structured.json")
    if root not in target.parents:
        abort(400, "Invalid path")
    return target


def retail_section_path(section: dict[str, Any], document_title: str = "") -> list[str]:
    """Return a clean business hierarchy while accepting legacy flattened titles."""
    stored = section.get("path")
    if isinstance(stored, list):
        path = [clean_text(part).strip(" :") for part in stored if clean_text(part).strip(" :")]
    else:
        path = [clean_text(part).strip(" :") for part in str(section.get("section_title", "")).split(">") if clean_text(part).strip(" :")]
    if not path:
        fallback = clean_text(section.get("section")) or "Section"
        path = [clean_text(document_title) or fallback, fallback]
    path[-1] = re.sub(r"^[\u2022\-*]+\s*", "", path[-1]).strip(" :") or path[-1]
    return [part for index, part in enumerate(path) if not index or part.casefold() != path[index - 1].casefold()]


def retail_content_blocks(content: Any) -> list[dict[str, Any]]:
    """Recover paragraphs and lists from PDF text without changing its meaning."""
    text = clean_text(content)
    if not text:
        return []
    bullet_parts = [clean_text(part) for part in re.split(r"\s*[\u2022\u25aa\ufffd]\s*", text)]
    if len(bullet_parts) == 1:
        circle_parts = [clean_text(part) for part in re.split(r"(?:^|\s)o\s+", text)]
        if len(circle_parts) > 2:
            bullet_parts = circle_parts
    if len(bullet_parts) > 1:
        prefix, items = bullet_parts[0], [re.sub(r"^o\s+", "", part, flags=re.IGNORECASE) for part in bullet_parts[1:] if part]
        blocks: list[dict[str, Any]] = []
        label = "Items"
        if prefix.rstrip().endswith(":"):
            # PDF line wrapping is not a semantic paragraph boundary. The full
            # introduction before a list is its heading, even when it wraps.
            label = prefix.strip(" :")
            prefix = ""
        if prefix:
            blocks.append({"type": "paragraph", "text": prefix})
        if items:
            blocks.append({"type": "bullet_list", "label": label, "items": items})
        return blocks
    numbered = list(re.finditer(r"(?:^|\s)(\d+)\.\s+", text))
    if len(numbered) >= 2:
        prefix = text[:numbered[0].start()].strip()
        items = [text[match.end(): numbered[index + 1].start()].strip() if index + 1 < len(numbered) else text[match.end():].strip() for index, match in enumerate(numbered)]
        blocks = ([{"type": "paragraph", "text": prefix}] if prefix else [])
        blocks.append({"type": "numbered_list", "label": "Steps", "items": [item for item in items if item]})
        return blocks
    return [{"type": "paragraph", "text": re.sub(r"^o\s+", "", text, flags=re.IGNORECASE)}]


def normalize_loan_sections(data: dict[str, Any]) -> None:
    """Repair the predictable heading drift and PDF artifacts in loan documents."""
    title = clean_text(data.get("title"))
    raw_sections = data.get("sections", [])
    is_loan = "loan" in title.casefold() or title.casefold() == "salary acquisition" or any("loan" in str(section.get("section_title", "")).casefold() for section in raw_sections if isinstance(section, dict))
    if not is_loan:
        return
    cleaned_sections = []
    classification = re.compile(r"\s*Classified as\s*:\s*CBoJ\s*-\s*Internal\s*", re.IGNORECASE)
    numbered = re.compile(r"^\s*\d+\.\s+")
    last_numbered = ""
    last_category = "General Conditions"

    def business_category(heading: str) -> str:
        value = heading.casefold()
        groups = [
            ("Eligibility", ("target", "age", "income", "employment", "service period", "profession", "sector categor", "job restriction")),
            ("Requirements & Documents", ("required", "document", "guarantee", "verification", "inspection", "valuation", "dealer", "agency", "property condition")),
            ("Fees & Charges", ("fee", "charge", "settlement")),
            ("Financing Details", ("tenor", "loan amount", "financing", "debt burden", "repayment", "number of loans", "credit card", "grace period", "vehicle age", "credit approval")),
            ("Programs", ("program", "buy-out", "construction", "land financing", "additional housing")),
        ]
        return next((category for category, keywords in groups if any(keyword in value for keyword in keywords)), "General Conditions")
    for section in raw_sections:
        if not isinstance(section, dict):
            continue
        content = classification.sub(" ", str(section.get("content", ""))).strip()
        if not clean_text(content):
            continue
        section["content"] = content
        section.pop("content_blocks", None)
        raw_path = retail_section_path(section, title)
        relative = raw_path[1:] if raw_path and raw_path[0].casefold() == title.casefold() else raw_path
        canonical_groups = {"Overview", "Eligibility", "Requirements & Documents", "Fees & Charges", "Financing Details", "Programs", "General Conditions"}
        if relative and relative[0] in canonical_groups:
            if len(relative) > 1 and numbered.match(relative[1]):
                last_numbered = relative[1]
                last_category = relative[0]
            section["path"] = [title, *relative]
            section["section_title"] = " > ".join(section["path"])
            section["section"] = section["path"][-1]
            cleaned_sections.append(section)
            continue
        terms_index = next((index for index, part in enumerate(relative) if "terms and condition" in part.casefold() or "terms a condition" in part.casefold()), None)
        reduced_index = next((index for index, part in enumerate(relative) if "reduced requirement" in part.casefold()), None)
        if reduced_index is not None:
            program = relative[reduced_index]
            tail = relative[reduced_index + 1:]
            section["path"] = [title, "Programs", program, *tail] if tail else [title, "Programs", program]
        elif terms_index is not None:
            tail = relative[terms_index + 1:]
            numbered_indexes = [index for index, part in enumerate(tail) if numbered.match(part)]
            if numbered_indexes:
                current = numbered_indexes[-1]
                last_numbered = tail[current]
                last_category = business_category(last_numbered)
                section["path"] = [title, last_category, tail[current], *tail[current + 1:]]
            else:
                label = tail[-1] if tail else "General Conditions"
                section["path"] = [title, last_category, last_numbered, label] if last_numbered and label.casefold().startswith("o ") else [title, business_category(label), label]
        elif len(relative) <= 1 or (relative and relative[-1].casefold() in {title.casefold(), f"{title} program".casefold()}):
            section["path"] = [title, "Overview", "Program overview"]
        else:
            section["path"] = [title, "Programs", *relative[1:]] if len(relative) > 1 else [title, "Overview", relative[-1]]
        section["section_title"] = " > ".join(section["path"])
        section["section"] = section["path"][-1]
        cleaned_sections.append(section)
    data["sections"] = cleaned_sections


def restore_loan_document_hierarchy(data: dict[str, Any]) -> None:
    """Present every loan using its description, numbered terms, and real subprograms."""
    title = clean_text(data.get("title"))
    loan_titles = {"car loan", "doctors loan", "housing loans", "personal loan", "pl against mortgage", "salary acquisition"}
    if title.casefold() not in loan_titles:
        return
    numbered = re.compile(r"^\s*(?:[1-9]|[12][0-9])\.\s+")
    program_title = "Car Loan Program with Reduced Requirements"
    salary_terms = {
        "job restrictions": 5,
        "incentives (cash bonus)": 6,
        "commitment period": 7,
        "cash bonus disbursement method": 8,
        "credit card limit": 9,
        "required documents": 10,
        "program conditions": 11,
        "campaign duration": 12,
    }
    for section in data.get("sections", []):
        if not isinstance(section, dict):
            continue
        path = retail_section_path(section, title)
        relative = path[1:] if path and path[0].casefold() == title.casefold() else path
        program_index = next((index for index, part in enumerate(relative) if "reduced requirement" in part.casefold()), None)
        numbered_index = next((index for index, part in enumerate(relative) if numbered.match(part)), None)
        description = relative and relative[-1].casefold() in {"program overview", "description"}
        salary_label = relative[-1].lstrip("•▪�- ").strip(" :") if title.casefold() == "salary acquisition" and relative else ""
        salary_number = salary_terms.get(salary_label.casefold())
        if salary_number:
            new_path = [title, "Terms and Conditions", f"{salary_number}. {salary_label}"]
        elif title.casefold() == "car loan" and program_index is not None:
            tail = relative[program_index + 1:]
            new_path = [title, program_title, *tail] if tail else [title, program_title, "Description"]
        elif numbered_index is not None:
            new_path = [title, "Terms and Conditions", *relative[numbered_index:]]
        elif title.casefold() == "housing loans" and any("buy-out program" in part.casefold() for part in relative):
            label = next(part for part in relative if "buy-out program" in part.casefold())
            new_path = [title, "Terms and Conditions", f"22. {label.lstrip('•▪�- ').strip()}"]
        elif title.casefold() == "salary acquisition" and relative:
            new_path = [title, "Terms and Conditions", salary_label]
        elif description:
            new_path = [title, "Description", "Description"]
        else:
            new_path = [title, "Description", "Description"]
        section["path"] = new_path
        section["display_title"] = new_path[-1]
        section["section_title"] = " > ".join(new_path)
        section["section"] = new_path[-1]


def place_retail_tables(sections: list[dict[str, Any]], title: str) -> None:
    """Nest extracted tables under the business category they most likely describe."""
    category_sections = []
    for section in sections:
        path = section.get("path") if isinstance(section.get("path"), list) else []
        if section.get("variant") != "table" and len(path) > 2:
            category_sections.append((path[1], int(section.get("page") or 0)))
    categories = list(dict.fromkeys(name for name, _ in category_sections))
    ignored = {"card", "cards", "loan", "loans", "program", "product", "products", "currency", "fee", "fees", "the", "and"}
    table_numbers: dict[str, int] = {}
    for section in sections:
        if section.get("variant") != "table" or not categories:
            continue
        current = section.get("path") if isinstance(section.get("path"), list) else []
        already_nested = len(current) > 3 and current[2].casefold() == "tables"
        if already_nested:
            owner = current[1]
        else:
            haystack = clean_text(section.get("content")).casefold()
            scored = []
            for category in categories:
                tokens = [token for token in re.findall(r"[a-z0-9$]+", category.casefold()) if token not in ignored and len(token) > 2]
                scored.append((sum(haystack.count(token) for token in tokens), category))
            best_score, owner = max(scored, default=(0, categories[0]))
            if best_score == 0:
                page = int(section.get("page") or 0)
                preceding = [(page - candidate_page, category) for category, candidate_page in category_sections if candidate_page and candidate_page <= page]
                owner = min(preceding, default=(0, categories[0]), key=lambda item: item[0])[1]
        table_name = current[-1] if current else clean_text(section.get("section")) or "Table"
        table_numbers[owner] = table_numbers.get(owner, 0) + 1
        if re.match(r"^Page\s+\d+\s*[-–—]\s*Table\s+\d+$", table_name, re.IGNORECASE):
            table_name = f"{owner} - Table {table_numbers[owner]}"
        section["path"] = [title, owner, "Tables", table_name]
        section["display_title"] = table_name
        section["section_title"] = " > ".join(section["path"])


def normalize_retail_data(data: dict[str, Any]) -> dict[str, Any]:
    """Add the business-facing structure used by the editor to any retail document."""
    if data.get("schema_version") == SCHEMA_VERSION and isinstance(data.get("content"), list):
        data["title"] = clean_text(data.get("name")) or clean_text(data.get("title"))
        # Canonical files created by the extraction pipeline may contain only the
        # nested ``content`` tree. Editor submissions contain both representations,
        # with ``sections`` holding the user's latest changes. Do not replace those
        # edits with the older content tree while staging them for approval.
        if not isinstance(data.get("sections"), list):
            data["sections"] = sections_from_document(data)
    normalize_loan_sections(data)
    restore_loan_document_hierarchy(data)
    title = clean_text(data.get("title"))
    for section in data.get("sections", []):
        if not isinstance(section, dict):
            continue
        path = retail_section_path(section, title)
        section["path"] = path
        section["display_title"] = path[-1]
        if not isinstance(section.get("content_blocks"), list):
            section["content_blocks"] = retail_content_blocks(section.get("content"))
        if len(section["content_blocks"]) == 1 and section["content_blocks"][0].get("type") in {"bullet_list", "numbered_list"} and section["content_blocks"][0].get("label") in {"Items", "Steps"}:
            section["content_blocks"][0]["label"] = path[-1]
    place_retail_tables(data.get("sections", []), title)
    canonical = document_from_sections(title, data.get("sections", []), data.get("source") if isinstance(data.get("source"), dict) else None)
    data.update({key: canonical[key] for key in ("schema_version", "id", "name", "source", "content")})
    data["structure"] = retail_sections_to_tree(data)
    return data


def retail_sections_to_tree(data: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical business document tree from editable leaf sections."""
    title = clean_text(data.get("title")) or "Retail product"
    root: dict[str, Any] = {"type": "document", "title": title, "children": []}

    def category(parent: dict[str, Any], name: str) -> dict[str, Any]:
        for child in parent["children"]:
            if clean_text(child.get("title")).casefold() == name.casefold():
                child["type"] = "category"
                child.setdefault("children", [])
                return child
        node = {"type": "category", "title": name, "children": []}
        parent["children"].append(node)
        return node

    for index, section in enumerate(data.get("sections", [])):
        if not isinstance(section, dict):
            continue
        path = retail_section_path(section, title)
        relative_path = path[1:] if path and path[0].casefold() == title.casefold() else path
        if not relative_path:
            relative_path = [clean_text(section.get("display_title")) or f"Section {index + 1}"]
        parent = root
        for part in relative_path[:-1]:
            parent = category(parent, part)
        blocks = section.get("content_blocks") if isinstance(section.get("content_blocks"), list) else retail_content_blocks(section.get("content"))
        node_type = "table" if section.get("variant") == "table" else (blocks[0].get("type") if len(blocks) == 1 else "section")
        leaf = {"type": node_type, "title": relative_path[-1], "section_index": index}
        if node_type == "table":
            rows = section.get("table_rows")
            leaf["rows"] = rows if isinstance(rows, list) else [[clean_text(cell) for cell in line.split("|")] for line in str(section.get("content", "")).splitlines() if clean_text(line)]
        else:
            leaf["blocks"] = blocks
        existing = next((child for child in parent["children"] if clean_text(child.get("title")).casefold() == relative_path[-1].casefold()), None)
        if existing:
            existing.update({key: value for key, value in leaf.items() if key not in {"type", "title"}})
        else:
            parent["children"].append(leaf)
    return root


def retail_pending_info(relative: str) -> dict[str, Any] | None:
    path = extracted_path(relative, pending=True)
    meta = path.with_name(path.name + ".meta.json")
    if not path.is_file() or not meta.is_file():
        return None
    return json.loads(meta.read_text(encoding="utf-8"))


def retail_pending_changes(relative: str, pending_data: dict[str, Any]) -> dict[str, Any]:
    approved = extracted_path(relative)
    if not approved.is_file():
        return {"document": ["New extracted document"], "sections": [{"index": index, "title": clean_text(section.get("section_title")) or f"Section {index + 1}", "changes": ["New section"]} for index, section in enumerate(pending_data.get("sections", []))]}
    approved_data = json.loads(approved.read_text(encoding="utf-8-sig"))
    document_changes = []
    if clean_text(approved_data.get("title")) != clean_text(pending_data.get("title")):
        document_changes.append("Document name changed")
    old_sections, new_sections = approved_data.get("sections", []), pending_data.get("sections", [])
    section_changes = []
    for index in range(max(len(old_sections), len(new_sections))):
        old = old_sections[index] if index < len(old_sections) else None
        new = new_sections[index] if index < len(new_sections) else None
        if old is None:
            section_changes.append({"index": index, "title": clean_text(new.get("section_title")) or f"Section {index + 1}", "changes": ["New section"]})
            continue
        if new is None:
            section_changes.append({"index": None, "title": clean_text(old.get("section_title")) or f"Section {index + 1}", "changes": ["Section deleted"]})
            continue
        changes = []
        if clean_text(old.get("section_title")) != clean_text(new.get("section_title")):
            changes.append("Heading changed")
        if old.get("variant") == "table" and new.get("variant") == "table":
            def dimensions(section):
                stored = section.get("table_rows")
                rows = stored if isinstance(stored, list) and stored else [line.split("|") for line in str(section.get("content", "")).splitlines()]
                return len(rows), max((len(row) for row in rows), default=1)
            old_rows, old_columns = dimensions(old)
            new_rows, new_columns = dimensions(new)
            if old_columns != new_columns:
                changes.append(f"Columns changed from {old_columns} to {new_columns}")
            if old_rows != new_rows:
                changes.append(f"Rows changed from {old_rows} to {new_rows}")
            if old.get("content") != new.get("content") and not changes:
                changes.append("Table cells changed")
        elif old.get("content") != new.get("content"):
            changes.append("Content changed")
        if any(old.get(field) != new.get(field) for field in ("topic", "section", "variant", "language", "images")):
            changes.append("Advanced details changed")
        if changes:
            section_changes.append({"index": index, "title": clean_text(new.get("section_title")) or f"Section {index + 1}", "changes": changes})
    return {"document": document_changes, "sections": section_changes}


def stage_change(domain: str, relative: str, source: Path) -> dict[str, Any]:
    target = pending_path(domain, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    info = {"status": "pending", "submitted_by": current_user()["username"], "submitted_at": now_iso()}
    target.with_name(target.name + ".meta.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    logger.info("CHANGE_STAGED domain=%s source=%s user=%s", domain, relative, current_user()["username"])
    return info


def workflow_change_summary(old_data: Any, new_data: Any) -> str:
    if old_data is None:
        return "New workflow created"
    changes = []
    if isinstance(old_data, dict) and isinstance(new_data, dict):
        if clean_text(old_data.get("title")) != clean_text(new_data.get("title")):
            changes.append("workflow name changed")
        old_steps = len(old_data.get("steps", [])) if isinstance(old_data.get("steps"), list) else None
        new_steps = len(new_data.get("steps", [])) if isinstance(new_data.get("steps"), list) else None
        if old_steps != new_steps:
            changes.append(f"entries changed from {old_steps or 0} to {new_steps or 0}")
    return ", ".join(changes).capitalize() if changes else "Workflow content updated"


def save_workflow_version(relative: str, source: Path, actor: str, action: str, summary: str, submitted_by: str | None = None) -> dict[str, Any]:
    source_key = hashlib.sha256(relative.encode()).hexdigest()[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    folder = VERSIONS_ROOT / "steps" / source_key / stamp
    folder.mkdir(parents=True, exist_ok=False)
    version_number = len(list((VERSIONS_ROOT / "steps" / source_key).glob("*/meta.json"))) + 1
    shutil.copy2(source, folder / "source.json")
    try:
        snapshot_data = json.loads(source.read_text(encoding="utf-8-sig"))
        image_names: set[str] = set()
        def collect_snapshot_images(value: Any) -> None:
            if isinstance(value, dict):
                for name in re.split(r"\s*,\s*", clean_text(value.get("image"))):
                    if name:
                        image_names.add(name)
                for child in value.values():
                    collect_snapshot_images(child)
            elif isinstance(value, list):
                for child in value:
                    collect_snapshot_images(child)
        collect_snapshot_images(snapshot_data)
        for name in image_names:
            image_relative = (Path(relative).parent / name).as_posix()
            image_source = (ACTIVE_STEPS_ROOT / image_relative.replace("/", os.sep)).resolve()
            if image_source.is_file() and (image_source == ACTIVE_STEPS_ROOT or ACTIVE_STEPS_ROOT in image_source.parents):
                image_target = folder / "assets" / image_relative.replace("/", os.sep)
                image_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(image_source, image_target)
    except (OSError, json.JSONDecodeError):
        logger.exception("WORKFLOW_VERSION_IMAGE_SNAPSHOT_FAILED source=%s", relative)
    meta = {"id": f"{source_key}/{stamp}", "domain": "steps", "version": version_number, "source_path": relative, "action": action, "summary": summary, "submitted_by": submitted_by or actor, "approved_by": actor, "created_at": now_iso()}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def save_retail_version(relative: str, data: dict[str, Any], actor: str, action: str, summary: str, submitted_by: str | None = None) -> dict[str, Any]:
    source_key = hashlib.sha256(relative.encode()).hexdigest()[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    source_root = VERSIONS_ROOT / "retail" / source_key
    folder = source_root / stamp
    folder.mkdir(parents=True, exist_ok=False)
    version_number = len(list(source_root.glob("*/meta.json"))) + 1
    (folder / "source.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {"id": f"{source_key}/{stamp}", "domain": "retail", "version": version_number, "source_path": relative, "action": action, "summary": summary, "submitted_by": submitted_by or actor, "approved_by": actor, "created_at": now_iso()}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def retail_change_summary(old_data: dict[str, Any] | None, new_data: dict[str, Any]) -> str:
    if not old_data:
        return "New retail extraction approved"
    old_sections = old_data.get("sections", [])
    new_sections = new_data.get("sections", [])
    changes = []
    if len(old_sections) != len(new_sections):
        changes.append(f"sections changed from {len(old_sections)} to {len(new_sections)}")
    for index, (old, new) in enumerate(zip(old_sections, new_sections)):
        if old.get("variant") != "table" or new.get("variant") != "table":
            continue
        def dimensions(section):
            stored = section.get("table_rows")
            rows = stored if isinstance(stored, list) and stored else [line.split("|") for line in str(section.get("content", "")).splitlines()]
            return len(rows), max((len(row) for row in rows), default=1)
        old_rows, old_columns = dimensions(old)
        new_rows, new_columns = dimensions(new)
        title = clean_text(new.get("section_title") or old.get("section_title") or f"Table {index + 1}").split(">")[-1].strip()
        if old_columns != new_columns:
            changes.append(f"{title}: columns changed from {old_columns} to {new_columns}")
        if old_rows != new_rows:
            changes.append(f"{title}: rows changed from {old_rows} to {new_rows}")
    return "; ".join(changes[:4]) if changes else "Retail content updated"


def version_folder(version_id: str) -> Path:
    root = (VERSIONS_ROOT / "steps").resolve()
    target = (root / version_id.replace("/", os.sep)).resolve()
    if root not in target.parents:
        abort(400, "Invalid version")
    return target


def retail_version_folder(version_id: str) -> Path:
    root = (VERSIONS_ROOT / "retail").resolve()
    target = (root / version_id.replace("/", os.sep)).resolve()
    if root not in target.parents:
        abort(400, "Invalid version")
    return target


def previous_version(domain: str, meta: dict[str, Any]) -> tuple[dict[str, Any] | None, Any]:
    source_key = str(meta.get("id", "")).split("/", 1)[0]
    candidates = []
    for meta_path in (VERSIONS_ROOT / domain / source_key).glob("*/meta.json"):
        try:
            candidate = json.loads(meta_path.read_text(encoding="utf-8"))
            if int(candidate.get("version", 0)) < int(meta.get("version", 0)):
                candidates.append((candidate, meta_path.parent / "source.json"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    if not candidates:
        return None, None
    previous_meta, source = max(candidates, key=lambda item: int(item[0].get("version", 0)))
    return previous_meta, json.loads(source.read_text(encoding="utf-8-sig"))


def can_view_history_version(meta: dict[str, Any]) -> bool:
    user = current_user()
    if user and user["admin"]:
        return True
    username = str(user["username"] if user else "").lower()
    return username in {str(meta.get("submitted_by", "")).lower(), str(meta.get("approved_by", "")).lower()}


def is_own_history_version(meta: dict[str, Any]) -> bool:
    """True only when the current user actually submitted or approved this version themselves.

    Unlike can_view_history_version, this ignores admin status: an admin restoring someone
    else's work is not "undoing a step they did", so that case still goes through approval.
    """
    user = current_user()
    username = str(user["username"] if user else "").lower()
    return bool(username) and username in {str(meta.get("submitted_by", "")).lower(), str(meta.get("approved_by", "")).lower()}


def history_action_available(meta: dict[str, Any]) -> bool:
    """History remains visible forever, but changes can only be reversed for 48 hours."""
    try:
        created_at = datetime.fromisoformat(str(meta.get("created_at", "")).replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds()
        return 0 <= age_seconds <= HISTORY_ACTION_WINDOW_HOURS * 60 * 60
    except (TypeError, ValueError):
        return False


def add_history_action_state(meta: dict[str, Any]) -> None:
    meta["action_available"] = history_action_available(meta)
    meta["action_window_hours"] = HISTORY_ACTION_WINDOW_HOURS
    meta["undo_kind"] = history_undo_kind(meta)


def source_version_metadata(domain: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
    source_key = str(meta.get("id", "")).split("/", 1)[0]
    versions = []
    for meta_path in (VERSIONS_ROOT / domain / source_key).glob("*/meta.json"):
        try:
            versions.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return versions


def history_undo_kind(meta: dict[str, Any]) -> str:
    """A latest first approval is revoked; every later action is a version rollback."""
    domain = clean_text(meta.get("domain"))
    versions = source_version_metadata(domain, meta) if domain in DOMAINS else []
    current_number = int(meta.get("version", 0))
    latest_number = max((int(item.get("version", 0)) for item in versions), default=current_number)
    earlier_business_approval = any(
        int(item.get("version", 0)) < current_number
        and item.get("action") in {"approved", "restored"}
        and clean_text(item.get("approved_by")).lower() not in {"", "system"}
        for item in versions
    )
    if meta.get("action") == "approved" and current_number == latest_number and not earlier_business_approval:
        return "revoke_approval"
    return "restore_version"


def publish_pending(domain: str, relative: str) -> Path:
    """Publish one approved change to the live data source."""
    root, live = safe_target(domain, relative)
    staged = pending_path(domain, relative)
    if not staged.is_file():
        abort(404, "No pending change for this source")
    # Images uploaded from the step editor remain private until this workflow
    # is approved. Publish only the files referenced by the approved JSON.
    if domain == "steps" and staged.suffix.lower() == ".json":
        data = json.loads(staged.read_text(encoding="utf-8-sig"))
        image_names: set[str] = set()

        def find_images(value: Any):
            if isinstance(value, dict):
                for name in re.split(r"\s*,\s*", str(value.get("image") or "")):
                    if name:
                        image_names.add(name)
                for child in value.values():
                    find_images(child)
            elif isinstance(value, list):
                for child in value:
                    find_images(child)

        find_images(data)
        parent = Path(relative).parent
        for name in image_names:
            asset_relative = (parent / name).as_posix()
            staged_asset = pending_path(domain, asset_relative)
            if staged_asset.is_file():
                _, live_asset = safe_target(domain, asset_relative)
                live_asset.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged_asset, live_asset)
                staged_asset.unlink()
    old_data = json.loads(live.read_text(encoding="utf-8-sig")) if domain == "steps" and live.is_file() and live.suffix.lower() == ".json" else None
    submission = pending_info(domain, relative) or {}
    if live.exists():
        backup = HISTORY_ROOT / domain / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") / live.relative_to(root)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live, backup)
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(staged, live)
    staged.unlink()
    staged.with_name(staged.name + ".meta.json").unlink(missing_ok=True)
    if domain == "steps" and live.suffix.lower() == ".json":
        new_data = json.loads(live.read_text(encoding="utf-8-sig"))
        action = "restored" if submission.get("restored_from") else "approved"
        summary = f"Restored from version {submission['restored_from']}" if submission.get("restored_from") else workflow_change_summary(old_data, new_data)
        save_workflow_version(relative, live, current_user()["username"], action, summary, submission.get("submitted_by"))
    return live


def require_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user():
            return jsonify(error="Authentication required"), 401
        return fn(*args, **kwargs)
    return wrapped


def require_domain(fn):
    @wraps(fn)
    @require_auth
    def wrapped(domain, *args, **kwargs):
        if domain not in current_user()["roles"]:
            return jsonify(error="You do not have access to this data area"), 403
        return fn(domain, *args, **kwargs)
    return wrapped


def ldap_login(username: str, password: str) -> bool:
    if DEV_MODE:
        return bool(password)
    host = os.getenv("LDAP_HOST")
    if not host:
        expected = DEV_PASSWORDS.get(username.lower())
        return (not AUTH_ENABLED) or (expected is not None and secrets.compare_digest(str(expected), password))
    try:
        from ldap3 import Connection, Server, SUBTREE, Tls
        server = Server(host, port=int(os.getenv("LDAP_PORT", "636")), use_ssl=os.getenv("LDAP_USE_SSL", "true").lower() == "true", tls=Tls(validate=2))
        directory = Connection(server, os.environ["LDAP_BIND_DN"], os.environ["LDAP_BIND_PASSWORD"], auto_bind=True)
        escaped = re.sub(r"([\\()*\x00])", lambda m: "\\%02x" % ord(m.group(1)), username)
        filt = os.getenv("LDAP_USER_FILTER", "(sAMAccountName={{username}})").replace("{{username}}", escaped)
        directory.search(os.environ["LDAP_BASE_DN"], filt, SUBTREE, attributes=["distinguishedName", "memberOf"])
        if len(directory.entries) != 1:
            return False
        entry = directory.entries[0]
        group = os.getenv("LDAP_GROUP_DN")
        if group and group.lower() not in [str(x).lower() for x in entry.memberOf.values]:
            return False
        return Connection(server, str(entry.entry_dn), password, auto_bind=True).bound
    except Exception:
        return False


def list_files(domain: str) -> list[dict[str, Any]]:
    root, _ = safe_target(domain)
    items = []
    topic_labels = topic_label_map() if domain == "steps" else {}
    for path in root.rglob("*") if root.exists() else []:
        if not path.is_file() or path.suffix.lower() not in DOMAINS[domain]["extensions"]:
            continue
        # Retail PDFs live at Data root except the Steps training-material copy.
        if domain == "retail" and "Steps" in path.relative_to(root).parts:
            continue
        stat = path.stat()
        relative = path.relative_to(root).as_posix()
        pending = (retail_pending_info(relative) or pending_info(domain, relative)) if domain == "retail" else pending_info(domain, relative)
        visible = pending_path(domain, relative) if pending else path
        title = path.parent.name if domain == "steps" and path.stem.lower() == "workflow" else path.stem
        if domain == "retail" and path.suffix.lower() == ".product":
            product_data = extracted_path(relative, pending=bool(pending))
            if not product_data.is_file():
                product_data = extracted_path(relative)
            try:
                title = clean_text(json.loads(product_data.read_text(encoding="utf-8-sig")).get("title")) or title
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
        if domain == "steps" and path.stem.lower().endswith("_overview"):
            title = "Overview"
        validation = None
        login_stage = None
        if path.suffix.lower() == ".json":
            try:
                data = json.loads(visible.read_text(encoding="utf-8-sig"))
                title = (clean_text(data.get("title")) or title) if isinstance(data, dict) else title
                if domain == "steps" and isinstance(data, dict) and isinstance(data.get("steps"), list):
                    validation = workflow_validation_status(data, topic_label_for_path(relative, topic_labels))
                    login_stage = validation["login_stage"]
            except (OSError, json.JSONDecodeError):
                pass
        items.append({"path": relative, "name": path.name, "title": title, "size": stat.st_size, "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), "pending": pending, "validation": validation, "login_stage": login_stage})
    staging = PENDING_ROOT / domain
    if staging.exists():
        known = {item["path"] for item in items}
        for path in staging.rglob("*"):
            if not path.is_file() or path.name.endswith(".meta.json") or path.suffix.lower() not in DOMAINS[domain]["extensions"]:
                continue
            relative = path.relative_to(staging).as_posix()
            if relative not in known:
                stat = path.stat()
                title = path.parent.name if domain == "steps" and path.stem.lower() == "workflow" else path.stem
                if domain == "steps" and path.stem.lower().endswith("_overview"):
                    title = "Overview"
                validation = None
                login_stage = None
                if path.suffix.lower() == ".json":
                    try:
                        data = json.loads(path.read_text(encoding="utf-8-sig"))
                        title = (clean_text(data.get("title")) or title) if isinstance(data, dict) else title
                        if domain == "steps" and isinstance(data, dict) and isinstance(data.get("steps"), list):
                            validation = workflow_validation_status(data, topic_label_for_path(relative, topic_labels))
                            login_stage = validation["login_stage"]
                    except (OSError, json.JSONDecodeError):
                        pass
                items.append({"path": relative, "name": path.name, "title": title, "size": stat.st_size, "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), "pending": pending_info(domain, relative), "new": True, "validation": validation, "login_stage": login_stage})
    return sorted(items, key=lambda x: x["path"].lower())


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def workflow_required_review_fields(data: dict[str, Any]) -> list[str]:
    """Fields a business approver must explicitly review before publishing."""
    required = ["topic", "title"]

    def add_step_fields(steps: list[Any], prefix: str):
        for index, step in enumerate(steps):
            required.extend([f"{prefix}.{index}.label", f"{prefix}.{index}.text", f"{prefix}.{index}.image"])
            if isinstance(step, dict) and isinstance(step.get("substeps"), list):
                add_step_fields(step["substeps"], f"{prefix}.{index}.substeps")

    add_step_fields(data.get("steps", []) if isinstance(data.get("steps"), list) else [], "steps")
    return required


def workflow_validation_status(data: dict[str, Any], topic_stage: str | None = None) -> dict[str, Any]:
    validation = data.get("validation") if isinstance(data.get("validation"), dict) else {}
    stored_fields = validation.get("reviewed_fields", [])
    reviewed = {str(value) for value in stored_fields if isinstance(value, str)} if isinstance(stored_fields, list) else set()
    required = workflow_required_review_fields(data)
    missing = [field for field in required if field not in reviewed]
    stage = clean_text(topic_stage or data.get("login_stage")).lower()
    complete = not missing
    return {"complete": complete, "reviewed": len(required) - len(missing), "total": len(required), "missing": missing, "login_stage": stage or None, "validated_by": validation.get("validated_by"), "validated_at": validation.get("validated_at")}


def json_chunks(data: Any, source: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    topic = clean_text(data.get("topic", "general")) if isinstance(data, dict) else "general"
    title = clean_text(data.get("title", data.get("section", Path(source).stem))) if isinstance(data, dict) else Path(source).stem

    def visit(value: Any, trail: list[str], images: list[str] | None = None):
        if isinstance(value, dict):
            text = clean_text(value.get("text", ""))
            label = value.get("step", value.get("Note", value.get("option", "")))
            own_images = images or []
            image_value = value.get("image")
            if image_value:
                own_images = [x.strip() for x in re.split(r"\s*,\s*", str(image_value)) if x.strip()]
            if text:
                heading = " > ".join([title, *trail, str(label) if label != "" else ""]).strip(" >")
                chunks.append({"topic": topic[:64], "login_stage": clean_text(data.get("login_stage")) if isinstance(data, dict) else "", "section": clean_text(trail[-1] if trail else "workflow")[:64], "variant": "note" if "Note" in value else "step", "section_title": heading[:512], "content": text, "language": "ar" if len(re.findall(r"[\u0600-\u06ff]", text)) > len(re.findall(r"[A-Za-z]", text)) else "en", "images": own_images})
            for key, child in value.items():
                if key not in {"text", "image", "topic", "title", "step", "Note", "login_stage", "validation"}:
                    visit(child, trail + ([str(key)] if key not in {"steps", "substeps"} else []), own_images)
        elif isinstance(value, list):
            for child in value:
                visit(child, trail, images)
        elif clean_text(value):
            text = clean_text(value)
            chunks.append({"topic": topic[:64], "section": clean_text(trail[-1] if trail else "reference")[:64], "variant": "reference", "section_title": " > ".join([title, *trail])[:512], "content": text, "language": "ar" if re.search(r"[\u0600-\u06ff]", text) else "en", "images": []})

    visit(data, [])
    return chunks


def spreadsheet_chunks(path: Path) -> list[dict[str, Any]]:
    import pandas as pd
    sheets = {"csv": pd.read_csv(path)} if path.suffix.lower() == ".csv" else pd.read_excel(path, sheet_name=None)
    result = []
    for sheet, frame in sheets.items():
        frame = frame.ffill().fillna("")
        for index, row in frame.iterrows():
            content = "\n".join(f"{col}: {value}" for col, value in row.items() if clean_text(value))
            if content:
                result.append({"topic": "atm_branches", "section": clean_text(sheet)[:64], "variant": "row", "section_title": f"{path.name} > {sheet} > Row {index + 1}", "content": content[:65000], "language": "ar" if re.search(r"[\u0600-\u06ff]", content) else "en", "images": []})
    return result


def pdf_chunks(path: Path) -> list[dict[str, Any]]:
    """Read a retail PDF into editable, layout-aware sections (no embeddings)."""
    import pymupdf
    lines = []
    tables = []
    with pymupdf.open(path) as doc:
        for page_number, page in enumerate(doc, 1):
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    spans = [span for span in line.get("spans", []) if clean_text(span.get("text"))]
                    if not spans:
                        continue
                    text = clean_text(" ".join(span.get("text", "") for span in spans))
                    lines.append({"page": page_number, "text": text, "size": max(float(span.get("size", 0)) for span in spans), "bold": any(int(span.get("flags", 0)) & 16 for span in spans)})
            try:
                for number, table in enumerate(page.find_tables().tables, 1):
                    rows = [" | ".join(clean_text(cell) for cell in row) for row in table.extract() if any(clean_text(cell) for cell in row)]
                    if len(rows) >= 2:
                        tables.append({"page": page_number, "title": f"Page {page_number} - Table {number}", "content": "\n".join(rows)})
            except Exception as exc:
                logger.warning("PDF_TABLE_READ_FAILED source=%s page=%d error=%s", path.name, page_number, exc)
    if not lines:
        return []
    sizes = sorted({round(line["size"], 1) for line in lines}, reverse=True)
    heading_sizes = set(sizes[:4])
    headings: list[str] = []
    result: list[dict[str, Any]] = []
    buffer: list[str] = []
    page = 1

    def flush():
        nonlocal buffer
        content = clean_text(" ".join(buffer))
        buffer = []
        if not content:
            return
        title = " > ".join([path.stem, *headings]) or path.stem
        result.append({"topic": clean_text(headings[0] if headings else path.stem)[:64], "section": clean_text(headings[-1] if headings else f"page_{page}")[:64], "variant": "content", "section_title": title[:512], "content": content[:65000], "language": "ar" if len(re.findall(r"[\u0600-\u06ff]", content)) > len(re.findall(r"[A-Za-z]", content)) else "en", "images": [], "page": page})

    for line in lines:
        text = line["text"]
        is_heading = round(line["size"], 1) in heading_sizes and len(text.split()) <= 10 and not text.endswith((".", ","))
        if is_heading:
            flush()
            level = sizes.index(round(line["size"], 1)) if round(line["size"], 1) in sizes else 3
            headings[level:] = [text]
        else:
            page = line["page"]
            buffer.append(text)
    flush()
    for table in tables:
        content = table["content"]
        result.append({"topic": path.stem[:64], "section": "table", "variant": "table", "section_title": f"{path.stem} > {table['title']}", "content": content[:65000], "language": "ar" if re.search(r"[\u0600-\u06ff]", content) else "en", "images": [], "page": table["page"]})
    return result


def extract_retail_pdf(path: Path) -> dict[str, Any]:
    """Extract a PDF into the canonical structured Retail JSON document."""
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise ValueError("A readable PDF file is required")
    return normalize_retail_data({"title": path.stem, "source": {"type": "pdf", "filename": path.name}, "sections": pdf_chunks(path)})


def ensure_retail_structured_files() -> tuple[int, int]:
    """Create canonical sidecar JSON for Retail PDFs that do not have one yet."""
    pdfs = sorted(RETAIL_ROOT.rglob("*.pdf")) if RETAIL_ROOT.exists() else []
    missing = [pdf for pdf in pdfs if not pdf.with_suffix(".structured.json").is_file()]
    if not missing:
        logger.info("RETAIL_STRUCTURE_CHECK sources=%d missing=0", len(pdfs))
        return 0, 0

    logger.info("RETAIL_STRUCTURE_CHECK sources=%d missing=%d action=extract", len(pdfs), len(missing))
    failures = 0
    for pdf in missing:
        relative = pdf.relative_to(RETAIL_ROOT).as_posix()
        destination = pdf.with_suffix(".structured.json")
        try:
            document = extract_retail_pdf(pdf)
            errors = validate_document(document)
            if errors:
                raise ValueError("; ".join(errors[:10]))
            destination.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("RETAIL_STRUCTURE_CREATED source=%s sections=%d", relative, len(document["sections"]))
        except Exception:
            failures += 1
            logger.exception("RETAIL_STRUCTURE_CREATE_FAILED source=%s", relative)
    return len(missing) - failures, failures


def make_chunks(domain: str, path: Path, relative: str) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        chunks = json_chunks(json.loads(path.read_text(encoding="utf-8-sig")), relative)
    elif path.suffix.lower() in {".xlsx", ".xls", ".csv"}:
        chunks = spreadsheet_chunks(path)
    else:
        chunks = pdf_chunks(path)
    stamp = now_iso()
    for index, chunk in enumerate(chunks):
        chunk.update({"chunk_id": hashlib.sha256(f"{domain}:{relative}:{index}:{chunk['content']}".encode()).hexdigest(), "source_path": relative, "domain": domain, "chunk_index": index, "chunked_at": stamp})
    return chunks


def import_chunks(domain: str, path: Path, relative: str) -> list[dict[str, Any]]:
    """Produce the exact chunk payload that is audited before Milvus receives it."""
    if domain != "retail":
        return make_chunks(domain, path, relative)
    structured = structured_retail_path(relative)
    approved = extracted_path(relative)
    if structured.is_file():
        data = normalize_retail_data(json.loads(structured.read_text(encoding="utf-8-sig")))
    elif approved.is_file():
        data = normalize_retail_data(json.loads(approved.read_text(encoding="utf-8-sig")))
    else:
        return make_chunks(domain, path, relative)
    return retail_chunks_from_data(data, domain, relative)


def retail_chunks_from_data(data: dict[str, Any], domain: str, relative: str) -> list[dict[str, Any]]:
    """Create Retail chunks from an approved or still-pending business document."""
    data = normalize_retail_data(data)
    chunks = [dict(section) for section in data.get("sections", []) if isinstance(section, dict)]
    stamp = now_iso()
    for index, chunk in enumerate(chunks):
        content = retail_blocks_text(chunk.get("content_blocks")) or clean_text(chunk.get("content"))
        chunk.update({"chunk_id": hashlib.sha256(f"{domain}:{relative}:{index}:{content}".encode()).hexdigest(), "content": content, "source_path": relative, "domain": domain, "chunk_index": index, "chunked_at": stamp})
        chunk.pop("content_blocks", None)
        chunk.pop("display_title", None)
        chunk.setdefault("section_title", f"{data.get('title', Path(relative).stem)} > Section {index + 1}")
        chunk.setdefault("topic", clean_text(data.get("title", Path(relative).stem))[:64])
        chunk.setdefault("section", f"section_{index + 1}")
        chunk.setdefault("variant", "content")
        chunk.setdefault("language", "ar" if re.search(r"[\u0600-\u06ff]", content) else "en")
        chunk.setdefault("images", [])
    return chunks


def milvus_candidate(domain: str, relative: str, requested_version: str = "approved") -> tuple[Path, list[dict[str, Any]], str]:
    """Resolve an admin-selected approved or pending source without publishing it."""
    if domain not in DOMAINS or requested_version not in {"approved", "pending"}:
        raise ValueError("Invalid Milvus source selection")
    root, live = safe_target(domain, relative)
    if requested_version == "pending":
        if domain == "retail":
            pending_data = extracted_path(relative, pending=True)
            if pending_data.is_file():
                data = json.loads(pending_data.read_text(encoding="utf-8-sig"))
                source = pending_path(domain, relative) if pending_path(domain, relative).is_file() else live
                return source, retail_chunks_from_data(data, domain, relative), "pending"
        staged = pending_path(domain, relative)
        if staged.is_file():
            if domain == "steps":
                data = json.loads(staged.read_text(encoding="utf-8-sig"))
                return staged, build_rows_for_source(relative=relative, data=data, steps_root=ACTIVE_STEPS_ROOT), "pending"
            return staged, make_chunks(domain, staged, relative), "pending"
        raise FileNotFoundError("Pending source not found")
    if not live.is_file():
        raise FileNotFoundError("Approved source not found")
    if domain == "steps":
        data = json.loads(live.read_text(encoding="utf-8-sig"))
        return live, build_rows_for_source(relative=relative, data=data, steps_root=ACTIVE_STEPS_ROOT), "approved"
    return live, import_chunks(domain, live, relative), "approved"


def explicitly_approved_sources(domain: str) -> set[str]:
    """Return sources whose latest recorded business action is an explicit approval."""
    root = VERSIONS_ROOT / domain
    latest: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return set()
    for meta_path in root.rglob("meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = clean_text(meta.get("source_path")).replace("\\", "/")
        if not source:
            continue
        if source not in latest or clean_text(meta.get("created_at")) > clean_text(latest[source].get("created_at")):
            latest[source] = meta
    return {
        source for source, meta in latest.items()
        if meta.get("action") in {"approved", "restored"} and clean_text(meta.get("approved_by")).lower() not in {"", "system"}
    }


def milvus_chunk_inventory(user: dict[str, Any]) -> list[dict[str, Any]]:
    """List selectable chunk sets, preferring pending content when it exists."""
    inventory = []
    domains = list(DOMAINS) if user.get("milvus_admin") else user.get("roles", [])
    for domain in domains:
        if domain not in DOMAINS:
            continue
        business_approved = explicitly_approved_sources(domain)
        for item in list_files(domain):
            version = "pending" if item.get("pending") else "approved"
            is_approved = item["path"] in business_approved and version != "pending"
            try:
                _, chunks, resolved = milvus_candidate(domain, item["path"], version)
                inventory.append({
                    "domain": domain, "path": item["path"], "title": item.get("title") or item["path"],
                    "version": resolved, "approved": is_approved, "chunks": len(chunks),
                    "collection": resolve_collection(domain, item["path"]),
                })
            except Exception as exc:
                inventory.append({
                    "domain": domain, "path": item["path"], "title": item.get("title") or item["path"],
                    "version": version, "approved": is_approved, "chunks": 0,
                    "collection": resolve_collection(domain, item["path"]), "error": clean_text(exc),
                })
    return inventory


def save_audit(domain: str, relative: str, chunks: list[dict[str, Any]], user: str) -> Path:
    """Stage the latest human-browsable chunk set before Milvus ingestion."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    normalized = str(relative).replace("\\", "/").strip("/")
    source_parts = [part for part in Path(normalized).with_suffix("").parts if part not in {".", ".."}]
    safe_parts = [re.sub(r"[^\w .()-]+", "_", part, flags=re.UNICODE).strip(" .") or "unnamed" for part in source_parts]
    if domain == "retail":
        # Normalize the source name before matching it. In particular, legacy
        # root files such as "Deposits .pdf" contain a space before the suffix.
        product_area = (safe_parts[0] if safe_parts else Path(normalized).stem).strip(" .").lower()
        if product_area not in RETAIL_COLLECTIONS:
            product_area = "unclassified"
        if not safe_parts or safe_parts[0].lower() != product_area:
            safe_parts.insert(0, product_area)
    folder = AUDIT_ROOT.joinpath(domain, *safe_parts)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dataset": f"{domain}/{normalized}",
        "version": stamp,
        "status": "ready_for_ingestion",
        "source_path": relative,
        "domain": domain,
        "chunked_at": now_iso(),
        "chunked_by": user,
        "chunk_count": len(chunks),
        "milvus_host": MILVUS_HOST,
        "collection": resolve_collection(domain, relative),
        "files": {"chunks": "chunks.jsonl", "readable_copy": "chunks.json"},
    }
    payload = {"audit": manifest, "chunks": chunks}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "chunks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (folder / "chunks.jsonl").open("w", encoding="utf-8") as stream:
        for chunk in chunks:
            stream.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    logger.info("AUDIT_SAVED domain=%s source=%s chunks=%d user=%s directory=%s", domain, relative, len(chunks), user, folder)
    return folder


MILVUS_EVENTS_PATH = (BASE_DIR / ".studio" / "milvus-events.jsonl").resolve()
SYNC_JOBS_PATH = (BASE_DIR / ".studio" / "sync-jobs.sqlite3").resolve()
SYNC_JOB_MAX_ATTEMPTS = 3
_SYNC_WORKER_STARTED = False
_SYNC_WORKER_LOCK = threading.Lock()


def record_milvus_event(domain: str, relative: str, user: str, sync: dict[str, Any], chunk_count: int, action: str) -> None:
    """Persist an admin-visible record of every attempted Milvus synchronization."""
    MILVUS_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    collection = sync.get("collection")
    if not collection:
        collection = resolve_collection(domain, relative)
    event = {"at": now_iso(), "domain": domain, "source_path": relative, "user": user, "action": action, "status": sync.get("status", "unknown"), "collection": collection, "chunks": chunk_count}
    if sync.get("error"):
        event["error"] = clean_text(sync["error"])[:1000]
    with MILVUS_EVENTS_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def milvus_events(limit: int = 500) -> list[dict[str, Any]]:
    if not MILVUS_EVENTS_PATH.is_file():
        return []
    lines = MILVUS_EVENTS_PATH.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 2000)):]
    events = []
    for line in reversed(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def sync_jobs_connection() -> sqlite3.Connection:
    SYNC_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(SYNC_JOBS_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""CREATE TABLE IF NOT EXISTS sync_jobs (
        id TEXT PRIMARY KEY, domain TEXT NOT NULL, source_path TEXT NOT NULL,
        version_id TEXT NOT NULL, requested_by TEXT NOT NULL, status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, next_attempt_at REAL NOT NULL DEFAULT 0,
        chunks INTEGER, collection_name TEXT, error TEXT
    )""")
    connection.commit()
    return connection


def latest_version_id(domain: str, relative: str) -> str:
    source_key = hashlib.sha256(relative.encode()).hexdigest()[:12]
    candidates = list((VERSIONS_ROOT / domain / source_key).glob("*/meta.json"))
    if not candidates:
        raise FileNotFoundError("Approved version snapshot was not created")
    latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    return json.loads(latest.read_text(encoding="utf-8"))["id"]


def enqueue_approved_sync(domain: str, relative: str, actor: str) -> dict[str, Any]:
    job_id = secrets.token_hex(12)
    version_id = latest_version_id(domain, relative)
    stamp = now_iso()
    with closing(sync_jobs_connection()) as connection, connection:
        connection.execute(
            "INSERT INTO sync_jobs (id, domain, source_path, version_id, requested_by, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
            (job_id, domain, relative, version_id, actor, stamp, stamp),
        )
    sync = {"status": "queued", "host": MILVUS_HOST, "collection": resolve_collection(domain, relative), "job_id": job_id}
    record_milvus_event(domain, relative, actor, sync, 0, "approved_sync_queued")
    logger.info("SYNC_JOB_QUEUED id=%s domain=%s source=%s version=%s user=%s", job_id, domain, relative, version_id, actor)
    start_sync_worker()
    return sync


def process_sync_job(job: sqlite3.Row) -> tuple[list[dict[str, Any]], Path, dict[str, Any]]:
    domain, relative, version_id, actor = job["domain"], job["source_path"], job["version_id"], job["requested_by"]
    folder = version_folder(version_id) if domain == "steps" else retail_version_folder(version_id)
    snapshot = folder / "source.json"
    if not snapshot.is_file():
        raise FileNotFoundError(f"Approved snapshot is missing for job {job['id']}")
    if domain == "steps":
        chunks = import_chunks(domain, snapshot, relative)
        audit = save_audit(domain, relative, chunks, actor)
        sync = sync_steps_milvus(relative, snapshot, folder / "assets")
        record_milvus_event(domain, relative, actor, sync, len(chunks), "background_approved_sync")
        return chunks, audit, sync
    data = json.loads(snapshot.read_text(encoding="utf-8-sig"))
    chunks = retail_chunks_from_data(data, domain, relative)
    audit = save_audit(domain, relative, chunks, actor)
    sync = sync_retail_milvus(relative, chunks)
    record_milvus_event(domain, relative, actor, sync, len(chunks), "background_approved_sync")
    return chunks, audit, sync


def run_sync_worker() -> None:
    logger.info("SYNC_WORKER_STARTED database=%s", SYNC_JOBS_PATH)
    while True:
        job = None
        try:
            with closing(sync_jobs_connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                job = connection.execute(
                    "SELECT * FROM sync_jobs WHERE (status IN ('queued', 'retrying') OR (status='embedding' AND next_attempt_at <= ?)) AND next_attempt_at <= ? ORDER BY created_at LIMIT 1",
                    (time.time(), time.time()),
                ).fetchone()
                if job:
                    connection.execute("UPDATE sync_jobs SET status='embedding', attempts=attempts+1, next_attempt_at=?, updated_at=? WHERE id=?", (time.time() + 900, now_iso(), job["id"]))
            if not job:
                time.sleep(1)
                continue
            chunks, _, sync = process_sync_job(job)
            if sync.get("status") != "synced":
                raise RuntimeError(sync.get("error") or "Connected-system synchronization failed")
            with closing(sync_jobs_connection()) as connection, connection:
                connection.execute(
                    "UPDATE sync_jobs SET status='synced', chunks=?, collection_name=?, error=NULL, updated_at=? WHERE id=?",
                    (len(chunks), sync.get("collection"), now_iso(), job["id"]),
                )
            logger.info("SYNC_JOB_COMPLETE id=%s domain=%s source=%s chunks=%d", job["id"], job["domain"], job["source_path"], len(chunks))
        except Exception as exc:
            logger.exception("SYNC_JOB_FAILED id=%s error=%s", job["id"] if job else "worker", exc)
            if job:
                attempts = int(job["attempts"]) + 1
                retrying = attempts < SYNC_JOB_MAX_ATTEMPTS
                with closing(sync_jobs_connection()) as connection, connection:
                    connection.execute(
                        "UPDATE sync_jobs SET status=?, error=?, next_attempt_at=?, updated_at=? WHERE id=?",
                        ("retrying" if retrying else "failed", clean_text(exc)[:1000], time.time() + (30 * attempts if retrying else 0), now_iso(), job["id"]),
                    )
            time.sleep(1)


def start_sync_worker() -> None:
    global _SYNC_WORKER_STARTED
    with _SYNC_WORKER_LOCK:
        if _SYNC_WORKER_STARTED:
            return
        sync_jobs_connection().close()
        threading.Thread(target=run_sync_worker, name="milvus-sync-worker", daemon=True).start()
        _SYNC_WORKER_STARTED = True


def recent_sync_jobs(limit: int = 100) -> list[dict[str, Any]]:
    with closing(sync_jobs_connection()) as connection:
        rows = connection.execute("SELECT * FROM sync_jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    return [dict(row) for row in rows]


def audit_and_sync_approved_data(domain: str, path: Path, relative: str, user: str, action: str = "approved_change") -> tuple[list[dict[str, Any]], Path, dict[str, Any]]:
    """Archive the approved chunk payload, then replace that source in Milvus."""
    chunks = import_chunks(domain, path, relative)
    logger.info("IMPORT_STARTED domain=%s source=%s user=%s chunks=%d", domain, relative, user, len(chunks))
    audit = save_audit(domain, relative, chunks, user)
    sync = sync_milvus(domain, relative, chunks, path)
    record_milvus_event(domain, relative, user, sync, len(chunks), action)
    return chunks, audit, sync


def finalize_staged_steps_workflow(relative: str, actor: str) -> tuple[bool, str | None, int]:
    """Validate and stamp a staged Steps workflow ahead of publishing it.

    On success, updates the staged file in place (login_stage + validation stamp) and
    returns (True, None, 200). On failure, returns (False, error_message, http_status)
    without touching the staged file - the caller decides what to do next (e.g. reject
    the request, or leave the change sitting in the approval queue).
    """
    staged_workflow = pending_path("steps", relative)
    if not staged_workflow.is_file():
        return False, "No pending workflow to validate", 404
    try:
        workflow_data = json.loads(staged_workflow.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return False, "The pending workflow is not valid JSON", 422
    if isinstance(workflow_data, dict) and isinstance(workflow_data.get("steps"), list):
        topic_stage = topic_label_for_path(relative)
        status = workflow_validation_status(workflow_data, topic_stage)
        if status["missing"]:
            return False, f"Review every workflow field before approval ({len(status['missing'])} still unchecked)", 422
        workflow_data.setdefault("validation", {})
        workflow_data["login_stage"] = status["login_stage"]
        workflow_data["validation"].update({"validated_by": actor, "validated_at": now_iso(), "complete": True})
        staged_workflow.write_text(json.dumps(workflow_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, None, 200


def publish_and_sync_generic(domain: str, relative: str, actor: str, defer_sync: bool = False) -> dict[str, Any]:
    """Publish an already-staged, already-finalized change and sync it to Milvus.

    Used for Steps workflows (after finalize_staged_steps_workflow), and as the fallback
    for any domain that doesn't have a domain-specific publish path.
    """
    live = publish_pending(domain, relative)
    if defer_sync:
        sync = enqueue_approved_sync(domain, relative, actor)
        return {"sync": sync, "message": "Workflow approved.", "status_code": 200, "chunks": 0, "audit": None}
    chunks, audit, sync = audit_and_sync_approved_data(domain, live, relative, actor)
    message = "Workflow approved and published to the AI knowledge base." if sync["status"] == "synced" else f"Workflow approved, but publishing to the AI knowledge base is {sync['status']}."
    return {"sync": sync, "message": message, "status_code": (200 if sync["status"] != "failed" else 202), "chunks": len(chunks), "audit": str(audit)}


def publish_and_sync_retail(relative: str, actor: str, defer_sync: bool = False) -> dict[str, Any] | None:
    """Publish an already-staged retail change (extracted sections + optional PDF) and sync it.

    Returns None if there is no pending retail extraction for this source - the caller
    should fall back to publish_and_sync_generic in that case, same as approve() always has.
    """
    retail_pending = extracted_path(relative, pending=True)
    if not (retail_pending and retail_pending.is_file()):
        return None
    data = json.loads(retail_pending.read_text(encoding="utf-8"))
    submission = retail_pending_info(relative) or {}
    previous_data = None
    staged_pdf = pending_path("retail", relative)
    if staged_pdf.is_file():
        retail_root, live_pdf = safe_target("retail", relative)
        if live_pdf.is_file():
            pdf_backup = HISTORY_ROOT / "retail" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") / live_pdf.relative_to(retail_root)
            pdf_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live_pdf, pdf_backup)
        live_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_pdf, live_pdf)
        staged_pdf.unlink()
        staged_pdf.with_name(staged_pdf.name + ".meta.json").unlink(missing_ok=True)
    approved = extracted_path(relative)
    if approved.is_file():
        previous_data = json.loads(approved.read_text(encoding="utf-8-sig"))
        backup = HISTORY_ROOT / "retail" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") / (relative + ".extracted.json")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(approved, backup)
    approved.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(retail_pending, approved)
    normalized = normalize_retail_data(data)
    structured = structured_retail_path(relative)
    structured.parent.mkdir(parents=True, exist_ok=True)
    structured.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    retail_pending.unlink()
    retail_pending.with_name(retail_pending.name + ".meta.json").unlink(missing_ok=True)
    retail_action = "restored" if submission.get("restored_from") else "approved"
    retail_summary = f"Restored from version {submission['restored_from']}" if submission.get("restored_from") else retail_change_summary(previous_data, data)
    save_retail_version(relative, data, actor, retail_action, retail_summary, submission.get("submitted_by"))
    if defer_sync:
        sync = enqueue_approved_sync("retail", relative, actor)
        return {"sync": sync, "message": "Retail data approved", "status_code": 200, "chunks": 0, "audit": None}
    _, approved_source = safe_target("retail", relative)
    chunks, audit, sync = audit_and_sync_approved_data("retail", approved_source, relative, actor)
    message = "Retail data approved"
    return {"sync": sync, "message": message, "status_code": (200 if sync["status"] != "failed" else 202), "chunks": len(chunks), "audit": str(audit)}


_EMBEDDING_MODEL: Any = None
_STEPS_PROCESSOR: Any = None
_STEPS_MODEL: Any = None

# workflow.json / tips.json / faq.json share one folder as their workflow_path, so a delete
# scoped to workflow_path alone would also wipe the other two files' rows. Scope by the
# content_types each file actually owns instead. Any other .json's workflow_path is the full
# file path (unique already), so it needs no extra scoping.
STEPS_SHARED_FOLDER_CONTENT_TYPES = {
    "workflow.json": ["workflow", "step", "substep", "option", "condition", "condition_step", "note"],
    "tips.json": ["tip"],
    "faq.json": ["faq"],
}


def embedding_model():
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _EMBEDDING_MODEL = SentenceTransformer(os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-large"), device=os.getenv("EMBEDDING_DEVICE", "cpu"))
    return _EMBEDDING_MODEL


def steps_embedding_backend():
    """Lazily load the SigLIP processor/model used for Steps text+image embeddings."""
    global _STEPS_PROCESSOR, _STEPS_MODEL
    if _STEPS_MODEL is None:
        from transformers import SiglipProcessor, SiglipModel
        device = os.getenv("EMBEDDING_DEVICE", "cpu")
        _STEPS_PROCESSOR = SiglipProcessor.from_pretrained(STEPS_MODEL_ID)
        _STEPS_MODEL = SiglipModel.from_pretrained(STEPS_MODEL_ID).to(device)
        _STEPS_MODEL.eval()
    return _STEPS_PROCESSOR, _STEPS_MODEL


def embed_steps_text(text: str) -> list[float]:
    import torch
    processor, model = steps_embedding_backend()
    inputs = processor(text=text, return_tensors="pt", padding=True, truncation=True).to(os.getenv("EMBEDDING_DEVICE", "cpu"))
    with torch.no_grad():
        output = model.get_text_features(**inputs)
    emb = output.pooler_output if hasattr(output, "pooler_output") else output
    emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.squeeze().cpu().tolist()


def embed_steps_image(path: Path) -> list[float]:
    import torch
    from PIL import Image
    processor, model = steps_embedding_backend()
    image = Image.open(path).convert("RGB")
    inputs = processor(images=image, return_tensors="pt").to(os.getenv("EMBEDDING_DEVICE", "cpu"))
    with torch.no_grad():
        output = model.get_image_features(**inputs)
    emb = output.pooler_output if hasattr(output, "pooler_output") else output
    emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.squeeze().cpu().tolist()


def milvus_client():
    from pymilvus import MilvusClient
    return MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}", token=os.getenv("MILVUS_TOKEN") or None, timeout=float(os.getenv("MILVUS_TIMEOUT_SECONDS", "5")))


def ensure_retail_collection(client, collection: str, dim: int) -> None:
    """Create a cards/loans/deposits-shaped collection if it doesn't exist yet. Never alters an existing one."""
    from pymilvus import DataType
    if client.has_collection(collection):
        return
    logger.warning("MILVUS_COLLECTION_CREATE collection=%s", collection)
    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field("topic", DataType.VARCHAR, max_length=32)
    schema.add_field("section", DataType.VARCHAR, max_length=32)
    schema.add_field("variant", DataType.VARCHAR, max_length=32)
    schema.add_field("section_title", DataType.VARCHAR, max_length=256)
    schema.add_field("content", DataType.VARCHAR, max_length=65535)
    schema.add_field("language", DataType.VARCHAR, max_length=8)
    schema.add_field("source_path", DataType.VARCHAR, max_length=512)
    index = client.prepare_index_params()
    index.add_index("embedding", index_type="HNSW", metric_type="COSINE", params={"M": 16, "efConstruction": 128})
    index.add_index("source_path", index_type="Trie")
    client.create_collection(collection, schema=schema, index_params=index)


def ensure_steps_collection(client) -> None:
    """Create the Steps collection with the real backend's exact field/index shape if it doesn't exist yet."""
    from pymilvus import DataType
    collection = DOMAINS["steps"]["collection"]
    if client.has_collection(collection):
        return
    logger.warning("MILVUS_COLLECTION_CREATE collection=%s", collection)
    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=STEPS_EMBEDDING_DIM)
    schema.add_field("modality", DataType.VARCHAR, max_length=16)
    schema.add_field("content_type", DataType.VARCHAR, max_length=32)
    schema.add_field("main_topic", DataType.VARCHAR, max_length=128)
    schema.add_field("workflow_name", DataType.VARCHAR, max_length=256)
    schema.add_field("workflow_path", DataType.VARCHAR, max_length=512)
    schema.add_field("path_json", DataType.VARCHAR, max_length=2048)
    schema.add_field("topic", DataType.VARCHAR, max_length=128)
    schema.add_field("subtopic", DataType.VARCHAR, max_length=256)
    schema.add_field("step_label", DataType.VARCHAR, max_length=128)
    schema.add_field("step_id", DataType.VARCHAR, max_length=128)
    schema.add_field("parent_step", DataType.VARCHAR, max_length=128)
    schema.add_field("order_index", DataType.INT64)
    schema.add_field("chunk_id", DataType.VARCHAR, max_length=512)
    schema.add_field("content", DataType.VARCHAR, max_length=8192)
    schema.add_field("raw_json", DataType.VARCHAR, max_length=32768)
    schema.add_field("image_path", DataType.VARCHAR, max_length=1024)
    index = client.prepare_index_params()
    index.add_index("embedding", index_type="HNSW", metric_type="COSINE", params={"M": 16, "efConstruction": 128})
    for field_name in ("chunk_id", "main_topic", "subtopic", "content_type", "workflow_path", "modality"):
        index.add_index(field_name, index_type="Trie")
    client.create_collection(collection, schema=schema, index_params=index)


def steps_delete_filter(relative: str) -> str:
    filename = relative.rsplit("/", 1)[-1]
    if filename in STEPS_SHARED_FOLDER_CONTENT_TYPES:
        dir_rel = relative.rsplit("/", 1)[0] if "/" in relative else ""
        types_list = ", ".join(f'"{t}"' for t in STEPS_SHARED_FOLDER_CONTENT_TYPES[filename])
        escaped_dir = dir_rel.replace("\\", "\\\\").replace('"', '\\"')
        return f'workflow_path == "{escaped_dir}" and content_type in [{types_list}]'
    escaped = relative.replace("\\", "\\\\").replace('"', '\\"')
    return f'workflow_path == "{escaped}"'


def milvus_error_status(exc: Exception) -> str:
    text = str(exc).lower()
    if "dimension" in text or "schema" in text or ("field" in text and ("not exist" in text or "not found" in text)):
        return "schema_mismatch"
    return "failed"


def milvus_varchar(value: Any, max_bytes: int) -> str:
    """Fit text into a Milvus VARCHAR limit, which is measured in UTF-8 bytes."""
    encoded = str(value or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8")
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def sync_retail_milvus(relative: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    collection = retail_collection_for(relative)
    if not collection:
        logger.warning("MILVUS_RETAIL_UNROUTED source=%s", relative)
        return {"status": "skipped", "host": MILVUS_HOST, "error": "This item isn't inside a recognized retail product folder (cards/loans/deposits), so it wasn't synced to Milvus."}
    try:
        model_name = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
        logger.info("EMBEDDING_START domain=retail source=%s chunks=%d model=%s device=%s", relative, len(chunks), model_name, os.getenv("EMBEDDING_DEVICE", "cpu"))
        started = time.perf_counter()
        model = embedding_model()
        vectors = model.encode(["passage: " + c["content"] for c in chunks], normalize_embeddings=True).tolist() if chunks else []
        logger.info("EMBEDDING_COMPLETE domain=retail source=%s vectors=%d duration_ms=%d", relative, len(vectors), round((time.perf_counter() - started) * 1000))
        client = milvus_client()
        ensure_retail_collection(client, collection, len(vectors[0]) if vectors else 1024)
        logger.info("MILVUS_CONNECTED host=%s port=%d collection=%s", MILVUS_HOST, MILVUS_PORT, collection)
        stored_source = milvus_varchar(relative, 512)
        escaped = stored_source.replace("\\", "\\\\").replace('"', '\\"')
        client.delete(collection_name=collection, filter=f'source_path == "{escaped}"')
        logger.info("MILVUS_OLD_SOURCE_DELETED collection=%s source=%s", collection, relative)
        if chunks:
            client.insert(collection_name=collection, data=[{
                "embedding": vector,
                "topic": milvus_varchar(c.get("topic", ""), 32),
                "section": milvus_varchar(c.get("section", ""), 32),
                "variant": milvus_varchar(c.get("variant", ""), 32),
                "section_title": milvus_varchar(c.get("section_title", ""), 256),
                "content": milvus_varchar(c["content"], 65535),
                "language": milvus_varchar(c.get("language", "en"), 8),
                "source_path": stored_source,
            } for c, vector in zip(chunks, vectors)])
        logger.info("MILVUS_SYNC_COMPLETE collection=%s source=%s chunks=%d", collection, relative, len(chunks))
        return {"status": "synced", "host": MILVUS_HOST, "collection": collection, "chunks": len(chunks)}
    except Exception as exc:
        logger.exception("MILVUS_SYNC_FAILED domain=retail source=%s collection=%s error=%s", relative, collection, exc)
        return {"status": milvus_error_status(exc), "host": MILVUS_HOST, "collection": collection, "error": str(exc)}


def sync_steps_milvus(relative: str, path: Path, asset_root: Path = ACTIVE_STEPS_ROOT) -> dict[str, Any]:
    collection = DOMAINS["steps"]["collection"]
    if path.suffix.lower() != ".json" or not path.is_file():
        return {"status": "skipped", "host": MILVUS_HOST, "collection": collection, "error": "Source is not a readable JSON file"}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        logger.info("EMBEDDING_START domain=steps source=%s model=%s device=%s", relative, STEPS_MODEL_ID, os.getenv("EMBEDDING_DEVICE", "cpu"))
        started = time.perf_counter()
        rows = build_rows_for_source(relative=relative, data=data, steps_root=ACTIVE_STEPS_ROOT)
        insert_rows = []
        for row in rows:
            embed_text = row.pop("_embed_text")
            embed_image_rel = row.pop("_embed_image_rel")
            if embed_text is not None:
                row["embedding"] = embed_steps_text(embed_text)
            elif embed_image_rel:
                row["embedding"] = embed_steps_image(asset_root / embed_image_rel)
            else:
                continue
            insert_rows.append(row)
        logger.info("EMBEDDING_COMPLETE domain=steps source=%s vectors=%d duration_ms=%d", relative, len(insert_rows), round((time.perf_counter() - started) * 1000))
        client = milvus_client()
        ensure_steps_collection(client)
        logger.info("MILVUS_CONNECTED host=%s port=%d collection=%s", MILVUS_HOST, MILVUS_PORT, collection)
        delete_filter = steps_delete_filter(relative)
        client.delete(collection_name=collection, filter=delete_filter)
        logger.info("MILVUS_OLD_SOURCE_DELETED collection=%s source=%s filter=%s", collection, relative, delete_filter)
        if insert_rows:
            client.insert(collection_name=collection, data=insert_rows)
        logger.info("MILVUS_SYNC_COMPLETE collection=%s source=%s chunks=%d", collection, relative, len(insert_rows))
        return {"status": "synced", "host": MILVUS_HOST, "collection": collection, "chunks": len(insert_rows)}
    except Exception as exc:
        logger.exception("MILVUS_SYNC_FAILED domain=steps source=%s collection=%s error=%s", relative, collection, exc)
        return {"status": milvus_error_status(exc), "host": MILVUS_HOST, "collection": collection, "error": str(exc)}


def sync_milvus(domain: str, relative: str, chunks: list[dict[str, Any]], path: Path) -> dict[str, Any]:
    if os.getenv("MILVUS_SYNC_ENABLED", "true").lower() != "true":
        logger.warning("MILVUS_DISABLED domain=%s source=%s chunks=%d", domain, relative, len(chunks))
        return {"status": "disabled", "host": MILVUS_HOST}
    if domain == "steps":
        return sync_steps_milvus(relative, path)
    return sync_retail_milvus(relative, chunks)


def delete_milvus_source(domain: str, relative: str, drop_if_empty: bool = False) -> dict[str, Any]:
    if os.getenv("MILVUS_SYNC_ENABLED", "true").lower() != "true":
        return {"status": "disabled", "host": MILVUS_HOST}
    collection = resolve_collection(domain, relative)
    if not collection:
        return {"status": "skipped", "host": MILVUS_HOST}
    try:
        client = milvus_client()
        dropped = False
        if client.has_collection(collection):
            if domain == "steps":
                filter_expr = steps_delete_filter(relative)
            else:
                escaped = relative.replace("\\", "\\\\").replace('"', '\\"')
                filter_expr = f'source_path == "{escaped}"'
            client.delete(collection_name=collection, filter=filter_expr)
            if drop_if_empty:
                client.flush(collection_name=collection)
                row_count = int(client.get_collection_stats(collection).get("row_count", 0))
                if row_count == 0:
                    client.drop_collection(collection)
                    dropped = True
        return {"status": "synced", "host": MILVUS_HOST, "collection": collection, "chunks": 0, "collection_dropped": dropped}
    except Exception as exc:
        logger.exception("MILVUS_DELETE_FAILED domain=%s source=%s error=%s", domain, relative, exc)
        return {"status": "failed", "host": MILVUS_HOST, "collection": collection, "error": str(exc)}


def pdf_safe_text(value: Any) -> str:
    """Turn source data into safe, readable text for the workflow export."""
    from html import escape
    return escape(clean_text(value)).replace("\n", "<br/>")


def render_generic_export_value(story: list[Any], key: str, value: Any, styles: dict[str, Any], level: int = 0) -> None:
    """Render one JSON key/value pair as readable PDF content.

    Fallback for Steps documents that are not one of the specifically supported
    shapes (a steps list, an FAQ list, tips, features, or services) so the export
    never has to dump raw JSON text into the PDF.
    """
    from reportlab.platypus import Paragraph, Spacer

    label = re.sub(r"[_\-]+", " ", str(key)).strip().title() or "Details"
    indent = "&nbsp;&nbsp;&nbsp;" * level
    if isinstance(value, str):
        if value.strip():
            story.append(Paragraph(f"{indent}<b>{pdf_safe_text(label)}:</b> {pdf_safe_text(value)}", styles["body"]))
    elif isinstance(value, (int, float, bool)):
        story.append(Paragraph(f"{indent}<b>{pdf_safe_text(label)}:</b> {pdf_safe_text(value)}", styles["body"]))
    elif isinstance(value, list):
        if not value:
            return
        story.append(Paragraph(f"{indent}<b>{pdf_safe_text(label)}</b>", styles["document"]))
        for item in value:
            if isinstance(item, dict):
                for sub_key, sub_value in item.items():
                    render_generic_export_value(story, sub_key, sub_value, styles, level + 1)
                story.append(Spacer(1, 4))
            elif isinstance(item, list):
                for sub_index, sub_item in enumerate(item, start=1):
                    render_generic_export_value(story, str(sub_index), sub_item, styles, level + 1)
            else:
                story.append(Paragraph(f"{indent}• {pdf_safe_text(item)}", styles["body"]))
    elif isinstance(value, dict):
        story.append(Paragraph(f"{indent}<b>{pdf_safe_text(label)}</b>", styles["document"]))
        for sub_key, sub_value in value.items():
            render_generic_export_value(story, sub_key, sub_value, styles, level + 1)
    elif value is not None:
        story.append(Paragraph(f"{indent}<b>{pdf_safe_text(label)}:</b> {pdf_safe_text(value)}", styles["body"]))


def document_title(data: Any, source: Path) -> str:
    """The display title used for one Steps document, in the export and its Contents entry alike."""
    title = source.stem.replace("_", " ")
    if isinstance(data, dict):
        title = clean_text(data.get("title")) or clean_text(data.get("type")) or title
    return title


def bordered_table(rows: list[list[Any]], col_widths: list[float], header: bool = True) -> Any:
    """A Table styled like the rest of the export: navy header row (if any), a visible grid,
    and a light body background - the shared "clear table with borders" look for every
    document type, not just step-by-step workflows."""
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.6, HexColor("#8FA79A")),
        ("BACKGROUND", (0, 1 if header else 0), (-1, -1), HexColor("#FFFFFF")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), HexColor("#183B52")))
    table.setStyle(TableStyle(style))
    return table


def boxed_content(flowables: list[Any]) -> Any:
    """Wrap a short list of flowables in a single bordered box - used for content shapes
    that don't naturally form a multi-row table (e.g. the free-form fallback)."""
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Table, TableStyle

    box = Table([[flowables]], colWidths=[493])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.7, HexColor("#8FA79A")),
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#FFFFFF")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return box


def add_workflow_export_content(story: list[Any], data: Any, source: Path, styles: dict[str, Any], anchor_key: str | None = None, show_title: bool = True) -> None:
    """Append one supported Steps JSON document to a ReportLab story.

    Every document - whatever its shape - is rendered inside a clearly bordered table or
    box, with its own title bar, so a reader can see exactly where one document ends and
    the next begins. anchor_key, if given, makes this document a jump target from the
    Contents page and the PDF's Bookmarks panel. show_title can be set to False when the
    caller already printed this exact title as a heading immediately above (e.g. a subtopic
    with a single, identically-named document) so the title isn't shown twice in a row.
    """
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image as PdfImage, Paragraph, Spacer, Table, TableStyle

    if show_title:
        title = document_title(data, source)
        title_html = (f'<a name="{anchor_key}"/>' if anchor_key else "") + pdf_safe_text(title)
        title_card = Table([[Paragraph(title_html, styles["document"])]], colWidths=[493])
        title_card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), HexColor("#EEF7F3")),
            ("BOX", (0, 0), (-1, -1), 1.1, HexColor("#183B52")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.extend([title_card, Spacer(1, 7)])
    validation = data.get("validation") if isinstance(data, dict) else None
    if isinstance(validation, dict) and validation.get("validated_by"):
        approved_at = clean_text(validation.get("validated_at"))
        stamp = f"Approved by {pdf_safe_text(validation['validated_by'])}"
        if approved_at:
            stamp += f" on {pdf_safe_text(approved_at)}"
        story.append(Paragraph(stamp, styles["stamp"]))
        story.append(Spacer(1, 5))

    def step_images(value: Any) -> list[Any]:
        images: list[Any] = []
        names = value if isinstance(value, list) else str(value or "").split(",")
        for name in (clean_text(item) for item in names):
            if not name:
                continue
            image_path = (source.parent / name).resolve()
            if source.parent.resolve() not in image_path.parents or not image_path.is_file():
                logger.warning("PDF_EXPORT_IMAGE_SKIPPED workflow=%s image=%s", source, name)
                continue
            try:
                width, height = ImageReader(str(image_path)).getSize()
                scale = min(135 / width, 190 / height, 1)
                image = PdfImage(str(image_path), width=width * scale, height=height * scale)
                image.hAlign = "LEFT"
                images.append(image)
            except Exception as exc:
                logger.warning("PDF_EXPORT_IMAGE_FAILED workflow=%s image=%s error=%s", source, name, exc)
        return images

    def step_rows(items: Any, level: int = 0) -> list[list[Any]]:
        rows: list[list[Any]] = []
        for position, item in enumerate(items if isinstance(items, list) else [], start=1):
            if not isinstance(item, dict):
                continue
            label = clean_text(item.get("step") or item.get("Note") or position)
            text = clean_text(item.get("text")) or "No instruction entered"
            name = "Step" if level == 0 else "Substep"
            indent = "&nbsp;&nbsp;&nbsp;" * level
            image_cell = step_images(item.get("image")) or [Paragraph("No image", styles["body"])]
            rows.append([
                Paragraph(f"{indent}<b>{name}<br/>{pdf_safe_text(label)}</b>", styles["body"]),
                Paragraph(pdf_safe_text(text), styles["body"]),
                image_cell,
            ])
            rows.extend(step_rows(item.get("substeps"), level + 1))
        return rows

    if isinstance(data, dict) and isinstance(data.get("steps"), list):
        header = [Paragraph('<font color="#FFFFFF"><b>Step</b></font>', styles["body"]), Paragraph('<font color="#FFFFFF"><b>Instruction</b></font>', styles["body"]), Paragraph('<font color="#FFFFFF"><b>Related image</b></font>', styles["body"])]
        rows = [header] + step_rows(data["steps"])
        story.append(bordered_table(rows, [76, 270, 147]))
    elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
        header = [Paragraph('<font color="#FFFFFF"><b>Question</b></font>', styles["body"]), Paragraph('<font color="#FFFFFF"><b>Answer</b></font>', styles["body"])]
        rows = [header]
        for item in data:
            question = clean_text(item.get("question")) or "-"
            answer = clean_text(item.get("answer")) or "-"
            rows.append([Paragraph(pdf_safe_text(question), styles["body"]), Paragraph(pdf_safe_text(answer), styles["body"])])
        story.append(bordered_table(rows, [160, 333]))
    elif isinstance(data, dict) and isinstance(data.get("tips"), list):
        header = [Paragraph('<font color="#FFFFFF"><b>Tip</b></font>', styles["body"])]
        rows = [header] + [[Paragraph(pdf_safe_text(tip), styles["body"])] for tip in data["tips"]]
        story.append(bordered_table(rows, [493]))
    elif isinstance(data, dict) and isinstance(data.get("services"), list):
        header = [Paragraph('<font color="#FFFFFF"><b>Service</b></font>', styles["body"]), Paragraph('<font color="#FFFFFF"><b>Details</b></font>', styles["body"])]
        rows = [header]
        for service in data["services"]:
            if not isinstance(service, dict):
                continue
            details = service.get("description", []) if isinstance(service.get("description"), list) else []
            detail_html = "<br/>".join(f"• {pdf_safe_text(detail)}" for detail in details) or "-"
            rows.append([Paragraph(pdf_safe_text(service.get("service")) or "-", styles["body"]), Paragraph(detail_html, styles["body"])])
        story.append(bordered_table(rows, [160, 333]))
    elif isinstance(data, dict) and isinstance(data.get("features"), list):
        header = [Paragraph('<font color="#FFFFFF"><b>Feature</b></font>', styles["body"]), Paragraph('<font color="#FFFFFF"><b>Details</b></font>', styles["body"])]
        rows = [header]
        for feature in data["features"]:
            if not isinstance(feature, dict):
                continue
            details = feature.get("details", []) if isinstance(feature.get("details"), list) else []
            detail_html = "<br/>".join(f"• {pdf_safe_text(detail)}" for detail in details) or "-"
            rows.append([Paragraph(pdf_safe_text(feature.get("name")) or "-", styles["body"]), Paragraph(detail_html, styles["body"])])
        story.append(bordered_table(rows, [160, 333]))
    elif isinstance(data, dict):
        inner: list[Any] = []
        for key, value in data.items():
            if key in {"title", "type", "topic", "validation", "login_stage"}:
                continue
            render_generic_export_value(inner, key, value, styles)
        if inner:
            story.append(boxed_content(inner))
    elif isinstance(data, list):
        inner: list[Any] = []
        for index, item in enumerate(data, start=1):
            render_generic_export_value(inner, str(index), item, styles)
        if inner:
            story.append(boxed_content(inner))
    else:
        logger.warning("PDF_EXPORT_UNRECOGNIZED_SHAPE workflow=%s", source)
        story.append(boxed_content([Paragraph(pdf_safe_text(json.dumps(data, ensure_ascii=False, indent=2)), styles["body"])]))
    story.append(Spacer(1, 14))


@app.before_request
def log_request_start():
    g.request_started = time.perf_counter()
    g.request_id = request.headers.get("X-Request-ID") or secrets.token_hex(4)
    user = session.get("username", "anonymous")
    logger.info("REQUEST_START id=%s method=%s path=%s remote=%s user=%s content_length=%s", g.request_id, request.method, request.path, request.remote_addr, user, request.content_length or 0)


@app.after_request
def log_request_complete(response):
    duration = round((time.perf_counter() - getattr(g, "request_started", time.perf_counter())) * 1000)
    response.headers["X-Request-ID"] = getattr(g, "request_id", "unknown")
    logger.info("REQUEST_COMPLETE id=%s method=%s path=%s status=%d duration_ms=%d", getattr(g, "request_id", "unknown"), request.method, request.path, response.status_code, duration)
    return response


@app.errorhandler(Exception)
def log_unhandled_error(error):
    from werkzeug.exceptions import HTTPException
    if isinstance(error, HTTPException):
        logger.warning("HTTP_ERROR id=%s method=%s path=%s status=%d message=%s", getattr(g, "request_id", "unknown"), request.method, request.path, error.code, error.description)
        return jsonify(error=error.description), error.code
    logger.exception("UNHANDLED_ERROR id=%s method=%s path=%s", getattr(g, "request_id", "unknown"), request.method, request.path)
    return jsonify(error="Internal server error", request_id=getattr(g, "request_id", "unknown")), 500


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "portal.html") if current_user() else redirect("/login.html")


@app.get("/index.html")
def editor_page():
    """Steward/approver editor. Gated separately so a viewer-only account bounces back to the portal."""
    user = current_user()
    if not user:
        return redirect("/login.html")
    if not user["roles"]:
        return redirect("/")
    return send_from_directory(app.static_folder, "index.html")


@app.get("/health")
def health():
    """Unauthenticated health probe for the service manager/reverse proxy."""
    return jsonify(status="ok", service="capital-data-studio", time=now_iso(), devMode=DEV_MODE)


@app.post("/api/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    username, password = clean_text(body.get("username")), str(body.get("password", ""))
    roles = USER_ROLES.get(username.lower(), [])
    if not username or not password or not ldap_login(username, password):
        logger.warning("AUTH_FAILED username=%s remote=%s assigned_roles=%s", username or "missing", request.remote_addr, roles)
        return jsonify(error="Invalid credentials"), 401
    # Any authenticated employee may sign in - USER_ROLES only decides who gets the Data Editor tab (see all_domains below).
    session.clear(); session.update(username=username, roles=roles)
    logger.info("AUTH_SUCCESS username=%s remote=%s roles=%s", username, request.remote_addr, roles)
    return jsonify(user=current_user())


@app.post("/api/auth/logout")
def logout():
    logger.info("AUTH_LOGOUT username=%s remote=%s", session.get("username", "anonymous"), request.remote_addr)
    session.clear(); return jsonify(ok=True)


@app.get("/api/auth/me")
@require_auth
def me():
    return jsonify(
        user=current_user(),
        domains={key: {"label": value["label"], "collection": value["collection"]} for key, value in DOMAINS.items() if key in current_user()["roles"]},
        all_domains={key: {"label": value["label"]} for key, value in DOMAINS.items()},
        milvus={"host": MILVUS_HOST, "port": MILVUS_PORT},
        devMode=DEV_MODE,
    )


@app.get("/api/portal/<domain>/files")
@require_auth
def portal_files(domain):
    """Read-only, company-wide listing: published content only, regardless of the viewer's edit roles."""
    if domain not in DOMAINS:
        abort(404, "Unknown data area")
    all_files = [f for f in list_files(domain) if not f.get("pending")]
    return jsonify(files=all_files, topic_labels=topic_label_map() if domain == "steps" else {})


@app.get("/api/portal/<domain>/file")
@require_auth
def portal_file(domain):
    """Read-only published content for a Steps item. Retail documents use /api/portal/retail/extracted instead."""
    if domain not in DOMAINS:
        abort(404, "Unknown data area")
    _, path = safe_target(domain, request.args.get("path", ""))
    if not path.is_file():
        abort(404)
    if path.suffix.lower() == ".json":
        return jsonify(path=request.args.get("path", ""), data=json.loads(path.read_text(encoding="utf-8-sig")))
    return send_file(path, as_attachment=False)


@app.get("/api/portal/retail/extracted")
@require_auth
def portal_retail_extracted():
    """Read-only, published structured sections for a retail product - never pending edits."""
    relative = request.args.get("path", "")
    _, pdf = safe_target("retail", relative)
    if pdf.suffix.lower() not in {".pdf", ".product"} or not pdf.is_file():
        abort(404, "Retail product not found")
    structured = structured_retail_path(relative)
    approved = extracted_path(relative)
    if structured.is_file():
        data = json.loads(structured.read_text(encoding="utf-8-sig"))
    elif approved.is_file():
        data = json.loads(approved.read_text(encoding="utf-8"))
    elif pdf.suffix.lower() == ".pdf":
        data = extract_retail_pdf(pdf)
    else:
        data = {"title": pdf.stem, "sections": []}
    data.setdefault("title", pdf.stem)
    data = normalize_retail_data(data)
    return jsonify(data=data)


@app.get("/api/portal/steps/asset")
@require_auth
def portal_step_asset():
    """Read-only published image for a Steps entry - available to every signed-in employee, not only Steps editors."""
    _, path = safe_target("steps", request.args.get("path", ""))
    return send_file(path) if path.is_file() else abort(404)


@app.get("/api/data/<domain>/files")
@require_domain
def files(domain):
    all_files = list_files(domain)
    # SECURITY FIX: Non-approvers only see published items, not pending changes awaiting approval
    if not current_user()["approver"]:
        all_files = [f for f in all_files if not f.get("pending")]
    return jsonify(files=all_files, topic_labels=topic_label_map() if domain == "steps" else {}, collection_roles=retail_collection_roles() if domain == "retail" else {}, collection_labels=retail_collection_labels() if domain == "retail" else {})


@app.get("/api/data/steps/export")
@require_auth
def export_steps_pdf():
    if "steps" not in current_user()["roles"]:
        return jsonify(error="You do not have access to Steps & workflows"), 403
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        return jsonify(error="PDF export is unavailable until the ReportLab dependency is installed"), 503

    started = time.perf_counter()

    class SectionMark(Flowable):
        """Zero-size marker: adds this point to the PDF's Bookmarks/Outline panel and
        records which page it lands on, so the Contents table can show real page numbers."""

        def __init__(self, key: str, title: str, level: int, registry: dict[str, int]):
            Flowable.__init__(self)
            self.key, self.title, self.level, self.registry = key, title, level, registry

        def wrap(self, available_width, available_height):
            return (0, 0)

        def draw(self):
            self.canv.bookmarkPage(self.key)
            self.canv.addOutlineEntry(self.title, self.key, self.level, 0)
            self.registry[self.key] = self.canv.getPageNumber()

    root = DOMAINS["steps"]["root"]
    topic_paths = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name.lower())
    def anchor(prefix: str, value: str) -> str:
        return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:12]}"

    topic_groups: list[tuple[Path, list[Path], dict[str, list[Path]]]] = []
    for topic_path in topic_paths:
        json_sources = sorted(topic_path.rglob("*.json"), key=lambda path: path.relative_to(topic_path).as_posix().lower())
        grouped_sources: dict[str, list[Path]] = {}
        for source in json_sources:
            group = source.parent.relative_to(topic_path).as_posix()
            grouped_sources.setdefault(group, []).append(source)
        topic_groups.append((topic_path, json_sources, grouped_sources))

    # Read every document once up front (title + a stable jump target for both passes),
    # so the Contents table can link and page-number all the way down to each document,
    # not just each topic and subtopic.
    document_cache: dict[Path, tuple[Any, str, str]] = {}
    for topic_path, json_sources, _ in topic_groups:
        for source in json_sources:
            try:
                data = json.loads(source.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                logger.warning("PDF_EXPORT_READ_FAILED workflow=%s", source)
                data = None
            title = document_title(data, source) if data is not None else source.stem.replace("_", " ")
            doc_key = anchor("document", f"{topic_path.name}/{source.relative_to(topic_path).as_posix()}")
            document_cache[source] = (data, title, doc_key)

    base = getSampleStyleSheet()
    styles = {
        "cover": ParagraphStyle("export-cover", parent=base["Title"], fontName="Helvetica-Bold", fontSize=23, leading=28, textColor=colors.HexColor("#183B52"), alignment=TA_CENTER, spaceAfter=12),
        "topic": ParagraphStyle("export-topic", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=colors.HexColor("#111111"), spaceBefore=4, spaceAfter=12),
        "subtopic": ParagraphStyle("export-subtopic", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=colors.HexColor("#111111"), spaceBefore=12, spaceAfter=6),
        "document": ParagraphStyle("export-document", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=colors.HexColor("#111111"), spaceBefore=5, spaceAfter=5),
        "body": ParagraphStyle("export-body", parent=base["BodyText"], fontName="Helvetica", fontSize=9.2, leading=13, textColor=colors.HexColor("#111111"), spaceAfter=4),
        "muted": ParagraphStyle("export-muted", parent=base["BodyText"], fontName="Helvetica", fontSize=10, leading=14, textColor=colors.HexColor("#587064"), alignment=TA_CENTER),
        "stamp": ParagraphStyle("export-stamp", parent=base["BodyText"], fontName="Helvetica-Oblique", fontSize=8.3, leading=11, textColor=colors.HexColor("#587064")),
        "toc_doc": ParagraphStyle("export-toc-doc", parent=base["BodyText"], fontName="Helvetica", fontSize=8.3, leading=11, textColor=colors.HexColor("#4B5B54")),
    }

    page_map: dict[str, int] = {}

    def build_story(toc_pages: dict[str, int]) -> list[Any]:
        contents_rows = [[Paragraph('<font color="#FFFFFF"><b>Contents</b></font>', styles["body"]), Paragraph('<font color="#FFFFFF"><b>Page</b></font>', styles["body"])]]
        for topic_path, _, grouped_sources in topic_groups:
            topic_anchor = anchor("topic", topic_path.name)
            contents_rows.append([Paragraph(f'<link href="#{topic_anchor}"><b>{pdf_safe_text(topic_path.name)}</b></link>', styles["body"]), Paragraph(str(toc_pages.get(topic_anchor, "")), styles["body"])])
            for group, sources in grouped_sources.items():
                subtopic = group if group != "." else "General"
                group_anchor = anchor("subtopic", f"{topic_path.name}/{group}")
                contents_rows.append([Paragraph(f'&nbsp;&nbsp;&nbsp;<link href="#{group_anchor}">{pdf_safe_text(subtopic)}</link>', styles["body"]), Paragraph(str(toc_pages.get(group_anchor, "")), styles["body"])])
                for source in sources:
                    _, doc_title, doc_key = document_cache[source]
                    contents_rows.append([Paragraph(f'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<link href="#{doc_key}">{pdf_safe_text(doc_title)}</link>', styles["toc_doc"]), Paragraph(str(toc_pages.get(doc_key, "")), styles["toc_doc"])])
        contents = Table(contents_rows, colWidths=[420, 73], repeatRows=1)
        contents.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#183B52")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B9CEC4")),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#FAFCFB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story: list[Any] = [Paragraph("Capital Data Studio", styles["cover"]), Paragraph("Steps & Workflows export", styles["cover"]), Spacer(1, 12), Paragraph(f"Generated {datetime.now().strftime('%d %b %Y, %I:%M %p')} - {len(topic_paths)} main topics", styles["muted"]), PageBreak(), Paragraph("Contents", styles["topic"]), Paragraph("Select a main topic, subtopic, or individual document to jump straight to it, or open the Bookmarks panel in your PDF viewer.", styles["muted"]), Spacer(1, 10), contents, PageBreak()]

        for topic_index, (topic_path, json_sources, grouped_sources) in enumerate(topic_groups):
            if topic_index:
                story.append(PageBreak())
            topic_anchor = anchor("topic", topic_path.name)
            story.append(SectionMark(topic_anchor, topic_path.name, 0, page_map))
            story.append(Paragraph(f'<a name="{topic_anchor}"/>{pdf_safe_text(topic_path.name)}', styles["topic"]))
            if not json_sources:
                story.append(Paragraph("No workflow documents in this topic.", styles["body"]))
                continue

            summary_rows = [[Paragraph('<font color="#FFFFFF"><b>Subtopic</b></font>', styles["body"]), Paragraph('<font color="#FFFFFF"><b>Workflows and documents</b></font>', styles["body"])]]
            for group, sources in grouped_sources.items():
                subtopic = group if group != "." else "General"
                documents = "<br/>".join(pdf_safe_text(source.stem.replace("_", " ")) for source in sources)
                group_anchor = anchor("subtopic", f"{topic_path.name}/{group}")
                summary_rows.append([Paragraph(f'<link href="#{group_anchor}">{pdf_safe_text(subtopic)}</link>', styles["body"]), Paragraph(documents, styles["body"])])
            summary = Table(summary_rows, colWidths=[185, 308], repeatRows=1)
            summary.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#183B52")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B9CEC4")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#FAFCFB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.extend([Paragraph("Topic overview", styles["subtopic"]), summary, Spacer(1, 12), Paragraph("Workflow details", styles["subtopic"])])

            previous_group = None
            for source in json_sources:
                group = source.parent.relative_to(topic_path).as_posix()
                subtopic_label = group if group != "." else "General"
                data, doc_title, doc_key = document_cache[source]
                # A subtopic that holds a single, identically-named document (e.g. subtopic
                # "Registration" containing just a "Registration" workflow) would otherwise
                # print that title twice in a row - once as the subtopic heading, once as
                # the document's own title card. Fold the document's jump anchor into the
                # subtopic heading in that case and skip the redundant card.
                merge_doc_anchor = data is not None and clean_text(subtopic_label).lower() == clean_text(doc_title).lower()
                if group != previous_group:
                    group_anchor = anchor("subtopic", f"{topic_path.name}/{group}")
                    group_title = f"{topic_path.name} / {subtopic_label}"
                    story.append(SectionMark(group_anchor, group_title, 1, page_map))
                    heading_anchors = f'<a name="{group_anchor}"/>' + (f'<a name="{doc_key}"/>' if merge_doc_anchor else "")
                    story.append(Paragraph(f'{heading_anchors}{pdf_safe_text(subtopic_label)}', styles["subtopic"]))
                    previous_group = group
                elif merge_doc_anchor:
                    # Same subtopic already printed by an earlier document in this group -
                    # merging isn't safe here (the heading belongs to a different document),
                    # so fall back to showing this document's own title card.
                    merge_doc_anchor = False
                story.append(SectionMark(doc_key, f"{topic_path.name} / {doc_title}", 2, page_map))
                if data is None:
                    story.append(Paragraph(f'<a name="{doc_key}"/><b>{pdf_safe_text(source.name)}</b> - Could not read this document.', styles["body"]))
                    story.append(Spacer(1, 14))
                    continue
                add_workflow_export_content(story, data, source, styles, anchor_key=None if merge_doc_anchor else doc_key, show_title=not merge_doc_anchor)
        return story

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#B9CEC4"))
        canvas.line(18 * mm, 12 * mm, A4[0] - 18 * mm, 12 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#587064"))
        canvas.drawString(18 * mm, 7.5 * mm, "Capital Data Studio - Steps & Workflows")
        canvas.drawRightString(A4[0] - 18 * mm, 7.5 * mm, f"Page {doc.page}")
        canvas.restoreState()

    def new_document(target: BytesIO) -> Any:
        return SimpleDocTemplate(target, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=17 * mm, bottomMargin=17 * mm, title="Capital Data Studio - Steps & Workflows")

    # Pass 1: lay out the document once (discarded) purely to learn which page each
    # topic/subtopic lands on, so the Contents table can show real page numbers.
    new_document(BytesIO()).build(build_story({}), onFirstPage=footer, onLaterPages=footer)

    # Pass 2: the real export, now with page numbers in the Contents table and a full
    # PDF outline (Bookmarks panel) built from the same topic/subtopic markers.
    buffer = BytesIO()
    new_document(buffer).build(build_story(page_map), onFirstPage=footer, onLaterPages=footer)
    buffer.seek(0)
    logger.info("STEPS_PDF_EXPORTED topics=%d user=%s duration_ms=%d", len(topic_paths), current_user()["username"], round((time.perf_counter() - started) * 1000))
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name="capital-data-studio-steps-and-workflows.pdf")


def build_retail_pdf(relative: str) -> tuple[BytesIO, str]:
    """Render one approved structured Retail product as a business-readable PDF."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import CondPageBreak, Flowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("PDF export is unavailable until the ReportLab dependency is installed") from exc

    class RetailBookmark(Flowable):
        """A zero-height destination used by the Contents links and PDF bookmarks panel."""

        def __init__(self, key: str, bookmark_title: str):
            Flowable.__init__(self)
            self.key = key
            self.bookmark_title = bookmark_title

        def wrap(self, available_width, available_height):
            return (0, 0)

        def draw(self):
            self.canv.bookmarkPage(self.key)
            self.canv.addOutlineEntry(self.bookmark_title, self.key, 0, 0)

    _, source = safe_target("retail", relative)
    structured = structured_retail_path(relative)
    approved = extracted_path(relative)
    if structured.is_file():
        data = json.loads(structured.read_text(encoding="utf-8-sig"))
    elif approved.is_file():
        data = json.loads(approved.read_text(encoding="utf-8-sig"))
    else:
        abort(404, "Structured Retail data not found")
    data = normalize_retail_data(data)
    title = clean_text(data.get("title") or data.get("name") or source.stem)
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("retail-title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=22, leading=27, textColor=colors.HexColor("#183B52"), alignment=TA_CENTER, spaceAfter=8),
        "category": ParagraphStyle("retail-category", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=colors.HexColor("#183B52"), spaceAfter=0, keepWithNext=True),
        "group": ParagraphStyle("retail-group", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=colors.HexColor("#245442"), spaceBefore=11, spaceAfter=5, keepWithNext=True),
        "section": ParagraphStyle("retail-section", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=colors.HexColor("#111111"), spaceBefore=8, spaceAfter=4, keepWithNext=True),
        "body": ParagraphStyle("retail-body", parent=base["BodyText"], fontName="Helvetica", fontSize=9, leading=13, textColor=colors.HexColor("#111111"), spaceAfter=4),
        "table_header": ParagraphStyle("retail-table-header", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=colors.white, spaceAfter=0),
        "item": ParagraphStyle("retail-item", parent=base["BodyText"], fontName="Helvetica", fontSize=9, leading=13, leftIndent=12, firstLineIndent=-8, textColor=colors.HexColor("#111111"), spaceAfter=3),
        "muted": ParagraphStyle("retail-muted", parent=base["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#587064"), alignment=TA_CENTER),
        "toc": ParagraphStyle("retail-toc", parent=base["BodyText"], fontName="Helvetica", fontSize=10, leading=15, textColor=colors.HexColor("#183B52"), leftIndent=10, spaceAfter=4),
    }
    export_sections = []
    seen_sections: set[str] = set()
    category_order: dict[str, int] = {}
    for original_index, section in enumerate(data.get("sections", [])):
        if not isinstance(section, dict):
            continue
        section_path = retail_section_path(section, title)
        relative_section_path = section_path[1:] if section_path and section_path[0].casefold() == title.casefold() else section_path
        category_key = clean_text(relative_section_path[0] if relative_section_path else section.get("section")).casefold()
        category_order.setdefault(category_key, len(category_order))
        fingerprint = json.dumps({"path": [clean_text(part).casefold() for part in section_path], "variant": section.get("variant"), "blocks": section.get("content_blocks"), "rows": section.get("table_rows"), "content": section.get("content")}, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen_sections:
            continue
        seen_sections.add(fingerprint)
        export_sections.append((category_order[category_key], original_index, section))
    ordered_sections = sorted(export_sections, key=lambda item: (item[0], item[1]))
    category_names = []
    for _, _, section in ordered_sections:
        section_path = retail_section_path(section, title)
        relative_section_path = section_path[1:] if section_path and section_path[0].casefold() == title.casefold() else section_path
        category_name = clean_text(relative_section_path[0] if relative_section_path else section.get("section")) or "Content"
        if category_name not in category_names:
            category_names.append(category_name)
    category_anchors = {name: f"retail_category_{index + 1}" for index, name in enumerate(category_names)}
    cover_card = Table([[Paragraph("BUSINESS-READABLE PRODUCT DATA", styles["muted"])], [Paragraph(pdf_safe_text(title), styles["title"])], [Paragraph(f"Generated {datetime.now().strftime('%d %b %Y, %I:%M %p')} - {len(ordered_sections)} sections", styles["muted"])]], colWidths=[A4[0] - 44 * mm])
    cover_card.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EEF7F3")), ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#9FB8AD")), ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    contents_entries = [
        Paragraph(
            f'<link href="#{category_anchors[name]}" color="#183B52"><u>{index + 1}. {pdf_safe_text(name)}</u></link>',
            styles["toc"],
        )
        for index, name in enumerate(category_names)
    ]
    story: list[Any] = [
        Spacer(1, 22 * mm),
        cover_card,
        Spacer(1, 18),
        Paragraph("Contents", styles["category"]),
        Paragraph("Select a section below to jump directly to it.", styles["muted"]),
        Spacer(1, 6),
        *contents_entries,
        PageBreak(),
    ]
    previous_category = ""
    previous_groups: list[str] = []
    for _, _, section in ordered_sections:
        if not isinstance(section, dict):
            continue
        path = retail_section_path(section, title)
        relative_path = path[1:] if path and path[0].casefold() == title.casefold() else path
        if not relative_path:
            relative_path = [clean_text(section.get("section")) or "Section"]
        category, groups, leaf = relative_path[0], relative_path[1:-1], relative_path[-1]
        if category != previous_category:
            if previous_category:
                story.append(CondPageBreak(45 * mm))
            story.append(RetailBookmark(category_anchors[category], category))
            category_banner = Table([[Paragraph(pdf_safe_text(category), styles["category"])]], colWidths=[A4[0] - 36 * mm])
            category_banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E7F2ED")), ("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#6F9987")), ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
            story.extend([category_banner, Spacer(1, 8)])
            previous_category, previous_groups = category, []
        for level, group in enumerate(groups):
            if level >= len(previous_groups) or previous_groups[level] != group:
                story.append(Paragraph(pdf_safe_text(group), styles["group"]))
                previous_groups = groups[:level + 1]
        if leaf != category or len(relative_path) > 1:
            story.append(Paragraph(pdf_safe_text(leaf), styles["section"]))
        if section.get("variant") == "table":
            rows = section.get("table_rows")
            if not isinstance(rows, list) or not rows:
                rows = [[clean_text(cell) for cell in line.split("|")] for line in str(section.get("content", "")).splitlines() if clean_text(line)]
            if rows:
                width = (A4[0] - 36 * mm) / max(1, max(len(row) for row in rows))
                cells = [[Paragraph(f'<font color="#FFFFFF"><b>{pdf_safe_text(cell)}</b></font>' if row_index == 0 else pdf_safe_text(cell), styles["table_header"] if row_index == 0 else styles["body"]) for cell in row] for row_index, row in enumerate(rows)]
                table = Table(cells, colWidths=[width] * max(len(row) for row in rows), repeatRows=1)
                table_commands = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#183B52")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9FB8AD")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]
                for row_index in range(1, len(rows)):
                    table_commands.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#F6FAF8") if row_index % 2 == 0 else colors.white))
                table.setStyle(TableStyle(table_commands))
                story.extend([table, Spacer(1, 8)])
            continue
        blocks = section.get("content_blocks") if isinstance(section.get("content_blocks"), list) else retail_content_blocks(section.get("content"))
        for block in blocks:
            if block.get("type") == "paragraph":
                if clean_text(block.get("text")):
                    story.append(Paragraph(pdf_safe_text(block.get("text")), styles["body"]))
                continue
            label = clean_text(block.get("label"))
            normalized_label = label.casefold().rstrip(":")
            if label and normalized_label not in {"items", "steps"} and normalized_label != clean_text(leaf).casefold().rstrip(":"):
                story.append(Paragraph(pdf_safe_text(label), styles["section"]))
            for index, item in enumerate(block.get("items", []) if isinstance(block.get("items"), list) else []):
                marker = f"{index + 1}." if block.get("type") == "numbered_list" else "-"
                story.append(Paragraph(f"{marker} {pdf_safe_text(item)}", styles["item"]))
        story.append(Spacer(1, 5))

    def footer(canvas, doc):
        canvas.saveState(); canvas.setStrokeColor(colors.HexColor("#B9CEC4")); canvas.line(18 * mm, 12 * mm, A4[0] - 18 * mm, 12 * mm)
        canvas.setFont("Helvetica", 8); canvas.setFillColor(colors.HexColor("#587064")); canvas.drawString(18 * mm, 7.5 * mm, f"Capital Data Studio - {title}"); canvas.drawRightString(A4[0] - 18 * mm, 7.5 * mm, f"Page {doc.page}"); canvas.restoreState()

    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=17 * mm, bottomMargin=17 * mm, title=f"Capital Data Studio - {title}")
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    buffer.seek(0)
    filename = re.sub(r'[^A-Za-z0-9 _-]+', '', title).strip() or source.stem
    return buffer, f"{filename}.pdf"


@app.get("/api/data/retail/export")
@require_auth
def export_retail():
    if "retail" not in current_user()["roles"]:
        return jsonify(error="You do not have access to Retail products"), 403
    relative = request.args.get("path", "").strip("/\\")
    root, target = safe_target("retail", relative)
    if target.is_file() and target.suffix.lower() in DOMAINS["retail"]["extensions"]:
        try:
            buffer, filename = build_retail_pdf(target.relative_to(root).as_posix())
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 503
        logger.info("RETAIL_PDF_EXPORTED source=%s user=%s", relative, current_user()["username"])
        return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=filename)
    if not target.is_dir():
        abort(404, "Retail folder not found")
    sources = sorted((path for path in target.rglob("*") if path.is_file() and path.suffix.lower() in DOMAINS["retail"]["extensions"]), key=lambda path: path.name.casefold())
    # A collection may be created from an existing standalone product (for
    # example cards.pdf plus cards/new_product.product). Include that original
    # product as the collection's General information export.
    general_sources = [path for path in root.iterdir() if path.is_file() and path.suffix.lower() in DOMAINS["retail"]["extensions"] and path.stem.strip().casefold() == target.name.strip().casefold()]
    sources = sorted([*general_sources, *sources], key=lambda path: path.name.casefold())
    if not sources:
        return jsonify(error="This Retail folder has no products to export"), 404
    archive = BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for source in sources:
            product_relative = source.relative_to(root).as_posix()
            pdf, filename = build_retail_pdf(product_relative)
            stem, suffix, candidate = Path(filename).stem, Path(filename).suffix, filename
            counter = 2
            while candidate.casefold() in used_names:
                candidate = f"{stem} {counter}{suffix}"; counter += 1
            used_names.add(candidate.casefold()); bundle.writestr(candidate, pdf.getvalue())
    archive.seek(0)
    folder_name = re.sub(r'[^A-Za-z0-9 _-]+', '', target.name).strip() or "retail-products"
    logger.info("RETAIL_FOLDER_EXPORTED folder=%s products=%d user=%s", relative, len(sources), current_user()["username"])
    return send_file(archive, mimetype="application/zip", as_attachment=True, download_name=f"{folder_name}.zip")


@app.put("/api/data/steps/topic-label")
@require_auth
def save_topic_label():
    if "steps" not in current_user()["roles"]:
        return jsonify(error="You do not have access to Steps & workflows"), 403
    body = request.get_json(silent=True) or {}
    topic = clean_text(body.get("topic"))
    stage = clean_text(body.get("login_stage")).lower()
    if not topic or "/" in topic or "\\" in topic or topic in {".", ".."}:
        return jsonify(error="A valid main topic is required"), 422
    if stage not in {"", "pre_login", "post_login"}:
        return jsonify(error="Choose Pre-login or Post-login"), 422
    labels = topic_label_map()
    if stage:
        labels[topic] = stage
    else:
        labels.pop(topic, None)
    TOPIC_LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOPIC_LABELS_PATH.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("TOPIC_LABEL_SAVED topic=%s stage=%s user=%s", topic, stage or "none", current_user()["username"])
    return jsonify(ok=True, topic_labels=labels, message="Topic login label saved")


@app.get("/api/data/<domain>/file")
@require_domain
def get_file(domain):
    root, path = safe_target(domain, request.args.get("path", ""))
    staged = pending_path(domain, request.args.get("path", ""))
    if not path.is_file() and not staged.is_file(): abort(404)
    if path.suffix.lower() == ".json":
        # SECURITY FIX: Only approvers see pending changes; others see published
        visible = (staged if staged.is_file() else path) if current_user()["approver"] else path
        pending = pending_info(domain, request.args.get("path", "")) if current_user()["approver"] else None
        return jsonify(path=request.args.get("path", ""), data=json.loads(visible.read_text(encoding="utf-8-sig")), pending=pending)
    return send_file(path, as_attachment=False)


@app.get("/api/data/retail/extracted")
@require_auth
def get_retail_extracted():
    if "retail" not in current_user()["roles"]:
        return jsonify(error="You do not have access to this data area"), 403
    relative = request.args.get("path", "")
    _, pdf = safe_target("retail", relative)
    staged_pdf = pending_path("retail", relative)
    visible_pdf = staged_pdf if staged_pdf.is_file() else pdf
    if pdf.suffix.lower() not in {".pdf", ".product"} or not visible_pdf.is_file():
        abort(404, "Retail product not found")
    pending = extracted_path(relative, pending=True)
    approved = extracted_path(relative)
    structured = structured_retail_path(relative)
    # SECURITY FIX: Approvers see pending extractions; others see approved/live
    if current_user()["approver"] and pending.is_file():
        data = json.loads(pending.read_text(encoding="utf-8"))
    elif staged_pdf.is_file() and pdf.suffix.lower() == ".pdf":
        data = extract_retail_pdf(visible_pdf)
    elif structured.is_file():
        data = json.loads(structured.read_text(encoding="utf-8-sig"))
    elif approved.is_file():
        data = json.loads(approved.read_text(encoding="utf-8"))
    elif pdf.suffix.lower() == ".pdf":
        data = extract_retail_pdf(pdf)
    else:
        data = {"title": pdf.stem, "native_product": True, "sections": []}
    data.setdefault("title", visible_pdf.stem)
    data = normalize_retail_data(data)
    schema_errors = validate_document(data)
    if schema_errors:
        logger.warning("RETAIL_SCHEMA_INVALID source=%s errors=%s", relative, schema_errors[:5])
    pending_info = retail_pending_info(relative) if current_user()["approver"] else None
    return jsonify(data=data, pending=pending_info, changes=retail_pending_changes(relative, data) if pending_info else {"document": [], "sections": []})


@app.put("/api/data/retail/extracted")
@require_auth
def save_retail_extracted():
    if "retail" not in current_user()["roles"]:
        return jsonify(error="You do not have access to this data area"), 403
    relative = request.args.get("path", "")
    _, pdf = safe_target("retail", relative)
    staged_pdf = pending_path("retail", relative)
    data = (request.get_json(silent=True) or {}).get("data")
    if pdf.suffix.lower() not in {".pdf", ".product"} or not (pdf.is_file() or staged_pdf.is_file()):
        abort(404, "Retail product not found")
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        return jsonify(error="Extracted retail data must contain a sections list"), 422
    data = normalize_retail_data(data)
    schema_errors = validate_document(data)
    if schema_errors:
        return jsonify(error="Retail product structure is invalid", details=schema_errors[:20]), 422
    for section in data["sections"]:
        if not isinstance(section, dict):
            return jsonify(error="Every retail section must contain text"), 422
        structured_content = retail_blocks_text(section.get("content_blocks"))
        if structured_content:
            section["content"] = structured_content
        if not clean_text(section.get("content")):
            return jsonify(error="Every retail section must contain text"), 422
    target = extracted_path(relative, pending=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    info = {"status": "pending", "submitted_by": current_user()["username"], "submitted_at": now_iso()}
    target.with_name(target.name + ".meta.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return jsonify(ok=True, pending=info, message="Retail edits saved for business approval")


@app.get("/api/data/steps/asset")
@require_auth
def step_asset():
    if "steps" not in current_user()["roles"]:
        return jsonify(error="You do not have access to this data area"), 403
    relative = request.args.get("path", "")
    _, path = safe_target("steps", relative)
    staged = pending_path("steps", relative)
    # SECURITY FIX: Only approvers see pending assets; everyone else sees published assets
    visible = (staged if staged.is_file() else path) if current_user()["approver"] else path
    return send_file(visible) if visible.is_file() else abort(404)


@app.post("/api/data/steps/asset")
@require_auth
def upload_step_asset():
    if "steps" not in current_user()["roles"]:
        return jsonify(error="You do not have access to this data area"), 403
    upload = request.files.get("file")
    workflow = request.form.get("workflow", "")
    if not upload or not workflow.lower().endswith(".json"):
        return jsonify(error="Image and workflow path are required"), 400
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return jsonify(error="Use a PNG, JPG, WEBP, or GIF image"), 400
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(upload.filename).name)
    relative = (Path(workflow).parent / filename).as_posix()
    # Validate against both roots before writing the pending image.
    safe_target("steps", relative)
    target = pending_path("steps", relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    upload.save(target)
    logger.info("STEP_IMAGE_STAGED workflow=%s image=%s user=%s", workflow, filename, current_user()["username"])
    return jsonify(ok=True, filename=filename, message="Image added to the pending workflow")


@app.put("/api/data/<domain>/file")
@require_domain
def save_file(domain):
    root, path = safe_target(domain, request.args.get("path", ""))
    body = request.get_json(silent=True) or {}
    if path.suffix.lower() != ".json" or domain != "steps":
        return jsonify(error="Use file upload to replace spreadsheet or PDF sources"), 400
    data = body.get("data")
    if not isinstance(data, (dict, list)):
        return jsonify(error="JSON content must be an object or array"), 422
    relative = path.relative_to(root).as_posix()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=BASE_DIR, delete=False, suffix=".json") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2); temp = Path(stream.name)
    try:
        info = stage_change(domain, relative, temp)
    finally:
        temp.unlink(missing_ok=True)
    return jsonify(ok=True, pending=info, message="Saved for business approval")


@app.delete("/api/data/steps/workflow")
@require_auth
def delete_workflow():
    if "steps" not in current_user()["roles"] or not current_user()["approver"]:
        return jsonify(error="Only an assigned business approver can delete workflows"), 403
    root, path = safe_target("steps", request.args.get("path", ""))
    relative = path.relative_to(root).as_posix()
    staged = pending_path("steps", relative)
    was_published = path.is_file()
    source = path if was_published else staged
    if path.suffix.lower() != ".json" or not source.is_file():
        abort(404, "Workflow not found")
    try:
        document_data = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        document_data = None
    document_label = "FAQ" if isinstance(document_data, list) else "Tips" if isinstance(document_data, dict) and isinstance(document_data.get("tips"), list) else "Rules" if isinstance(document_data, dict) and isinstance(document_data.get("Rules"), list) else "Overview" if isinstance(document_data, dict) and any(isinstance(document_data.get(key), list) for key in ("overview", "features", "services")) else "Workflow"
    if was_published:
        backup = HISTORY_ROOT / "steps" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") / path.relative_to(root)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        save_workflow_version(relative, path, current_user()["username"], "deleted", f"{document_label} deleted")
        path.unlink()
    staged.unlink(missing_ok=True)
    staged.with_name(staged.name + ".meta.json").unlink(missing_ok=True)
    sync = delete_milvus_source("steps", relative) if was_published else {"status": "not_published", "chunks": 0}
    logger.info("STEP_DOCUMENT_DELETED type=%s source=%s user=%s", document_label, relative, current_user()["username"])
    return jsonify(ok=True, milvus=sync, message=f"{document_label} deleted"), (200 if sync["status"] != "failed" else 202)


@app.delete("/api/data/steps/topic")
@require_auth
def delete_main_topic():
    """Delete one verified top-level Steps topic and every source it contains."""
    if "steps" not in current_user()["roles"] or not current_user()["approver"]:
        return jsonify(error="Only an assigned business approver can delete main topics"), 403

    topic = clean_text(request.args.get("topic"))
    if not topic or "/" in topic or "\\" in topic or topic in {".", ".."}:
        return jsonify(error="A valid main topic is required"), 422
    root, topic_path = safe_target("steps", topic)
    staged_topic = pending_path("steps", topic)
    if topic_path.parent != root or (not topic_path.is_dir() and not staged_topic.is_dir()):
        abort(404, "Main topic not found")

    actor = current_user()["username"]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    sources = [path for path in topic_path.rglob("*") if path.is_file()] if topic_path.is_dir() else []
    workflow_sources = [path for path in sources if path.suffix.lower() == ".json"]
    sync_results = []

    # Preserve a complete audit copy before removing anything from the live data folder.
    for source in sources:
        relative_path = source.relative_to(root)
        backup = HISTORY_ROOT / "steps" / timestamp / relative_path
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)

    for workflow in workflow_sources:
        relative = workflow.relative_to(root).as_posix()
        save_workflow_version(relative, workflow, actor, "deleted", "Main topic deleted")
        sync_results.append(delete_milvus_source("steps", relative))
        staged = pending_path("steps", relative)
        staged.unlink(missing_ok=True)
        staged.with_name(staged.name + ".meta.json").unlink(missing_ok=True)

    # topic_path was verified above as exactly one direct child of the Steps root.
    if topic_path.is_dir():
        shutil.rmtree(topic_path)
    if staged_topic.is_dir():
        shutil.rmtree(staged_topic)

    labels = topic_label_map()
    if topic in labels:
        labels.pop(topic, None)
        TOPIC_LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOPIC_LABELS_PATH.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [result for result in sync_results if result["status"] == "failed"]
    logger.info("MAIN_TOPIC_DELETED topic=%s workflows=%d files=%d user=%s", topic, len(workflow_sources), len(sources), actor)
    message = f"Main topic deleted ({len(workflow_sources)} workflows removed)"
    if failed:
        message += "; some entries could not be removed from the AI knowledge base"
    return jsonify(ok=True, milvus=sync_results, topic_labels=labels, message=message), (202 if failed else 200)


@app.put("/api/data/steps/topic")
@require_auth
def rename_main_topic():
    """Rename a top-level Steps topic while preserving all nested content."""
    if "steps" not in current_user()["roles"] or not current_user()["approver"]:
        return jsonify(error="Only an assigned business approver can rename main topics"), 403
    payload = request.get_json(silent=True) or {}
    topic = clean_text(payload.get("topic"))
    new_title = clean_text(payload.get("title"))
    if not topic or not new_title or any(re.search(r'[<>:"/\\|?*]', value) for value in (topic, new_title)) or topic in {".", ".."} or new_title in {".", ".."}:
        return jsonify(error="Valid current and new topic names are required"), 422
    root, topic_path = safe_target("steps", topic)
    _, destination = safe_target("steps", new_title)
    staged_topic = pending_path("steps", topic)
    staged_destination = pending_path("steps", new_title)
    if topic_path.parent != root or destination.parent != root or (not topic_path.is_dir() and not staged_topic.is_dir()):
        abort(404, "Main topic not found")
    if destination.exists() or staged_destination.exists():
        return jsonify(error="A main topic with this name already exists"), 409
    approved_relatives = [path.relative_to(root).as_posix() for path in topic_path.rglob("*.json")] if topic_path.is_dir() else []
    if topic_path.is_dir():
        topic_path.rename(destination)
    if staged_topic.is_dir():
        staged_topic.rename(staged_destination)
    labels = topic_label_map()
    if topic in labels:
        labels[new_title] = labels.pop(topic)
        TOPIC_LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOPIC_LABELS_PATH.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    sync_results = []
    for old_relative in approved_relatives:
        suffix = Path(old_relative).relative_to(topic).as_posix()
        new_relative = (Path(new_title) / suffix).as_posix()
        delete_milvus_source("steps", old_relative)
        new_source = destination / suffix
        if new_source.is_file():
            _, _, sync = audit_and_sync_approved_data("steps", new_source, new_relative, current_user()["username"])
            sync_results.append(sync)
    logger.info("STEP_TOPIC_RENAMED old=%s new=%s user=%s", topic, new_title, current_user()["username"])
    failed = [result for result in sync_results if result.get("status") == "failed"]
    return jsonify(ok=True, title=new_title, topic_labels=labels, message="Main topic renamed", milvus=sync_results), (202 if failed else 200)


@app.get("/api/history/steps")
@require_auth
def workflow_history():
    if "steps" not in current_user()["roles"]:
        return jsonify(error="You do not have access to workflow history"), 403
    versions = []
    root = VERSIONS_ROOT / "steps"
    for meta_path in root.glob("*/*/meta.json") if root.exists() else []:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            source = meta_path.parent / "source.json"
            data = json.loads(source.read_text(encoding="utf-8-sig"))
            meta["title"] = (clean_text(data.get("title")) or Path(meta["source_path"]).parent.name) if isinstance(data, dict) else Path(meta["source_path"]).stem
            if can_view_history_version(meta):
                add_history_action_state(meta)
                versions.append(meta)
        except (OSError, json.JSONDecodeError):
            continue
    return jsonify(versions=sorted(versions, key=lambda item: item["created_at"], reverse=True))


def revoke_history_approval(domain: str, version: dict[str, Any], source: Path) -> tuple[Any, int]:
    """Keep business content live while withdrawing it from approved connected systems."""
    relative = version["source_path"]
    actor = current_user()["username"]
    if domain == "steps":
        _, live = safe_target(domain, relative)
        if not live.is_file():
            return jsonify(error="The approved workflow source is no longer available"), 404
        save_workflow_version(relative, live, actor, "approval_revoked", "Business approval revoked", actor)
    else:
        approved = extracted_path(relative)
        data_path = approved if approved.is_file() else source
        if not data_path.is_file():
            return jsonify(error="The approved Retail source is no longer available"), 404
        data = json.loads(data_path.read_text(encoding="utf-8-sig"))
        save_retail_version(relative, data, actor, "approval_revoked", "Business approval revoked", actor)
    sync = delete_milvus_source(domain, relative, drop_if_empty=True)
    record_milvus_event(domain, relative, actor, sync, 0, "approval_revoked")
    logger.info("APPROVAL_REVOKED domain=%s source=%s user=%s milvus=%s dropped=%s", domain, relative, actor, sync["status"], sync.get("collection_dropped", False))
    if sync["status"] == "failed":
        message = "Approval revoked in Data Studio, but removing the source from Milvus failed."
        return jsonify(ok=True, approved=False, message=message, milvus=sync), 202
    collection_note = " The empty Milvus collection was dropped." if sync.get("collection_dropped") else " Its vectors were removed from Milvus."
    return jsonify(ok=True, approved=False, message="Approval revoked." + collection_note, milvus=sync), 200


@app.get("/api/history/steps/version")
@require_auth
def get_workflow_version():
    if "steps" not in current_user()["roles"]:
        return jsonify(error="You do not have access to workflow history"), 403
    folder = version_folder(request.args.get("id", ""))
    if not (folder / "meta.json").is_file() or not (folder / "source.json").is_file():
        abort(404, "Version not found")
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    if not can_view_history_version(meta):
        return jsonify(error="You can only view your own history"), 403
    before_meta, before = previous_version("steps", meta)
    return jsonify(meta=meta, data=json.loads((folder / "source.json").read_text(encoding="utf-8-sig")), before_meta=before_meta, before=before)


@app.post("/api/history/steps/restore")
@require_auth
def restore_workflow_version():
    if "steps" not in current_user()["roles"]:
        return jsonify(error="You do not have access to workflow history"), 403
    version_id = (request.get_json(silent=True) or {}).get("id", "")
    folder = version_folder(version_id)
    source, meta_path = folder / "source.json", folder / "meta.json"
    if not source.is_file() or not meta_path.is_file():
        abort(404, "Version not found")
    version = json.loads(meta_path.read_text(encoding="utf-8"))
    if not can_view_history_version(version):
        return jsonify(error="You can only restore your own history"), 403
    if not history_action_available(version):
        return jsonify(error="This history action expired after 48 hours. The record remains available for viewing only."), 409
    if history_undo_kind(version) == "revoke_approval":
        return revoke_history_approval("steps", version, source)
    relative = version["source_path"]
    info = stage_change("steps", relative, source)
    info["restored_from"] = version_id
    pending = pending_path("steps", relative)
    pending.with_name(pending.name + ".meta.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

    # A restore selects an already-audited historical version. Authorized users can
    # publish it directly; requiring another content approval creates a redundant loop.
    actor = current_user()["username"]
    result = publish_and_sync_generic("steps", relative, actor)
    logger.info("HISTORY_RESTORE_PUBLISHED domain=steps source=%s user=%s milvus=%s", relative, actor, result["sync"]["status"])
    message = "Version restored and published everywhere." if result["sync"]["status"] == "synced" else f"Version restored in Data Studio, but Milvus synchronization is {result['sync']['status']}."
    return jsonify(ok=True, pending=None, published=True, message=message, milvus=result["sync"]), result["status_code"]


def ensure_retail_version_history():
    """Create an original-PDF baseline for retail data approved before versioning existed."""
    root = EXTRACTED_ROOT / "retail"
    if not root.exists():
        return
    for approved in root.rglob("*.pdf.extracted.json"):
        relative = approved.relative_to(root).as_posix().removesuffix(".extracted.json")
        source_key = hashlib.sha256(relative.encode()).hexdigest()[:12]
        if list((VERSIONS_ROOT / "retail" / source_key).glob("*/meta.json")):
            continue
        try:
            _, pdf = safe_target("retail", relative)
            current = json.loads(approved.read_text(encoding="utf-8-sig"))
            if pdf.is_file():
                original = {"title": pdf.stem, "sections": pdf_chunks(pdf)}
                save_retail_version(relative, original, "system", "baseline", "Original PDF extraction")
                if original != current:
                    save_retail_version(relative, current, "system", "approved", retail_change_summary(original, current))
            else:
                save_retail_version(relative, current, "system", "baseline", "Existing approved retail data")
        except (OSError, json.JSONDecodeError):
            logger.exception("RETAIL_HISTORY_MIGRATION_FAILED source=%s", relative)


def ensure_retail_deletion_history():
    """Backfill deletion events for backed-up PDFs that no longer exist live."""
    history_root = HISTORY_ROOT / "retail"
    if not history_root.exists():
        return
    deleted_candidates: dict[str, Path] = {}
    for backup in history_root.rglob("*.pdf"):
        backup_relative = backup.relative_to(history_root)
        if len(backup_relative.parts) < 2:
            continue
        relative = Path(*backup_relative.parts[1:]).as_posix()
        _, live_pdf = safe_target("retail", relative)
        if not live_pdf.is_file() and (relative not in deleted_candidates or backup.stat().st_mtime > deleted_candidates[relative].stat().st_mtime):
            deleted_candidates[relative] = backup
    for relative, backup in deleted_candidates.items():
        source_key = hashlib.sha256(relative.encode()).hexdigest()[:12]
        metadata = list((VERSIONS_ROOT / "retail" / source_key).glob("*/meta.json"))
        try:
            deleted_meta = next((path for path in metadata if json.loads(path.read_text(encoding="utf-8")).get("action") == "deleted"), None)
            backup_reference = backup.relative_to(HISTORY_ROOT).as_posix()
            if deleted_meta:
                stored = json.loads(deleted_meta.read_text(encoding="utf-8"))
                if not stored.get("deleted_pdf_backup"):
                    stored["deleted_pdf_backup"] = backup_reference
                    deleted_meta.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
                continue
            sources = [(path.parent / "source.json") for path in metadata if (path.parent / "source.json").is_file()]
            data = json.loads(max(sources, key=lambda path: path.stat().st_mtime).read_text(encoding="utf-8-sig")) if sources else {"title": backup.stem, "sections": pdf_chunks(backup)}
            saved = save_retail_version(relative, data, "system", "deleted", "Retail PDF deleted (recovered from backup)")
            saved["deleted_pdf_backup"] = backup_reference
            deletion_meta = retail_version_folder(saved["id"]) / "meta.json"
            deletion_meta.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            logger.exception("RETAIL_DELETION_HISTORY_MIGRATION_FAILED source=%s", relative)


@app.get("/api/history/retail")
@require_auth
def retail_history():
    if "retail" not in current_user()["roles"]:
        return jsonify(error="You do not have access to retail history"), 403
    versions = []
    root = VERSIONS_ROOT / "retail"
    for meta_path in root.glob("*/*/meta.json") if root.exists() else []:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            data = json.loads((meta_path.parent / "source.json").read_text(encoding="utf-8-sig"))
            meta["domain"] = "retail"
            meta["title"] = clean_text(data.get("title")) or Path(meta["source_path"]).stem
            if can_view_history_version(meta):
                add_history_action_state(meta)
                versions.append(meta)
        except (OSError, json.JSONDecodeError):
            continue
    return jsonify(versions=sorted(versions, key=lambda item: item["created_at"], reverse=True))


@app.get("/api/history/retail/version")
@require_auth
def get_retail_version():
    if "retail" not in current_user()["roles"]:
        return jsonify(error="You do not have access to retail history"), 403
    folder = retail_version_folder(request.args.get("id", ""))
    if not (folder / "meta.json").is_file() or not (folder / "source.json").is_file():
        abort(404, "Version not found")
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    if not can_view_history_version(meta):
        return jsonify(error="You can only view your own history"), 403
    before_meta, before = previous_version("retail", meta)
    return jsonify(meta=meta, data=json.loads((folder / "source.json").read_text(encoding="utf-8-sig")), before_meta=before_meta, before=before)


@app.post("/api/history/retail/restore")
@require_auth
def restore_retail_version():
    if "retail" not in current_user()["roles"]:
        return jsonify(error="You do not have access to retail history"), 403
    version_id = (request.get_json(silent=True) or {}).get("id", "")
    folder = retail_version_folder(version_id)
    source, meta_path = folder / "source.json", folder / "meta.json"
    if not source.is_file() or not meta_path.is_file():
        abort(404, "Version not found")
    version = json.loads(meta_path.read_text(encoding="utf-8"))
    if not can_view_history_version(version):
        return jsonify(error="You can only restore your own history"), 403
    if not history_action_available(version):
        return jsonify(error="This history action expired after 48 hours. The record remains available for viewing only."), 409
    if history_undo_kind(version) == "revoke_approval":
        return revoke_history_approval("retail", version, source)
    if version.get("action") == "deleted":
        backup_root = HISTORY_ROOT.resolve()
        pdf_backup = (backup_root / str(version.get("deleted_pdf_backup", "")).replace("/", os.sep)).resolve()
        if backup_root not in pdf_backup.parents or pdf_backup.suffix.lower() not in {".pdf", ".product"} or not pdf_backup.is_file():
            return jsonify(error="The original retail product backup for this deletion is unavailable"), 404
        staged_pdf = pending_path("retail", version["source_path"])
        staged_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_backup, staged_pdf)
    relative = version["source_path"]
    target = extracted_path(relative, pending=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    info = {"status": "pending", "submitted_by": current_user()["username"], "submitted_at": now_iso(), "restored_from": version_id}
    target.with_name(target.name + ".meta.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

    actor = current_user()["username"]
    result = publish_and_sync_retail(relative, actor)
    if not result:
        return jsonify(error="The selected Retail version could not be restored"), 500
    logger.info("HISTORY_RESTORE_PUBLISHED domain=retail source=%s user=%s milvus=%s", relative, actor, result["sync"]["status"])
    message = "Version restored and published everywhere." if result["sync"]["status"] == "synced" else f"Version restored in Data Studio, but Milvus synchronization is {result['sync']['status']}."
    return jsonify(ok=True, pending=None, published=True, message=message, milvus=result["sync"]), result["status_code"]


@app.delete("/api/data/retail/pdf")
@require_auth
def delete_retail_pdf():
    if "retail" not in current_user()["roles"] or not current_user()["approver"]:
        return jsonify(error="Only an assigned business approver can delete retail products"), 403
    root, pdf = safe_target("retail", request.args.get("path", ""))
    if pdf.suffix.lower() not in {".pdf", ".product"} or not pdf.is_file():
        abort(404, "Retail product not found")
    sync = delete_retail_product_source(root, pdf)
    return jsonify(ok=True, milvus=sync, message="Retail product deleted"), (200 if sync["status"] != "failed" else 202)


def delete_retail_product_source(root: Path, pdf: Path) -> dict[str, Any]:
    """Delete one Retail source while preserving its recoverable history entry."""
    relative = pdf.relative_to(root).as_posix()
    approved_extraction = extracted_path(relative)
    if approved_extraction.is_file():
        deletion_data = json.loads(approved_extraction.read_text(encoding="utf-8-sig"))
    else:
        deletion_data = {"title": pdf.stem, "sections": pdf_chunks(pdf) if pdf.suffix.lower() == ".pdf" else []}
    backup = HISTORY_ROOT / "retail" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") / pdf.relative_to(root)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf, backup)
    saved = save_retail_version(relative, deletion_data, current_user()["username"], "deleted", "Retail product deleted")
    saved["deleted_pdf_backup"] = backup.relative_to(HISTORY_ROOT).as_posix()
    deletion_meta = retail_version_folder(saved["id"]) / "meta.json"
    deletion_meta.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    pdf.unlink()
    for candidate in (pending_path("retail", relative), extracted_path(relative), extracted_path(relative, pending=True), structured_retail_path(relative)):
        candidate.unlink(missing_ok=True)
        candidate.with_name(candidate.name + ".meta.json").unlink(missing_ok=True)
    sync = delete_milvus_source("retail", relative)
    logger.info("RETAIL_PRODUCT_DELETED source=%s user=%s", relative, current_user()["username"])
    return sync


@app.delete("/api/data/retail/collection")
@require_auth
def delete_retail_collection():
    if "retail" not in current_user()["roles"] or not current_user()["approver"]:
        return jsonify(error="Only an assigned business approver can delete retail collections"), 403
    relative = request.args.get("path", "").strip("/\\")
    root, folder = safe_target("retail", relative)
    if not relative or not folder.is_dir():
        abort(404, "Retail collection not found")
    sources = [path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in DOMAINS["retail"]["extensions"]]
    sources.extend(path for path in root.iterdir() if path.is_file() and path.suffix.lower() in DOMAINS["retail"]["extensions"] and path.stem.strip().casefold() == folder.name.strip().casefold())
    if not sources:
        abort(404, "Retail collection has no products")
    sync_results = [delete_retail_product_source(root, source) for source in sources]
    try:
        folder.rmdir()
    except OSError:
        pass
    roles = retail_collection_roles()
    roles.pop(relative, None)
    RETAIL_COLLECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RETAIL_COLLECTIONS_PATH.write_text(json.dumps(roles, ensure_ascii=False, indent=2), encoding="utf-8")
    labels = retail_collection_labels()
    labels.pop(relative, None)
    RETAIL_COLLECTION_LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RETAIL_COLLECTION_LABELS_PATH.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [result for result in sync_results if result.get("status") == "failed"]
    logger.info("RETAIL_COLLECTION_DELETED collection=%s products=%d user=%s", relative, len(sources), current_user()["username"])
    return jsonify(ok=True, message="Retail collection deleted", deleted=len(sources), milvus=sync_results), (202 if failed else 200)


@app.put("/api/data/retail/collection")
@require_auth
def rename_retail_collection():
    if "retail" not in current_user()["roles"]:
        return jsonify(error="You do not have access to this data area"), 403
    payload = request.get_json(silent=True) or {}
    relative = clean_text(payload.get("path")).strip("/\\")
    title = clean_text(payload.get("title"))
    _, folder = safe_target("retail", relative)
    if not relative or not folder.is_dir():
        abort(404, "Retail collection not found")
    if not title:
        return jsonify(error="Collection name is required"), 422
    labels = retail_collection_labels()
    labels[relative] = title
    RETAIL_COLLECTION_LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RETAIL_COLLECTION_LABELS_PATH.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("RETAIL_COLLECTION_RENAMED collection=%s title=%s user=%s", relative, title, current_user()["username"])
    return jsonify(ok=True, title=title, collection_labels=labels, message="Collection renamed")


@app.post("/api/data/retail/product")
@require_auth
def create_retail_product():
    if "retail" not in current_user()["roles"]:
        return jsonify(error="You do not have access to this data area"), 403
    payload = request.get_json(silent=True) or {}
    title = clean_text(payload.get("title"))
    parent = clean_text(payload.get("parent")).strip("/\\")
    existing_role = clean_text(payload.get("existing_role")).lower()
    if not title:
        return jsonify(error="Product name is required"), 422
    filename = re.sub(r'[^A-Za-z0-9 _-]+', '', title).strip().replace(' ', '_') or "new_product"
    if parent and (Path(parent).name != parent or parent in {".", ".."}):
        return jsonify(error="Invalid product group"), 422
    if existing_role and existing_role not in {"general", "product"}:
        return jsonify(error="Choose whether the existing data is general information or a product"), 422
    relative = f"{parent}/{filename}.product" if parent else f"{filename}.product"
    _, marker = safe_target("retail", relative)
    approved = extracted_path(relative)
    if marker.exists() or approved.exists():
        return jsonify(error="A retail product with this name already exists"), 409
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"type": "retail_product", "title": title}, ensure_ascii=False, indent=2), encoding="utf-8")
    approved.parent.mkdir(parents=True, exist_ok=True)
    approved.write_text(json.dumps({"title": title, "native_product": True, "sections": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    document = normalize_retail_data({"title": title, "source": {"type": "business"}, "sections": []})
    structured = structured_retail_path(relative)
    structured.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    if parent and existing_role:
        roles = retail_collection_roles()
        roles[parent] = existing_role
        RETAIL_COLLECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RETAIL_COLLECTIONS_PATH.write_text(json.dumps(roles, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("RETAIL_PRODUCT_CREATED source=%s user=%s", relative, current_user()["username"])
    return jsonify(ok=True, path=relative, message="New retail product created")


@app.post("/api/data/<domain>/upload")
@require_domain
def upload_file(domain):
    upload = request.files.get("file")
    relative = request.form.get("path") or (upload.filename if upload else "")
    root, path = safe_target(domain, relative)
    if not upload or path.suffix.lower() not in DOMAINS[domain]["extensions"]:
        return jsonify(error="Unsupported or missing file"), 400
    relative = path.relative_to(root).as_posix()
    with tempfile.NamedTemporaryFile("wb", dir=BASE_DIR, delete=False, suffix=path.suffix) as stream:
        upload.save(stream); temp = Path(stream.name)
    try:
        info = stage_change(domain, relative, temp)
    finally:
        temp.unlink(missing_ok=True)
    return jsonify(ok=True, pending=info, message="Uploaded for business approval")


@app.post("/api/data/<domain>/approve")
@require_domain
def approve(domain):
    if not current_user()["approver"]:
        return jsonify(error="Only an assigned business approver can validate data"), 403
    relative = (request.get_json(silent=True) or {}).get("path", "")
    actor = current_user()["username"]
    if domain == "steps":
        ok_flag, error, status_code = finalize_staged_steps_workflow(relative, actor)
        if not ok_flag:
            if status_code == 404:
                abort(404, error)
            return jsonify(error=error), status_code
    if domain == "retail":
        result = publish_and_sync_retail(relative, actor, defer_sync=True)
        if result:
            logger.info("CHANGE_APPROVED domain=%s source=%s approver=%s milvus=%s", domain, relative, actor, result["sync"]["status"])
            message = "Retail data approved"
            return jsonify(ok=True, message=message, chunks=result["chunks"], audit=result["audit"], milvus=result["sync"]), result["status_code"]
    result = publish_and_sync_generic(domain, relative, actor, defer_sync=True)
    logger.info("CHANGE_APPROVED domain=%s source=%s approver=%s milvus=%s", domain, relative, actor, result["sync"]["status"])
    message = result["message"] if current_user()["admin"] else "Workflow approved."
    return jsonify(ok=True, message=message, chunks=result["chunks"], audit=result["audit"], milvus=result["sync"]), result["status_code"]


def all_collections() -> list[dict[str, Any]]:
    """Every real Milvus collection Data Studio can write to, with the dim it should hold."""
    retail_dim = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
    product_labels = {"cards": "Retail products · Cards", "loans": "Retail products · Loans", "deposits": "Retail products · Deposits"}
    entries = [{"domain": "steps", "product": None, "label": DOMAINS["steps"]["label"], "collection": DOMAINS["steps"]["collection"], "expected_dim": STEPS_EMBEDDING_DIM}]
    for key, collection in RETAIL_COLLECTIONS.items():
        entries.append({"domain": "retail", "product": key, "label": product_labels[key], "collection": collection, "expected_dim": retail_dim})
    return entries


def collection_health(client, collection: str, expected_dim: int) -> dict[str, Any]:
    """Row count and stored embedding dimension for one collection - the same check check_milvus_ingestion.py does at the CLI."""
    if not client.has_collection(collection):
        return {"exists": False}
    try:
        info = client.describe_collection(collection)
        dim = None
        for field in info.get("fields", []):
            if field.get("name") == "embedding":
                dim = (field.get("params") or {}).get("dim")
                break
        stats = client.get_collection_stats(collection)
        row_count = int(stats.get("row_count", 0))
        dim = int(dim) if dim is not None else None
        return {"exists": True, "row_count": row_count, "dimension": dim, "dimension_mismatch": dim is not None and dim != expected_dim}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def server_collection_inventory(client) -> list[dict[str, Any]]:
    """Read a compact overview of every collection visible on the Milvus server."""
    managed = {entry["collection"] for entry in all_collections()}
    collections = []
    for name in sorted(client.list_collections(), key=str.casefold):
        entry: dict[str, Any] = {"name": name, "managed": name in managed}
        try:
            description = client.describe_collection(name)
            fields = description.get("fields", [])
            vectors = []
            primary_field = None
            for field in fields:
                params = field.get("params") or {}
                if params.get("dim") is not None:
                    vectors.append({"field": field.get("name", "vector"), "dimension": int(params["dim"])})
                if field.get("is_primary"):
                    primary_field = field.get("name")
            stats = client.get_collection_stats(name)
            entry.update({"row_count": int(stats.get("row_count", 0)), "field_count": len(fields), "primary_field": primary_field, "vectors": vectors})
            try:
                load = client.get_load_state(collection_name=name)
                state = load.get("state") if isinstance(load, dict) else load
                entry["load_state"] = getattr(state, "name", str(state)).replace("LoadState", "").strip(" .:_") or "Unknown"
            except Exception:
                entry["load_state"] = "Unknown"
        except Exception as exc:
            entry["error"] = clean_text(exc)
        collections.append(entry)
    return collections


@app.get("/api/milvus/status")
@require_auth
def milvus_status():
    if not current_user()["milvus_admin"]:
        return jsonify(error="Milvus administration is restricted to authorized administrators"), 403
    events = milvus_events()
    entries = all_collections()
    enabled = os.getenv("MILVUS_SYNC_ENABLED", "true").lower() == "true"
    connected = False
    connection_error = None
    server_collections = []
    try:
        client = milvus_client()
        server_collections = server_collection_inventory(client)
        connected = True
        for entry in entries:
            entry.update(collection_health(client, entry["collection"], entry["expected_dim"]))
    except Exception as exc:
        connection_error = clean_text(exc)
        for entry in entries:
            entry["health_error"] = connection_error
    return jsonify(
        enabled=enabled,
        connected=connected,
        connection_error=connection_error,
        host=MILVUS_HOST,
        port=MILVUS_PORT,
        collections=entries,
        server_collections=server_collections,
        initial_ingest_complete=any(event.get("action") == "initial_ingest" and event.get("status") == "synced" for event in events),
        chunk_inventory=milvus_chunk_inventory(current_user()),
        background_jobs=recent_sync_jobs(),
        events=events,
    )


@app.post("/api/milvus/ingest-selected")
@require_auth
def milvus_ingest_selected():
    """Let a Milvus admin ingest explicit approved or pending chunk sets."""
    user = current_user()
    if not user["milvus_admin"]:
        return jsonify(error="Milvus administration is restricted to authorized administrators"), 403
    if os.getenv("MILVUS_SYNC_ENABLED", "true").lower() != "true":
        return jsonify(error="Milvus synchronization is disabled. Set MILVUS_SYNC_ENABLED=true and restart the application."), 409
    selections = (request.get_json(silent=True) or {}).get("items", [])
    if not isinstance(selections, list) or not selections:
        return jsonify(error="Select at least one chunk set to ingest"), 422
    if len(selections) > 500:
        return jsonify(error="Select no more than 500 chunk sets at once"), 422
    results = []
    seen = set()
    approval_cache: dict[str, set[str]] = {}
    for selected in selections:
        domain = clean_text(selected.get("domain")) if isinstance(selected, dict) else ""
        relative = clean_text(selected.get("path")) if isinstance(selected, dict) else ""
        version = clean_text(selected.get("version")) if isinstance(selected, dict) else ""
        key = (domain, relative, version)
        if key in seen:
            continue
        seen.add(key)
        try:
            source, chunks, resolved = milvus_candidate(domain, relative, version)
            audit = save_audit(domain, relative, chunks, user["username"])
            sync = sync_milvus(domain, relative, chunks, source)
            approval_cache.setdefault(domain, explicitly_approved_sources(domain))
            is_business_approved = relative in approval_cache[domain] and resolved != "pending"
            action = "admin_selected_ingest" if is_business_approved else "admin_override_unapproved"
            record_milvus_event(domain, relative, user["username"], sync, len(chunks), action)
            results.append({"domain": domain, "source_path": relative, "version": resolved, "business_approved": is_business_approved, "chunks": len(chunks), "audit": str(audit), "status": sync.get("status"), "error": sync.get("error")})
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            results.append({"domain": domain, "source_path": relative, "version": version, "chunks": 0, "status": "failed", "error": clean_text(exc)})
    failed = sum(result["status"] != "synced" for result in results)
    logger.warning("MILVUS_ADMIN_SELECTED_INGEST user=%s sources=%d failed=%d unapproved_overrides=%d", user["username"], len(results), failed, sum(not result.get("business_approved", False) for result in results))
    return jsonify(ok=failed == 0, sources=len(results), failed=failed, results=results), (200 if failed == 0 else 202)


@app.delete("/api/milvus/collection")
@require_auth
def milvus_delete_collection():
    """Drop one explicitly named collection after admin confirmation in the UI."""
    user = current_user()
    if not user["milvus_admin"]:
        return jsonify(error="Milvus administration is restricted to authorized administrators"), 403
    name = clean_text(request.args.get("name"))
    if not name:
        return jsonify(error="Collection name is required"), 422
    try:
        client = milvus_client()
        available = set(client.list_collections())
        if name not in available:
            return jsonify(error="Collection not found on this Milvus server"), 404
        stats = client.get_collection_stats(name)
        row_count = int(stats.get("row_count", 0))
        client.drop_collection(name)
        sync = {"status": "deleted", "collection": name}
        record_milvus_event("system", name, user["username"], sync, row_count, "collection_deleted")
        logger.warning("MILVUS_COLLECTION_DELETED collection=%s rows=%d user=%s", name, row_count, user["username"])
        return jsonify(ok=True, collection=name, rows=row_count, message=f'Collection "{name}" deleted')
    except Exception as exc:
        logger.exception("MILVUS_COLLECTION_DELETE_FAILED collection=%s user=%s error=%s", name, user["username"], exc)
        return jsonify(error=str(exc)), 500


@app.post("/api/milvus/recreate-collection")
@require_auth
def milvus_recreate_collection():
    user = current_user()
    if not user["milvus_admin"]:
        return jsonify(error="Milvus administration is restricted to authorized administrators"), 403
    if os.getenv("MILVUS_SYNC_ENABLED", "true").lower() != "true":
        return jsonify(error="Milvus synchronization is disabled. Set MILVUS_SYNC_ENABLED=true and restart the application."), 409
    payload = request.get_json(silent=True) or {}
    collection = clean_text(payload.get("collection"))
    known = {entry["collection"]: entry for entry in all_collections()}
    entry = known.get(collection)
    if not entry:
        return jsonify(error="Unknown collection"), 400
    try:
        client = milvus_client()
        if client.has_collection(collection):
            client.drop_collection(collection)
        if entry["domain"] == "steps":
            ensure_steps_collection(client)
        else:
            ensure_retail_collection(client, collection, entry["expected_dim"])
        results = []
        domain = entry["domain"]
        if domain in user["roles"]:
            root, _ = safe_target(domain)
            for item in list_files(domain):
                if item.get("pending") or item.get("new"):
                    continue
                relative = item["path"]
                if resolve_collection(domain, relative) != collection:
                    continue
                path = (root / relative.replace("/", os.sep)).resolve()
                if not path.is_file():
                    continue
                chunks, _, sync = audit_and_sync_approved_data(domain, path, relative, user["username"], "collection_recreate_ingest")
                results.append({"source_path": relative, "chunks": len(chunks), "status": sync.get("status"), "error": sync.get("error")})
        failed = sum(result["status"] != "synced" for result in results)
        logger.warning("MILVUS_COLLECTION_RECREATED collection=%s sources=%d failed=%d user=%s", collection, len(results), failed, user["username"])
        message = f"{collection} recreated and {len(results) - failed} approved source{'s' if len(results) - failed != 1 else ''} ingested."
        if failed:
            message += f" {failed} source{'s' if failed != 1 else ''} failed."
        return jsonify(ok=failed == 0, collection=collection, sources=len(results), failed=failed, results=results, message=message), (200 if failed == 0 else 202)
    except Exception as exc:
        logger.exception("MILVUS_COLLECTION_RECREATE_FAILED collection=%s error=%s", collection, exc)
        return jsonify(error=str(exc)), 500


@app.post("/api/milvus/ingest-all")
@require_auth
def milvus_ingest_all():
    user = current_user()
    if not user["milvus_admin"]:
        return jsonify(error="Milvus administration is restricted to authorized administrators"), 403
    if os.getenv("MILVUS_SYNC_ENABLED", "true").lower() != "true":
        return jsonify(error="Milvus synchronization is disabled. Set MILVUS_SYNC_ENABLED=true and restart the application."), 409
    results = []
    for domain in user["roles"]:
        if domain not in DOMAINS:
            continue
        root, _ = safe_target(domain)
        for item in list_files(domain):
            if item.get("pending") or item.get("new"):
                continue
            relative = item["path"]
            path = (root / relative.replace("/", os.sep)).resolve()
            if not path.is_file():
                continue
            chunks, _, sync = audit_and_sync_approved_data(domain, path, relative, user["username"], "initial_ingest")
            results.append({"domain": domain, "source_path": relative, "chunks": len(chunks), "status": sync.get("status"), "error": sync.get("error")})
    failed = sum(result["status"] != "synced" for result in results)
    logger.info("MILVUS_INITIAL_INGEST user=%s sources=%d failed=%d", user["username"], len(results), failed)
    return jsonify(ok=failed == 0, sources=len(results), failed=failed, results=results), (200 if failed == 0 else 202)


@app.post("/api/data/<domain>/import")
@require_domain
def import_data(domain):
    if not current_user()["approver"]:
        return jsonify(error="Only an assigned business approver can import data"), 403
    root, path = safe_target(domain, (request.get_json(silent=True) or {}).get("path", ""))
    if not path.is_file():
        abort(404, "Validated source not found")
    relative = path.relative_to(root).as_posix()
    if pending_path(domain, relative).is_file() or (domain == "retail" and extracted_path(relative, pending=True).is_file()):
        return jsonify(error="Approve the pending changes before importing data"), 409
    chunks, audit, sync = audit_and_sync_approved_data(domain, path, relative, current_user()["username"], "manual_import")
    return jsonify(ok=True, chunks=len(chunks), audit=str(audit), milvus=sync), (200 if sync["status"] != "failed" else 202)


if __name__ == "__main__":
    host = os.getenv("DEV_HOST", "127.0.0.1") if DEV_MODE else os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("DEV_PORT", "4173") if DEV_MODE else os.getenv("PORT", "4173"))
    if DEV_MODE:
        logger.warning("DEV_MODE_ACTIVE ldap=disabled bind=%s:%d local_data=%s production_use=forbidden", host, port, DATA_ROOT)
    logger.info("APPLICATION_START service=capital-data-studio host=%s port=%d dev_mode=%s auth_enabled=%s data_root=%s audit_root=%s milvus=%s:%d roles=%s", host, port, DEV_MODE, AUTH_ENABLED, DATA_ROOT, AUDIT_ROOT, MILVUS_HOST, MILVUS_PORT, sorted(USER_ROLES))
    for domain, config in DOMAINS.items():
        logger.info("DOMAIN_CONFIG domain=%s root=%s collection=%s", domain, config["root"], config["collection"])
    ensure_retail_structured_files()
    start_sync_worker()
    app.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
