# Root Checklist

Update this file whenever project-wide progress changes. Each module checklist should agree with this root checklist.

## Foundation

- [x] Confirm final architecture and stack
- [x] Create project coordination docs
- [x] Define Python dependency strategy
- [x] Prepare sample data layout
- [x] Create and verify project virtual environment
- [x] Define common Pydantic models and validation helpers
- [x] Add one-command run target

## Ingestion and STT

- [x] Load mp3 input and compute stable meeting metadata
- [x] Run direct STT
- [x] Run speaker diarization
- [x] Build speaker-separated transcript JSON
- [x] Compare generated transcript with provided transcript JSON

## Preprocessing

- [x] Normalize speakers and roles
- [x] Apply ad/marketing glossary
- [x] Remove duplicate/noisy utterances
- [x] Build semantic chunks

## Extraction

- [x] Implement Gemini extractor
- [x] Implement mock extractor fallback
- [x] Enforce structured action item schema
- [x] Validate and retry schema failures
- [x] Compute `normalized_task_signature`
- [x] Compute `action_item_id` and `dedup_key`

## Storage

- [x] Create SQLite schema
- [x] Implement idempotent upsert
- [x] Store STT, extraction, action item, source, and event records

## Analytics

- [x] Generate issue keywords after action item storage
- [x] Compute confidence and validation quality views
- [x] Add optional embedding-based similar decision search
- [x] Add evaluation metrics if gold sample is available

## Integrations

- [x] Generate Slack mock payload JSON
- [x] Store generated payloads

## Dashboard

- [x] Build Overview tab
- [x] Build Action Ops tab
- [x] Build Quality tab
- [x] Build STT Review tab
- [x] Build Similar Decisions tab if time allows

## Documentation and Submission

- [x] Write README with setup and run commands
- [x] Write AI_USAGE.md
- [x] Write planning document within 5 pages
- [x] Add 4-week operation and validation plan
- [x] Verify `make run` or equivalent one-line execution
- [x] Define submission packaging plan
- [x] Add `.env.example`
- [x] Add `.gitignore` for secrets and generated artifacts
- [x] Verify mock mode works without API keys
- [x] Verify real Gemini mode works with `GEMINI_API_KEY`
- [ ] Prepare 1 to 3 minute dashboard screen recording
- [ ] Prepare final GitHub repository link and reviewer access
