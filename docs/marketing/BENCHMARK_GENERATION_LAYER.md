# Benchmark generation layer (post-extraction)

## Goal
Extend the existing RAG app to automatically generate **benchmark Q&A** and **keyword intelligence** *after* a user uploads a code/standard.

- **Extraction is unchanged**: PDF → `clauses.csv` + `tables.csv` already works; do not modify it.
- **Source of truth is unchanged**: final answers must still be grounded **only** in `clauses.csv` and `tables.csv`.
- **Benchmark artifacts are retrieval-only**: benchmark Q&A and keyword rules are used to **improve retrieval and query understanding**, not to generate the final answer.
- **Runtime**: run Q&A generation on Modal (GPU) for speed.
- **Storage**: save benchmark outputs in Supabase per `user_id` + `code_id`.

## Where it fits

### Current flow
PDF upload → extraction → `clauses.csv` + `tables.csv` → retrieval → answer

### Proposed flow
PDF upload → extraction → `clauses.csv` + `tables.csv` → Modal GPU Q&A generation → save `benchmark_qna.json` in Supabase (per user + code) → retrieval (enhanced) → answer (still clauses/tables only)

## Benchmark generation output
The post-extraction benchmark layer reads `clauses.csv` and `tables.csv`, generates candidate Q&A, strictly verifies each Q&A against source text, and emits:

- `benchmark_qna.json` (verified Q&A, primary artifact used by retrieval enhancement)
- `rejected_qna.jsonl` (rejections + reason taxonomy)
- `keyword_rules.json` (learned expansions; conservative)

## Pipeline steps (concept)

### Step 1 - Load data
Read:
- `clauses.csv`
- `tables.csv`

Process in batches; support resume; log per-row errors without failing the whole job.

### Step 2 - Clause/table understanding (Modal GPU worker)
For each clause/table, extract structured meaning:

```json
{
  "source_id": "...",
  "intent": "requirement | permission | prohibition | definition",
  "key_terms": ["..."],
  "technical_terms": ["..."],
  "numeric_values": ["..."],
  "conditions": ["..."],
  "exceptions": ["..."]
}
```

This is not used as truth; it’s used to guide what questions to generate and what keywords to expand.

### Step 3 - Question generation (Modal GPU worker)
For each clause/table, generate multiple question “views”:

- direct question
- natural user question
- keyword-poor question
- keyword-rich question
- trap / misleading question

Each Q&A candidate includes:

```json
{
  "question": "...",
  "expanded_keywords": ["..."],
  "missing_keywords": ["..."],
  "must_retrieve": ["source_id"],
  "expected_answer": "...",
  "citation_snippet": "verbatim text from the clause/table"
}
```

### Step 4 - Verification (mandatory)
Use strict verifier checks (deterministic + LLM) and reject Q&A if:

- answer not directly supported by the source text
- citation snippet does not prove the answer
- wrong clause/table referenced
- invented values or invented conditions
- keyword expansion changes meaning/scope
- trap question incorrectly treated as valid

Only keep verified Q&A.

### Step 5 - Save output
Save verified output as `benchmark_qna.json` and persist it to Supabase with `user_id` and `code_id`.
Keep optional diagnostic files (`rejected_qna.jsonl`, `keyword_rules.json`) for debugging/analytics.

Verified Q&A shape:

```json
{
  "id": "...",
  "dataset_id": "...",
  "source_type": "clause | table",
  "source_id": "...",
  "question_type": "direct | natural | keyword_poor | keyword_rich | trap",
  "question": "...",
  "expanded_keywords": ["..."],
  "missing_keywords": ["..."],
  "must_retrieve": ["..."],
  "expected_answer": "...",
  "citation_snippet": "...",
  "verified": true
}
```

### Step 6 - Keyword learning
Create `keyword_rules.json` with patterns like:

```json
{
  "user_phrase": "breaker overload",
  "add_keywords": ["circuit breaker", "protective device", "rating", "overcurrent"],
  "confidence": 0.8,
  "source_ids": ["..."]
}
```

