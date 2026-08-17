"""Data Studio: role-aware editing, local chunk audit, and Milvus synchronization."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, redirect, request, send_file, send_from_directory, session
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATA_ROOT = Path(os.getenv("DATA_ROOT", BASE_DIR / "Data")).resolve()
AUDIT_ROOT = Path(os.getenv("AUDIT_ROOT", BASE_DIR / "data_chunks")).resolve()
HISTORY_ROOT = Path(os.getenv("HISTORY_ROOT", BASE_DIR / ".studio" / "history")).resolve()
MILVUS_HOST = os.getenv("MILVUS_HOST", "10.0.30.51")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"

DOMAINS = {
    "steps": {
        "label": "Steps & workflows", "root": DATA_ROOT / "Steps", "extensions": {".json"},
        "collection": os.getenv("STEPS_COLLECTION", "mobile_steps"),
    },
    "locations": {
        "label": "ATMs & branches", "root": DATA_ROOT / "ATMs_Branches", "extensions": {".xlsx", ".xls", ".csv"},
        "collection": os.getenv("LOCATIONS_COLLECTION", "atm_branches"),
    },
    "retail": {
        "label": "Retail products", "root": DATA_ROOT, "extensions": {".pdf"},
        "collection": os.getenv("RETAIL_COLLECTION", "retail_products"),
    },
}

app = Flask(__name__, static_folder="public", static_url_path="")
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", MAX_CONTENT_LENGTH=50 * 1024 * 1024)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json_env(name: str, default: Any) -> Any:
    try:
        return json.loads(os.getenv(name, "")) if os.getenv(name) else default
    except json.JSONDecodeError:
        return default


# Production: configure LDAP_* and USER_ROLES_JSON. Local passwords are only a safe development fallback.
USER_ROLES = {str(k).lower(): v for k, v in load_json_env("USER_ROLES_JSON", {
    "steps.admin": ["steps"], "locations.admin": ["locations"], "retail.admin": ["retail"], "data.admin": ["steps", "locations", "retail"]
}).items()}
DEV_PASSWORDS = load_json_env("DEV_USERS_JSON", {})


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
    return {"username": username, "roles": session.get("roles", [])}


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
    if not root.exists():
        return []
    items = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in DOMAINS[domain]["extensions"]:
            continue
        # Retail PDFs live at Data root except the Steps training-material copy.
        if domain == "retail" and "Steps" in path.relative_to(root).parts:
            continue
        stat = path.stat()
        items.append({"path": path.relative_to(root).as_posix(), "name": path.name, "size": stat.st_size, "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
    return sorted(items, key=lambda x: x["path"].lower())


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


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
                chunks.append({"topic": topic[:64], "section": clean_text(trail[-1] if trail else "workflow")[:64], "variant": "note" if "Note" in value else "step", "section_title": heading[:512], "content": text, "language": "ar" if len(re.findall(r"[\u0600-\u06ff]", text)) > len(re.findall(r"[A-Za-z]", text)) else "en", "images": own_images})
            for key, child in value.items():
                if key not in {"text", "image", "topic", "title", "step", "Note"}:
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
    import fitz
    result = []
    with fitz.open(path) as doc:
        for page_number, page in enumerate(doc, 1):
            text = page.get_text("text").strip()
            for number, block in enumerate(re.split(r"\n\s*\n", text)):
                block = clean_text(block)
                if len(block) < 20:
                    continue
                result.append({"topic": path.stem[:64], "section": f"page_{page_number}", "variant": "content", "section_title": f"{path.name} > Page {page_number} > Part {number + 1}", "content": block[:65000], "language": "ar" if re.search(r"[\u0600-\u06ff]", block) else "en", "images": []})
    return result


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


def save_audit(domain: str, relative: str, chunks: list[dict[str, Any]], user: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    source_key = hashlib.sha256(relative.encode()).hexdigest()[:12]
    folder = AUDIT_ROOT / domain / source_key / stamp
    folder.mkdir(parents=True, exist_ok=False)
    payload = {"audit": {"source_path": relative, "domain": domain, "chunked_at": now_iso(), "chunked_by": user, "chunk_count": len(chunks), "milvus_host": MILVUS_HOST, "collection": DOMAINS[domain]["collection"]}, "chunks": chunks}
    (folder / "chunks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (folder / "chunks.jsonl").open("w", encoding="utf-8") as stream:
        for chunk in chunks:
            stream.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    return folder


def sync_milvus(domain: str, relative: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    if os.getenv("MILVUS_SYNC_ENABLED", "true").lower() != "true":
        return {"status": "disabled", "host": MILVUS_HOST}
    try:
        from pymilvus import MilvusClient, DataType
        from sentence_transformers import SentenceTransformer
        model_name = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
        model = SentenceTransformer(model_name, device=os.getenv("EMBEDDING_DEVICE", "cpu"))
        vectors = model.encode(["passage: " + c["content"] for c in chunks], normalize_embeddings=True).tolist() if chunks else []
        client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}", token=os.getenv("MILVUS_TOKEN") or None)
        collection = DOMAINS[domain]["collection"]
        if not client.has_collection(collection):
            schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
            schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=len(vectors[0]) if vectors else 1024)
            schema.add_field("source_path", DataType.VARCHAR, max_length=2048)
            schema.add_field("section_title", DataType.VARCHAR, max_length=1024)
            schema.add_field("content", DataType.VARCHAR, max_length=65535)
            schema.add_field("metadata", DataType.JSON)
            index = client.prepare_index_params().add_index("embedding", index_type="HNSW", metric_type="COSINE", params={"M": 16, "efConstruction": 128})
            client.create_collection(collection, schema=schema, index_params=index)
        escaped = relative.replace("\\", "\\\\").replace('"', '\\"')
        client.delete(collection_name=collection, filter=f'source_path == "{escaped}"')
        if chunks:
            client.insert(collection_name=collection, data=[{"chunk_id": c["chunk_id"], "embedding": vector, "source_path": relative, "section_title": c["section_title"], "content": c["content"], "metadata": {k: v for k, v in c.items() if k not in {"content", "section_title", "chunk_id"}}} for c, vector in zip(chunks, vectors)])
        return {"status": "synced", "host": MILVUS_HOST, "collection": collection, "chunks": len(chunks)}
    except Exception as exc:
        return {"status": "failed", "host": MILVUS_HOST, "collection": DOMAINS[domain]["collection"], "error": str(exc)}


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html") if current_user() else redirect("/login.html")


@app.post("/api/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    username, password = clean_text(body.get("username")), str(body.get("password", ""))
    roles = USER_ROLES.get(username.lower(), [])
    if not username or not password or not roles or not ldap_login(username, password):
        return jsonify(error="Invalid credentials or no assigned data access"), 401
    session.clear(); session.update(username=username, roles=roles)
    return jsonify(user=current_user())


@app.post("/api/auth/logout")
def logout():
    session.clear(); return jsonify(ok=True)


@app.get("/api/auth/me")
@require_auth
def me():
    return jsonify(user=current_user(), domains={key: {"label": value["label"], "collection": value["collection"]} for key, value in DOMAINS.items() if key in current_user()["roles"]}, milvus={"host": MILVUS_HOST, "port": MILVUS_PORT})


@app.get("/api/data/<domain>/files")
@require_domain
def files(domain):
    return jsonify(files=list_files(domain))


@app.get("/api/data/<domain>/file")
@require_domain
def get_file(domain):
    root, path = safe_target(domain, request.args.get("path", ""))
    if not path.is_file(): abort(404)
    if path.suffix.lower() == ".json":
        return jsonify(path=path.relative_to(root).as_posix(), data=json.loads(path.read_text(encoding="utf-8-sig")))
    return send_file(path, as_attachment=False)


@app.get("/api/data/steps/asset")
@require_auth
def step_asset():
    if "steps" not in current_user()["roles"]:
        return jsonify(error="You do not have access to this data area"), 403
    _, path = safe_target("steps", request.args.get("path", ""))
    return send_file(path) if path.is_file() else abort(404)


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
    if path.exists():
        backup = HISTORY_ROOT / domain / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") / path.relative_to(root)
        backup.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2); temp = Path(stream.name)
    temp.replace(path)
    relative = path.relative_to(root).as_posix(); chunks = make_chunks(domain, path, relative)
    audit = save_audit(domain, relative, chunks, current_user()["username"]); sync = sync_milvus(domain, relative, chunks)
    status = 200 if sync["status"] != "failed" else 202
    return jsonify(ok=True, chunks=len(chunks), audit=str(audit), milvus=sync), status


@app.post("/api/data/<domain>/upload")
@require_domain
def upload_file(domain):
    upload = request.files.get("file")
    relative = request.form.get("path") or (upload.filename if upload else "")
    root, path = safe_target(domain, relative)
    if not upload or path.suffix.lower() not in DOMAINS[domain]["extensions"]:
        return jsonify(error="Unsupported or missing file"), 400
    if path.exists():
        backup = HISTORY_ROOT / domain / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") / path.relative_to(root)
        backup.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True); upload.save(path)
    relative = path.relative_to(root).as_posix(); chunks = make_chunks(domain, path, relative)
    audit = save_audit(domain, relative, chunks, current_user()["username"]); sync = sync_milvus(domain, relative, chunks)
    return jsonify(ok=True, chunks=len(chunks), audit=str(audit), milvus=sync), (200 if sync["status"] != "failed" else 202)


@app.post("/api/data/<domain>/reindex")
@require_domain
def reindex(domain):
    root, path = safe_target(domain, (request.get_json(silent=True) or {}).get("path", ""))
    relative = path.relative_to(root).as_posix(); chunks = make_chunks(domain, path, relative)
    audit = save_audit(domain, relative, chunks, current_user()["username"]); sync = sync_milvus(domain, relative, chunks)
    return jsonify(ok=True, chunks=len(chunks), audit=str(audit), milvus=sync), (200 if sync["status"] != "failed" else 202)


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "4173")), debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
