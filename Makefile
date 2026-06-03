PYTHON := .venv/Scripts/python.exe
STREAMLIT := .venv/Scripts/streamlit.exe
DB := data/app.db
INPUT ?= data/sample_transcript.json
INPUT_MODE ?= transcript

.PHONY: install install-stt demo dashboard run

install:
	$(PYTHON) -m pip install -r requirements.txt

install-stt:
	$(PYTHON) -m pip install -r requirements-stt.txt

demo:
	$(PYTHON) pipeline.py --input $(INPUT) --input-mode $(INPUT_MODE) --db $(DB)
	$(PYTHON) -c "from db.sqlite_client import SQLiteClient; from analytics.keywords import regenerate_issue_keywords; from integrations.slack_mock import generate_and_store_payloads; c=SQLiteClient('$(DB)'); mids=[r['meeting_id'] for r in c.fetch_all('SELECT meeting_id FROM meetings')]; [regenerate_issue_keywords(c,m) for m in mids]; [generate_and_store_payloads(c,m) for m in mids]"

dashboard:
	$(STREAMLIT) run dashboard/app.py

run: demo dashboard