### Step 7 - Integrate with existing RAG (enhance, do not replace)
Do not replace the current retrieval logic; enhance it:

Before retrieval:
- load `benchmark_qna.json` for the active `user_id` + `code_id` from Supabase
- match user query against verified Q&A (high precision)
- infer likely `source_id` hints (prefer/boost, not hard filter)
- expand query conservatively using `keyword_rules`
- pass enriched queries to existing retrieval (multi-query friendly)

## Why this works
- **Hard separation**: benchmark intelligence improves retrieval without becoming an answer source.
- **Fast generation path**: Modal GPU reduces Q&A generation latency after upload.
- **Multi-tenant safety**: Supabase scoping by user + code keeps artifacts isolated.
- **Verification step is central**, not optional.
- Trap questions create an adversarial signal to harden verification and keyword expansion safety.

## Risks and guardrails

### Risk 1 - Heading-only sources produce junk Q&A
If some CSV rows contain titles/headings rather than normative clause text, the generator either:
- wastes compute (everything rejected), or
- leaks low-quality Q&A if verification is too lenient.

**Guardrail**: add a strict pre-check before generating:
- minimum text length
- presence of normative language (shall/must/may/prohibited/required) and/or numeric tokens (where relevant)

### Risk 2 - Keyword expansion changes meaning (scope drift)
Adding “close” keywords can shift retrieval into adjacent topics (e.g., overload → short-circuit).

**Guardrails**:
- treat keyword_rules as **conditional** expansions with a confidence threshold
- tie each rule to `source_ids` and only apply when a benchmark match (or other strong signal) is present
- preserve original query and add expanded variants (don’t overwrite)

### Risk 3 - Generator/verifier collapse (model agrees with itself)
Using the same local model for generation and verification can create systematic blind spots.

**Guardrails**:
- if possible, use two different local models (generator vs verifier)
- otherwise, keep verifier prompt adversarial with a hard checklist and strict rejection bias

### Risk 4 - Q&A explosion and cost
Five question types per source scales quickly.

**Guardrails**:
- cap Q&A per clause/table; prioritize high-value sources (numbers, exceptions, normative “shall/must”)
- batch and resume; log yield rate

## Keep it strict (high impact)

### 1) Make `citation_snippet` deterministic
Require `citation_snippet` to be a **verbatim substring** of the source text.

Deterministic rule:
- if `citation_snippet` is not found in the source text → reject (no LLM needed)

### 2) Capture a support span
Store offsets for the snippet in the source text (e.g., start/end char). This makes audits and UI highlighting easy later.

### 3) Normalize IDs early
Pick one ID namespace (`anchor_id` / `clause_id` / row ID) that maps cleanly into existing retrieval/ranking hints.

### 4) Prefer minimal, provable expected answers
Use an “expected answer minimal” style: one sentence, no extra conditions unless present in the snippet.

### 5) Add a rejection taxonomy
In `rejected_qna.jsonl`, include a machine-friendly `reject_reason`, for example:

- `snippet_not_verbatim`
- `no_normative_support`
- `numeric_invented`
- `wrong_scope`
- `wrong_id`
- `keyword_shift`
- `ambiguous`
- `other`

## Operational notes
- **Upload contract**: for every uploaded PDF, create `clauses.csv`, `tables.csv`, and `benchmark_qna.json`
- **Execution**: trigger Q&A generation job on Modal immediately after extraction finishes
- **Persistence**: store `benchmark_qna.json` in Supabase keyed by `user_id` + `code_id` (+ version/timestamp)
- **Resume support**: persist progress (last processed row + input CSV hash)
- **Batching**: batch by count and consider table size
- **Error handling**: per-clause failure should not stop the job
- **Observability**: track verified/total yield; sharp drops indicate dataset mismatch or low-quality source text

## Success criteria
- Retrieval improves (more relevant clauses/tables in top ranks) without introducing new hallucination risk.
- Final answers remain grounded only in `clauses.csv`/`tables.csv`.
- Benchmark artifacts stay conservative and verifiable.

