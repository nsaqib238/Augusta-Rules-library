# File upload & Q&A — step mirror (for log checks)

Brief **design order** only: match your backend logs top-down. If a step’s fingerprint is **missing**, that segment stalled, failed, or was skipped (env/branch). Minor implementation details omitted.

**Code map:** `uploads.py` → `pdf_job_queue` → `pdf_ingest_service.run_pdf_pipeline_and_ingest_from_path` → `pdf_pipeline` (`PDFProcessor`, `OutputGenerator`) → Supabase `chunks` / `standard_tables` → embeddings → `BenchmarkGenerationService` → `sync_clause_units_after_ingest`. Chat: `chat.py` → `rag_orchestrator.run` → `clause_retrieval` + LLM steps.

---

## A. PDF upload + file-side processing + PDF Q&A (benchmark)

| # | Step (what the code does) | Log / signal to look for |
|---|---------------------------|---------------------------|
| A1 | Accept `POST /api/v1/uploads/`, auth, limits, read PDF | `Backend authenticated` / `received file` (prints), progress `received` → `reading_file` |
| A2 | Temp file, **upload to S3**, URL on `documents` | `Uploading file to S3` / `File uploaded to S3`, progress `uploaded` |
| A3 | User schema if needed, **insert `documents` row** | progress `saving_metadata` |
| A4 | Copy PDF to `_incoming`, **queue background job** | progress `processing`; HTTP returns `processing` |
| A5 | **Queue** slot (`pdf_job_queue`) | Gap before A6 = wait for concurrency |
| A6 | **Extract PDF** → normalized text / Modal tables (per settings) | `pdf_processor` steps / Modal lines |
| A7 | **Write outputs** incl. `clauses.csv`, `tables.csv` | `Generated clauses CSV`, `Generated tables CSV` |
| A8 | **Parse CSVs** → in-memory chunk/table rows | `[pdf_ingest] parsed_csv` … `clauses_csv=True` / counts |
| A9 | **Replace** prior chunks/tables/embeddings for doc (if re-ingest) | (often quiet); failures → delete warnings |
| A10 | **Insert** `chunks` + `standard_tables` | `[pdf_ingest] inserted` … `ready_for_search` |
| A11 | **Embeddings** on chunks | `Embedding generation complete` or `Auto-embeddings after PDF ingest failed` |
| A12 | **Benchmark Q&A JSON** (Modal `BENCHMARK_MODAL_ENDPOINT`) | `[benchmark_generation] pipeline` → `stored` (or `failed (non-fatal)`) |
| A13 | **Sync to `clause_units`** for RAG chat | `[pdf_ingest_rag_sync]` … `clauses_synced` / `errors` |
| A14 | **Finish** job, progress 100 | `[pdf_ingest] complete` |

**Legacy branch:** no auto pipeline → admin queue + `pending_admin_review` (steps A6–A14 do not run automatically).

**IDs for grep:** `document_id`, `job_id` (`[pdf_ingest]`), `upload_id` (early prints / `processed_files/_progress/<id>.json`).

---

## B. RAG Q&A (chat question)

**Entry:** `POST /api/v1/chat` → `rag_orchestrator.run`.

| # | Step | Log / signal |
|---|------|----------------|
| B1 | **Request** (needs non-empty `dataset_ids`) | `[Chat] request` … `dataset_ids=` |
| B2 | Normalize query; load models / topic hints | `RAG [input]`, `RAG [models]`, `RAG topic_hints loaded` |
| B3 | Optional **NCC rephrase** | `RAG [ncc_rephrase]` or rephrase warning |
| B4 | **Intent** (router LLM) | `RAG [intent]` — `Intent step failed` = hard stop |
| B5 | **Rewrite** retrieval plan (skipped on clause fast path) | `RAG [rewrite]` |
| B6 | Optional **benchmark hints** | `[benchmark_hints]` (best-effort) |
| B7 | **Retrieve** from `clause_units` (`search_db` / multi-query, or fast path `fetch_units_by_anchor_ids`) | `RAG [retrieval]`, `RAG retrieval: … candidates=`, `clause_retrieval …` |
| B8 | **Rank** candidates | `RAG [ranker]`, `RAG ranker:` |
| B9 | **Associated** clauses/tables + coverage top-ups | `RAG [associated]`, `RAG associated:`, optional `coverage_critic` |
| B10 | **Evidence filter** / cap | `RAG [evidence_filter]`, `RAG evidence_filter:`, `RAG evidence_list` |
| B11 | **Answer** (+ critic loop, optional retrieval retry) | `RAG clauses sent to answer LLM`, `RAG answer/critic:`, `RAG retry_retrieval` |
| B12 | **Format** markdown response | `RAG [formatter]`, `RAG formatter done. Total pipeline` |

**Hard failures:** `Chat failed`, `Chat request timed out`, `Intent step failed`, `Answer step failed` (exception). **`candidates=0`** = retrieval empty, not necessarily a crash.

---

## C. Other search (not chat RAG)

| API | Data |
|-----|------|
| `GET /api/v1/documents/search` | `chunks` + `textSearch` (not `clause_units`) |

---

## D. Where to read logs

| Environment | Command / place |
|---------------|------------------|
| **VPS (systemd)** | `sudo journalctl -u aus-augusta-backend --since "15 min ago" --no-pager \| grep -E '<paste patterns from tables A/B>'` |
| **Local** | Terminal running **uvicorn** (same patterns; no journalctl). |
| **Upload progress file** | `backend/processed_files/_progress/<upload_id>.json` on the API host |

**One-liner examples**

Upload + ingest: `grep -E '\[pdf_ingest\]|pdf_ingest_rag_sync|benchmark_generation|File uploaded to S3'`

RAG: `grep -E '\[Chat\]|RAG \[|clause_retrieval|Chat failed|timed out'`

If a **row in A or B has no matching log** for a given `document_id` or chat time window, that step did not run or scrolled away — re-run with a tighter `--since` or follow `-f` while reproducing.
