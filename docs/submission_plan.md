# Submission Plan

This document defines how the final project should be packaged and checked before submission.

## Submission Target

Submit a GitHub repository link by email.

Expected email metadata:

```text
To: recruit@mobidays.com
Subject: [(주)모비데이즈_데이터AI사전과제_지원자명]
```

## Final Deliverables

| Deliverable | Location | Required |
|---|---|---|
| Code repository | GitHub | Yes |
| README | `README.md` | Yes |
| AI usage note | `AI_USAGE.md` | Yes |
| Planning document | `docs/planning.md` or PDF | Yes |
| Dashboard app | `dashboard/app.py` | Yes |
| Screen recording | external link or local mp4 | Yes |
| One-line run command | README and Makefile or equivalent | Yes |
| `.env.example` | root | Yes |

## Repository Shape

The submitted repository should expose this flow clearly:

```text
mp3
-> STT and diarization
-> transcript JSON
-> preprocessing
-> Gemini or mock extraction
-> SQLite
-> Streamlit dashboard
```

The reviewer should be able to run either:

```bash
make run
```

or:

```bash
python pipeline.py --input data/raw/sample.mp3
streamlit run dashboard/app.py
```

The exact final command must match the implemented code.

## README Checklist

README must include:

- project purpose
- architecture summary
- dependency installation
- data placement
- environment variables
- STT execution path
- Gemini real extractor path
- mock fallback path
- SQLite database location
- Streamlit run command
- troubleshooting
- known limitations

## AI_USAGE Checklist

AI_USAGE.md must include:

- AI tools used
- why each tool was used
- major prompts or task instructions
- decision points influenced by AI
- manual review or correction examples
- generated output that was rejected or changed

## Planning Document Checklist

The planning document must be concise, max 5 pages.

Recommended sections:

1. Problem definition
2. End-to-end architecture
3. Data schema and idempotency design
4. LLM extraction and validation strategy
5. Dashboard decisions, before/after impact, and failure handling

Include:

- why SQLite was selected
- why Streamlit was selected
- why Gemini Free Tier plus mock fallback was selected
- STT and diarization design
- `action_item_id` and `dedup_key` rationale
- `llm_confidence` and `validation_score` rationale
- 4-week operation and validation plan

## Dashboard Recording Checklist

Target length: 1 to 3 minutes.

Show:

- pipeline result loaded in Streamlit
- Overview metrics
- assignee backlog
- issue keywords
- confidence and review-required items
- STT Review tab
- status update loop
- Slack mock payload preview

## Final Verification

Before submitting:

- [ ] Fresh environment install succeeds
- [ ] No API keys are committed
- [ ] `.env.example` exists
- [ ] mock mode runs without external API
- [ ] Gemini mode runs with `GEMINI_API_KEY`
- [ ] STT path produces transcript JSON
- [ ] SQLite contains expected tables
- [ ] dashboard opens locally
- [ ] README commands are accurate
- [ ] AI_USAGE.md is complete
- [ ] planning document is 5 pages or less
- [ ] screen recording is accessible
- [ ] GitHub repository access is configured

## Risk Notes

- If Gemini API rate limits fail during review, mock fallback must still reproduce the dashboard.
- If STT dependencies are heavy, README must clearly separate required setup from optional acceleration.
- If raw mp3 cannot be committed, provide data placement instructions and use generated sample artifacts where allowed.
