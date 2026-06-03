PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meetings (
    meeting_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    meeting_date TEXT,
    audio_path TEXT,
    audio_hash TEXT,
    transcript_path TEXT,
    source_type TEXT NOT NULL DEFAULT 'audio'
        CHECK (source_type IN ('audio', 'transcript', 'mock')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stt_runs (
    stt_run_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    audio_path TEXT NOT NULL,
    audio_hash TEXT,
    stt_model TEXT NOT NULL,
    diarization_model TEXT,
    language TEXT DEFAULT 'ko',
    duration_sec REAL,
    segment_count INTEGER NOT NULL DEFAULT 0,
    speaker_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'completed'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'fallback')),
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS participants (
    participant_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    speaker_raw TEXT NOT NULL,
    speaker_normalized TEXT,
    role TEXT,
    confidence REAL NOT NULL DEFAULT 0.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (meeting_id, speaker_raw),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS utterances (
    utterance_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    participant_id TEXT,
    speaker_raw TEXT NOT NULL,
    speaker_normalized TEXT,
    text TEXT NOT NULL,
    start_sec REAL,
    end_sec REAL,
    sequence_no INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'stt'
        CHECK (source IN ('stt', 'provided_transcript', 'mock')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (meeting_id, sequence_no),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    FOREIGN KEY (participant_id) REFERENCES participants(participant_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    topic_hint TEXT,
    start_sequence_no INTEGER,
    end_sequence_no INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chunk_utterances (
    chunk_id TEXT NOT NULL,
    utterance_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    PRIMARY KEY (chunk_id, utterance_id),
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    FOREIGN KEY (utterance_id) REFERENCES utterances(utterance_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    extraction_run_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'mock'
        CHECK (provider IN ('gemini', 'mock')),
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT 'v1',
    mode TEXT NOT NULL DEFAULT 'mock'
        CHECK (mode IN ('real', 'mock', 'fallback')),
    raw_request_json TEXT,
    raw_response_json TEXT,
    parsed_ok INTEGER NOT NULL DEFAULT 0
        CHECK (parsed_ok IN (0, 1)),
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS action_items (
    action_item_id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL,
    meeting_id TEXT NOT NULL,
    chunk_id TEXT,
    extraction_run_id TEXT,
    sequence_no INTEGER NOT NULL,
    assignee TEXT NOT NULL DEFAULT 'unassigned',
    assignee_normalized TEXT NOT NULL DEFAULT 'unassigned',
    description TEXT NOT NULL,
    normalized_task_signature TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'uncategorized',
    due_date TEXT,
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_progress', 'done', 'blocked')),
    llm_confidence REAL NOT NULL DEFAULT 0.0
        CHECK (llm_confidence >= 0.0 AND llm_confidence <= 1.0),
    validation_score REAL NOT NULL DEFAULT 0.0
        CHECK (validation_score >= 0.0 AND validation_score <= 1.0),
    final_confidence REAL NOT NULL DEFAULT 0.0
        CHECK (final_confidence >= 0.0 AND final_confidence <= 1.0),
    review_required INTEGER NOT NULL DEFAULT 0
        CHECK (review_required IN (0, 1)),
    risk_flags_json TEXT NOT NULL DEFAULT '[]',
    campaign_context TEXT,
    advertiser_context TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (meeting_id, dedup_key),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id) ON DELETE SET NULL,
    FOREIGN KEY (extraction_run_id) REFERENCES extraction_runs(extraction_run_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS action_item_sources (
    action_item_id TEXT NOT NULL,
    utterance_id TEXT NOT NULL,
    evidence_text TEXT,
    relevance_score REAL NOT NULL DEFAULT 1.0
        CHECK (relevance_score >= 0.0 AND relevance_score <= 1.0),
    PRIMARY KEY (action_item_id, utterance_id),
    FOREIGN KEY (action_item_id) REFERENCES action_items(action_item_id) ON DELETE CASCADE,
    FOREIGN KEY (utterance_id) REFERENCES utterances(utterance_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS issue_keywords (
    issue_keyword_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    keyword_type TEXT NOT NULL DEFAULT 'bigram'
        CHECK (keyword_type IN ('domain', 'bigram', 'risk_flag')),
    score REAL NOT NULL DEFAULT 0.0,
    frequency INTEGER NOT NULL DEFAULT 0,
    source_action_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (meeting_id, keyword, keyword_type),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS action_item_events (
    event_id TEXT PRIMARY KEY,
    action_item_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL
        CHECK (new_status IN ('open', 'in_progress', 'done', 'blocked')),
    changed_by TEXT NOT NULL DEFAULT 'dashboard',
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (action_item_id) REFERENCES action_items(action_item_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS slack_payloads (
    payload_id TEXT PRIMARY KEY,
    action_item_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sent_mock INTEGER NOT NULL DEFAULT 0
        CHECK (sent_mock IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (action_item_id) REFERENCES action_items(action_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stt_runs_meeting_id
    ON stt_runs(meeting_id);

CREATE INDEX IF NOT EXISTS idx_participants_meeting_id
    ON participants(meeting_id);

CREATE INDEX IF NOT EXISTS idx_utterances_meeting_sequence
    ON utterances(meeting_id, sequence_no);

CREATE INDEX IF NOT EXISTS idx_chunks_meeting_id
    ON chunks(meeting_id);

CREATE INDEX IF NOT EXISTS idx_chunk_utterances_utterance_id
    ON chunk_utterances(utterance_id);

CREATE INDEX IF NOT EXISTS idx_extraction_runs_meeting_id
    ON extraction_runs(meeting_id);

CREATE INDEX IF NOT EXISTS idx_action_items_meeting_status
    ON action_items(meeting_id, status);

CREATE INDEX IF NOT EXISTS idx_action_items_assignee_status
    ON action_items(assignee_normalized, status);

CREATE INDEX IF NOT EXISTS idx_action_items_review_required
    ON action_items(review_required, final_confidence);

CREATE INDEX IF NOT EXISTS idx_action_item_sources_utterance_id
    ON action_item_sources(utterance_id);

CREATE INDEX IF NOT EXISTS idx_issue_keywords_meeting_score
    ON issue_keywords(meeting_id, score DESC);

CREATE INDEX IF NOT EXISTS idx_action_item_events_action_item_id
    ON action_item_events(action_item_id, created_at);

CREATE INDEX IF NOT EXISTS idx_slack_payloads_action_item_id
    ON slack_payloads(action_item_id);

CREATE TABLE IF NOT EXISTS action_item_embeddings (
    action_item_id TEXT PRIMARY KEY,
    model_name     TEXT NOT NULL,
    vector         BLOB NOT NULL,
    text_input     TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (action_item_id) REFERENCES action_items(action_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_embeddings_model
    ON action_item_embeddings(model_name);

CREATE TABLE IF NOT EXISTS meeting_summaries (
    summary_id   TEXT PRIMARY KEY,
    meeting_id   TEXT NOT NULL UNIQUE,
    agenda_json  TEXT NOT NULL DEFAULT '[]',
    decisions_json TEXT NOT NULL DEFAULT '[]',
    summary_text TEXT NOT NULL DEFAULT '',
    provider     TEXT NOT NULL DEFAULT 'mock',
    model_name   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
);
