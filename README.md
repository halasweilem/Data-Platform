# Capital Data Studio

Role-aware Python UI for editing steps/workflows and managing ATM, branch, and retail source data. Every save or source replacement creates immutable timestamped chunks on the GPU/server filesystem and attempts an immediate upsert to Milvus at `10.0.30.51:19530`.

## Run

1. Create a Python 3.11+ virtual environment and install `requirements.txt`.
2. Copy `.env.example` to `.env` and load those environment variables with your service manager (or shell).
3. Configure LDAP and `USER_ROLES_JSON`. For local development only, set `LDAP_HOST=` and add test credentials to `DEV_USERS_JSON`.
4. For a local test, run `python app.py`. For production, run
   `gunicorn --bind 0.0.0.0:${PORT:-4173} --workers 2 --timeout 300 app:app`.
5. Verify the service with `curl http://127.0.0.1:${PORT:-4173}/health`.

For a UI-only local review, set `AUTH_ENABLED=false` and `MILVUS_SYNC_ENABLED=false`. The first configured username can then sign in with any non-empty password.

## Data and audit behavior

- `steps`: structured JSON editor supporting steps, notes, images, and nested data; raw JSON remains available for uncommon structures.
- `locations`: Excel/CSV files are replaced as complete sources and chunked one row at a time.
- `retail`: PDFs are replaced as complete sources and chunked into page sections.
- Every content change backs up the previous source under `.studio/history/`.
- Every chunking run writes both `chunks.json` and `chunks.jsonl` under `data_chunks/<domain>/<source-hash>/<UTC-timestamp>/` before Milvus synchronization is attempted.
- Milvus rows use deterministic chunk IDs. Existing rows for the edited source are deleted before the new chunks are inserted, avoiding stale or duplicate vectors.

The server should be placed behind HTTPS. Keep `Data`, `.studio`, and `data_chunks` on backed-up storage accessible to auditors; browsers never receive direct filesystem access.
