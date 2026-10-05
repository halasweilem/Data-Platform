#!/usr/bin/env python3
"""
Standalone Milvus ingestion diagnostic for Capital Data Studio.

Run this from a machine/network that can actually reach MILVUS_HOST:MILVUS_PORT
(this cannot be verified from an environment without network access to that host).
It does NOT touch the Data Platform UI app or its running server - it only reads
your .env file for connection settings and talks to Milvus + the embedding model
directly, the same way app.py's sync_milvus() does.

Usage:
    python check_milvus_ingestion.py                 # read-only checks (safe, default)
    python check_milvus_ingestion.py --write-test     # also does a real insert+delete
                                                       # round trip using a throwaway
                                                       # chunk_id/source_path, then
                                                       # removes it immediately after.

Exit code is 0 if every check passed, 1 otherwise, so this can be wired into a
CI/ops pipeline if useful.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

FAILS: list[str] = []
WARNS: list[str] = []


def ok(message: str) -> None:
    print(f"  [OK]   {message}")


def warn(message: str) -> None:
    WARNS.append(message)
    print(f"  [WARN] {message}")


def fail(message: str) -> None:
    FAILS.append(message)
    print(f"  [FAIL] {message}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write-test", action="store_true", help="Also perform a real insert+delete round trip (cleans up after itself).")
    parser.add_argument("--env-file", default=str(Path(__file__).resolve().parent / ".env"), help="Path to the .env file to read settings from (default: .env next to this script).")
    args = parser.parse_args()

    section("Loading configuration")
    try:
        from dotenv import load_dotenv
    except ImportError:
        fail("python-dotenv is not installed. Run: pip install -r requirements.txt")
        return 1
    env_path = Path(args.env_file)
    if env_path.is_file():
        load_dotenv(env_path)
        ok(f"Loaded settings from {env_path}")
    else:
        warn(f"No .env file found at {env_path} - relying on already-exported environment variables.")

    milvus_host = os.getenv("MILVUS_HOST", "10.0.30.51")
    milvus_port = int(os.getenv("MILVUS_PORT", "19530"))
    milvus_token = os.getenv("MILVUS_TOKEN") or None
    sync_enabled = os.getenv("MILVUS_SYNC_ENABLED", "true").lower() == "true"
    embedding_model = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
    embedding_device = os.getenv("EMBEDDING_DEVICE", "cpu")
    steps_collection = os.getenv("STEPS_COLLECTION", "mobile_steps")
    retail_collection = os.getenv("RETAIL_COLLECTION", "retail_products")

    print(f"  MILVUS_HOST            = {milvus_host}")
    print(f"  MILVUS_PORT             = {milvus_port}")
    print(f"  MILVUS_TOKEN             = {'(set)' if milvus_token else '(empty)'}")
    print(f"  MILVUS_SYNC_ENABLED      = {sync_enabled}")
    print(f"  EMBEDDING_MODEL          = {embedding_model}")
    print(f"  EMBEDDING_DEVICE         = {embedding_device}")
    print(f"  STEPS_COLLECTION         = {steps_collection}")
    print(f"  RETAIL_COLLECTION        = {retail_collection}")

    if not sync_enabled:
        warn("MILVUS_SYNC_ENABLED=false - the running app will NOT sync approvals to Milvus right now, "
             "even if every check below passes. This script checks whether Milvus itself is reachable "
             "and correctly configured; flipping the flag on is a separate, deliberate step (see the checklist).")

    for unused_var in ("EMBEDDING_DIMENSIONS", "STEPS_EMBEDDING_DIMENSIONS"):
        if os.getenv(unused_var):
            warn(f"{unused_var} is set in .env but app.py never reads it - the vector dimension actually used is "
                 "whatever the embedding model produces at run time. This variable is currently just documentation, "
                 "not a safety check, so a mismatch here would not be caught automatically.")

    section("Connecting to Milvus")
    try:
        from pymilvus import MilvusClient, DataType
    except ImportError:
        fail("pymilvus is not installed. Run: pip install -r requirements.txt")
        return 1

    try:
        started = time.perf_counter()
        client = MilvusClient(uri=f"http://{milvus_host}:{milvus_port}", token=milvus_token, timeout=10)
        client.list_collections()
        ok(f"Connected to {milvus_host}:{milvus_port} in {round((time.perf_counter() - started) * 1000)} ms")
    except Exception as exc:
        fail(f"Could not connect to Milvus at {milvus_host}:{milvus_port} - {exc}")
        print("\nThis usually means either (a) this machine cannot reach that host/port on the network "
              "(firewall, VPN, wrong subnet), or (b) Milvus is not running / not listening on that port. "
              "Nothing further can be checked until this connects.")
        return 1

    section("Loading the embedding model (to determine the real vector dimension)")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        fail("sentence-transformers is not installed. Run: pip install -r requirements.txt")
        return 1
    try:
        started = time.perf_counter()
        model = SentenceTransformer(embedding_model, device=embedding_device)
        sample_vector = model.encode(["passage: connectivity test"], normalize_embeddings=True)[0].tolist()
        real_dim = len(sample_vector)
        ok(f"Loaded {embedding_model} in {round((time.perf_counter() - started) * 1000)} ms - produces {real_dim}-dimension vectors")
    except Exception as exc:
        fail(f"Could not load embedding model '{embedding_model}' - {exc}")
        return 1

    section("Checking collections")
    for domain, collection in (("steps", steps_collection), ("retail", retail_collection)):
        print(f"\n  Domain: {domain}  ->  collection '{collection}'")
        try:
            exists = client.has_collection(collection)
        except Exception as exc:
            fail(f"    Could not check whether collection '{collection}' exists - {exc}")
            continue
        if not exists:
            warn(f"    Collection '{collection}' does not exist yet. It will be auto-created on the first "
                 f"approval in this domain, using {real_dim}-dimension vectors (from {embedding_model}). "
                 "This is expected and fine on a fresh setup.")
            continue
        try:
            info = client.describe_collection(collection)
            stats = client.get_collection_stats(collection)
            embedding_field = next((f for f in info.get("fields", []) if f.get("name") == "embedding"), None)
            existing_dim = None
            if embedding_field:
                existing_dim = (embedding_field.get("params") or {}).get("dim")
            row_count = stats.get("row_count")
            ok(f"    Collection exists - {row_count} rows currently indexed")
            if existing_dim is not None:
                if int(existing_dim) == real_dim:
                    ok(f"    Embedding dimension matches: collection={existing_dim}, model={real_dim}")
                else:
                    fail(f"    DIMENSION MISMATCH: collection '{collection}' was created with {existing_dim}-dim vectors, "
                         f"but {embedding_model} now produces {real_dim}-dim vectors. Every insert will fail until "
                         "either the collection is recreated (drops existing vectors for this domain) or the "
                         "embedding model/config is changed back to match.")
            else:
                warn("    Could not read the embedding field's dimension from the collection schema.")
        except Exception as exc:
            fail(f"    Could not read schema/stats for '{collection}' - {exc}")

    if args.write_test:
        section("Write round-trip test (insert + delete a throwaway row)")
        test_collection = steps_collection
        test_source = "__milvus_ingestion_test__"
        test_chunk_id = "milvus_ingestion_test_chunk"
        try:
            if not client.has_collection(test_collection):
                schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
                schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
                schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=real_dim)
                schema.add_field("source_path", DataType.VARCHAR, max_length=2048)
                schema.add_field("section_title", DataType.VARCHAR, max_length=1024)
                schema.add_field("content", DataType.VARCHAR, max_length=65535)
                schema.add_field("metadata", DataType.JSON)
                index = client.prepare_index_params().add_index("embedding", index_type="HNSW", metric_type="COSINE", params={"M": 16, "efConstruction": 128})
                client.create_collection(test_collection, schema=schema, index_params=index)
                ok(f"Created collection '{test_collection}' (did not exist before this run)")
            client.delete(collection_name=test_collection, filter=f'source_path == "{test_source}"')
            client.insert(collection_name=test_collection, data=[{
                "chunk_id": test_chunk_id, "embedding": sample_vector, "source_path": test_source,
                "section_title": "Ingestion test", "content": "This is a throwaway test row.", "metadata": {},
            }])
            ok("Inserted a throwaway test row")
            client.delete(collection_name=test_collection, filter=f'source_path == "{test_source}"')
            ok("Deleted the throwaway test row - insert/delete round trip works end to end")
        except Exception as exc:
            fail(f"Write round-trip failed - {exc}")

    section("Summary")
    if not FAILS and not WARNS:
        print("All checks passed.")
    else:
        if WARNS:
            print(f"{len(WARNS)} warning(s) - review above.")
        if FAILS:
            print(f"{len(FAILS)} failure(s) - review above. Ingestion will NOT work reliably until these are fixed.")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
