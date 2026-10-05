# ClaimLens

Retrieval-augmented health insurance claim checker with verified citations. The LLM structures the case, extracts cited rule values and writes prose. **Code** computes the payout (Decimal, policy deduction order), verifies every citation, and abstains for human review when it cannot ground an answer.

```
description -> LLM structure -> hybrid retrieval (BM25 [+dense], RRF, IRDAI rules dated) -> LLM extract rules w/ clause ids
            -> verify (clause retrieved AND value literally in clause) -> engine (eligibility, non-payables, room rent on
            associated costs only, sub-limit, deductible+co-pay, sum insured) -> explain (checked) | abstain
```

## Run
```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Linux/mac: .venv/bin/pip
export ANTHROPIC_API_KEY=...            # env only, never committed
.venv/Scripts/uvicorn app.main:app      # API on :8000
cd ../frontend && npm install && npm run dev   # UI on :3000, proxies /api to :8000
```
Put `GEMINI_API_KEY=...` (default model `gemini-3.1-flash-lite`, free tier) or `ANTHROPIC_API_KEY` in `backend/.env` (gitignored). Provider is chosen automatically, or set `CLAIMLENS_PROVIDER`.
No key? `python eval/demo_server.py` serves a canned demo (paper's room-rent example, select `starcare-gold`).

## Using it
Pick a sample policy or drop your own PDF, Word (.docx) or text file (5 MB max, text-based, not scanned). Uploads live in memory for an hour and are never written to disk. Answer the plain-language questions and read the estimate; if the policy is unclear the app says so instead of guessing.

## Test / evaluate
```bash
cd backend
.venv/Scripts/python -m pytest -q          # 39 tests: engine, verifier, pipeline, API hardening
.venv/Scripts/python eval/run_eval.py      # 7 hand-solved scenarios, oracle extraction (engine+verifier)
.venv/Scripts/python eval/run_eval.py --live   # real Claude extraction, needs key
.venv/Scripts/python -m pip_audit -r requirements.txt
```

## Security design
- Strict Pydantic request model (`extra=forbid`, numeric/length/date bounds, no NaN); policy id is looked up, never used as a path.
- Untrusted text only inside `<claim_description>`; LLM output is schema-validated, then each cited clause id and value is checked against retrieved text in code. The LLM cannot change money or the verdict. Prose with unknown citations or invented amounts is replaced by a template.
- 16 KB body cap (also chunked), per-IP and global rate limits, CORS allow-list, security headers, no stack traces or upstream errors to clients, docs off unless `CLAIMLENS_DEBUG=1`.
- Pinned deps, `pip-audit` and `npm audit` clean, non-root Docker image.

## Limits (honest)
- Corpus is synthetic sample wording plus **paraphrased** IRDAI excerpts: verify against official circulars before real use. Add real policies as `backend/data/policies/*.md` (see header format in the samples).
- Not modelled yet (abstains): restoration benefit, network package rates. Skipped from the slides: OCR for bills, pgvector, cross-encoder reranker, open-weights comparison, RAGAS.
- Claude Opus 5.5 rejects `temperature`, so determinism comes from structured outputs + validation, not temperature 0.
- Behind a reverse proxy, configure trusted proxy headers so rate limiting keys on the real client IP.
- Live Gemini eval: 5/7 (`CLAIMLENS_EVAL_SLEEP=5 python eval/run_eval.py --live`). The 2 misses abstain on purpose: `securehealth-basic` has a restoration-benefit clause the engine does not model. Claude path (`ClaudeLLM`) is untested.
- Uploaded policies are split into fixed-size text chunks, so odd layouts, tables in PDFs or scanned images can read poorly; the verifier then abstains.
- Not legal or financial advice.
