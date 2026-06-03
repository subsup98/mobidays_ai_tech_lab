# AI Usage

## Tools Used

- ChatGPT/Codex was used for architecture discussion, implementation planning, code generation, and review.
- The generated code and design decisions were manually reviewed and adjusted during implementation.

## Major AI-Assisted Decisions

- Selected SQLite over DuckDB for local reproducibility, status updates, and simple submission execution.
- Selected Streamlit for the local dashboard.
- Selected Gemini Free Tier as the real LLM path, with mock fallback for reproducibility.
- Decided to process mp3 directly through STT and diarization while keeping transcript JSON as the canonical intermediate format.
- Split `action_item_id` and `dedup_key` responsibilities.
- Split `llm_confidence` and `validation_score`.
- Generated issue keywords after extraction instead of asking the LLM to tag everything.

## Manual Review and Corrections

- Corrected the DB choice from an earlier DuckDB suggestion to SQLite.
- Refined `dedup_key` to avoid both duplicate drift and over-merge risk:
  `meeting_id + assignee_normalized + category + due_date + normalized_task_signature`.
- Pinned STT dependencies after import testing:
  `numpy<2`, `torch==2.3.1`, `torchaudio==2.3.1`.
- Checked Community-1 as an alternate diarization model, then kept pyannote 3.1 as the default because Community-1 requires a newer pyannote stack that is less stable for this Windows PoC.
- Kept real LLM usage optional and preserved mock fallback.
- Added submission planning and module-level checklists/worklogs for traceability.

## Prompting Context

AI was given the project requirements, evaluation criteria, confirmed API-use permission, and the final architecture constraints.

Important constraints included:

- direct STT path
- speaker diarization
- Gemini Free Tier
- SQLite
- Streamlit
- structured extraction
- evidence utterance references
- confidence and validation separation
- dashboard for marketing operations

## Rejected or Changed AI Output

- A pure DuckDB design was rejected because the PoC needs local operational updates more than OLAP performance.
- A `dedup_key` based directly on raw/canonical description was changed to a normalized task signature.
- Low confidence retry was changed to review flagging, because ambiguous meeting content should not be retried into hallucinated certainty.
