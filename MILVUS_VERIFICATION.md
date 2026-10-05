# Verifying Milvus ingestion for Steps & workflows

## What I found reviewing the code and logs (22 Aug 2026)

- **Sync is currently switched off.** `.env` has `MILVUS_SYNC_ENABLED=false`. As long as this
  is `false`, approving a workflow still succeeds, but `sync_milvus()` returns
  `{"status": "disabled", ...}` immediately and nothing is sent to Milvus.
- **No sync has actually run recently.** The last ~3 days of `.studio/server-output.log`
  (Aug 19-22) contain zero `CHANGE_APPROVED`, `MILVUS_*`, or `EMBEDDING_*` log lines, meaning
  no workflow has gone through the approve flow in that window. The pipeline hasn't been
  exercised end to end lately, disabled flag aside.
- **A possible vector-dimension mismatch.** `.env` sets `STEPS_EMBEDDING_DIMENSIONS=512` and
  `EMBEDDING_DIMENSIONS=1024`, but `app.py` never reads either variable - the vector size
  actually sent to Milvus is whatever `EMBEDDING_MODEL` (`intfloat/multilingual-e5-large`,
  which produces 1024-dim vectors) outputs at run time. If the `steps_postlogin` collection
  already exists in Milvus with a schema created by a different/older pipeline at 512
  dimensions, every insert will fail with a dimension error the moment sync is turned on.
  This is the single biggest risk to rule out before flipping the flag - `check_milvus_ingestion.py`
  (below) checks for exactly this.
- **Port mismatch worth double-checking.** `app.py`'s built-in default is `MILVUS_PORT=19530`
  (Milvus's standard gRPC port), but `.env` overrides it to `19531`. That may well be correct
  for your deployment (e.g. a proxy or non-standard port mapping) - just confirm with
  whoever manages the Milvus instance that `19531` is intentional.
- **A failed sync does not block approval.** In `approve()`, if `sync_milvus()` returns
  `"failed"`, the workflow is still approved and published locally; the API just returns
  HTTP 202 instead of 200 with a warning message, shown to the approver as a toast/notice.
  There's no retry and no separate alert - if sync silently keeps failing, the only place
  that shows up is the server log and that one-time notice. Worth deciding whether that's
  the behavior you want, or whether a failed sync should be surfaced more persistently
  (e.g. a "needs re-sync" indicator) - I didn't change this without checking with you first,
  since it changes approval semantics.
- **The core sync logic itself looks correct**: it re-embeds all chunks for the source,
  deletes any existing Milvus rows for that exact `source_path` first, then inserts the new
  ones - so re-approving a workflow won't leave stale/duplicate vectors behind, and deleting
  a workflow correctly removes its vectors too (`delete_milvus_source`).

## Why I couldn't test this myself

`MILVUS_HOST` (`10.0.30.51`) is a private network address. Neither this cloud session nor
your local device sandbox has network access to it, so I could not open a live connection
to confirm any of this directly - the findings above are from reading the code and logs only.

## How to verify it, step by step

1. **Run the diagnostic script from a machine that can reach Milvus** - your office network
   or wherever the app itself normally runs, not this session. It's `check_milvus_ingestion.py`,
   delivered alongside this checklist. It only needs `pymilvus`, `sentence-transformers`, and
   `python-dotenv` (already in `requirements.txt`) and does **not** touch the running app.

   ```
   pip install -r requirements.txt
   python check_milvus_ingestion.py
   ```

   It will, read-only:
   - confirm it can connect to `MILVUS_HOST:MILVUS_PORT`
   - load the embedding model and report the real vector dimension it produces
   - check whether the `steps` and `retail` collections exist, and if so, compare their
     stored vector dimension against the model's real output (this is the dimension-mismatch
     check above)
   - flag the unused `EMBEDDING_DIMENSIONS`/`STEPS_EMBEDDING_DIMENSIONS` variables as a reminder

   If you want it to also do a real write test (insert one throwaway row, then delete it
   immediately), add `--write-test`. This is opt-in and off by default since it does modify
   the collection, even though it cleans up after itself.

2. **Fix anything the script flags as `FAIL`** before proceeding - especially a dimension
   mismatch, which needs a decision (recreate the collection vs. adjust the model/config)
   rather than a code change on its own.

3. **Do one real end-to-end test in a non-production setting**, e.g. your dev/local instance
   with a throwaway test workflow:
   - Set `MILVUS_SYNC_ENABLED=true` in that instance's `.env` and restart it.
   - Create or pick a test workflow under Steps, submit it, and approve it as a business
     approver.
   - Watch `.studio/server-output.log` for this sequence (all present = success):
     ```
     IMPORT_STARTED domain=steps source=<path> ...
     AUDIT_SAVED domain=steps source=<path> ...
     EMBEDDING_START domain=steps source=<path> ...
     EMBEDDING_COMPLETE domain=steps source=<path> ...
     MILVUS_CONNECTED host=... port=... collection=...
     MILVUS_OLD_SOURCE_DELETED collection=... source=<path>
     MILVUS_SYNC_COMPLETE collection=... source=<path> chunks=<n>
     CHANGE_APPROVED domain=steps source=<path> approver=... milvus=synced
     ```
     A `MILVUS_SYNC_FAILED` line means something is still wrong - the exception message on
     that line is the actual cause.
   - Confirm the latest matching chunk file was written under
     `data_chunks/steps/<source path>/chunks.json` (this always happens regardless of Milvus;
     approval history is maintained separately under `.studio/versions`).
   - If you have read access to Milvus separately (e.g. Attu, or `pymilvus` directly), query
     the collection for that `source_path` and confirm the row count matches the number of
     chunks logged.
   - Delete that test workflow afterwards and confirm its vectors are removed too
     (`MILVUS_DELETE_FAILED` would show up in the log if not).

4. **Only once that's clean, enable it in the environment(s) that matter** by setting
   `MILVUS_SYNC_ENABLED=true` there and restarting the app.

## Open questions for you / your infra team

- Is `MILVUS_PORT=19531` intentional, or should it be the default `19530`?
- Does the `steps_postlogin` collection already exist in Milvus from an earlier pipeline?
  If so, what dimension was it created with - the diagnostic script will tell you, but it's
  worth checking with whoever set it up originally.
- Do you want a failed sync after approval to be more visible than today's one-time toast
  (e.g., a persistent "not yet in the knowledge base" badge on that workflow)? I can build
  that once sync itself is confirmed working, if wanted.
