param(
    [string]$InputPath = "data/sample_transcript.json",
    [string]$InputMode = "transcript",
    [string]$DatabasePath = "data/app_quality.db"
)

$ErrorActionPreference = "Stop"

.\.venv\Scripts\python.exe pipeline.py --input $InputPath --input-mode $InputMode --db $DatabasePath
.\.venv\Scripts\python.exe -c "from db.sqlite_client import SQLiteClient; from analytics.keywords import regenerate_issue_keywords; from integrations.slack_mock import generate_and_store_payloads; c=SQLiteClient('$DatabasePath'); mids=[r['meeting_id'] for r in c.fetch_all('SELECT meeting_id FROM meetings')]; [regenerate_issue_keywords(c,m) for m in mids]; [generate_and_store_payloads(c,m) for m in mids]"
.\.venv\Scripts\streamlit.exe run dashboard/app.py
