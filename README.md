# 모비데이즈 회의 액션아이템 자동화 시스템

> **한 줄 실행(환경설정 + 샘플 데이터 데모)**: `.\run.ps1`

회의 음성(mp3) 또는 transcript JSON을 입력받아 Gemini API로 액션아이템을 자동 추출하고, PostgreSQL에 적재한 뒤 Streamlit 대시보드에서 운영자가 확인·관리할 수 있는 PoC입니다.

---

## 목차

1. [아키텍처 및 데이터 흐름](#아키텍처-및-데이터-흐름)
2. [실행 방법](#실행-방법)
3. [기술 스택 선택 근거](#기술-스택-선택-근거)
4. [프롬프트 설계 근거](#프롬프트-설계-근거)
5. [가정 사항](#가정-사항)

---

## 아키텍처 및 데이터 흐름

### 전체 흐름

```
[음성 mp3 / transcript JSON]
         │
         ▼
[ingestion] audio_loader → STT (faster-whisper) + 화자분리 (pyannote 3.1)
         │  → canonical Transcript 객체
         │
         ▼
[preprocessing] 화자 정규화 → 중복 제거 → 청크화 (최대 4발화/청크, glossary 기반 topic hint)
         │
         ▼
[extraction] GeminiExtractor
         │  structured JSON (담당자·기한·카테고리·신뢰도·위험신호)
         │
         ▼
[validation] validation_score 계산 → dedup_key 중복 제거 → DB 저장
         │
         ▼
[db] PostgreSQL
         │
         ▼
[dashboard] Streamlit 7탭 + FAISS 유사도 검색
```

### 모듈 분리 기준

| 모듈 | 책임 | 분리 이유 |
|---|---|---|
| `ingestion/` | 오디오·JSON 로드, STT, 화자분리, Transcript 조립 | 입력 포맷 다양성 흡수. STT 재실행이 추출 결과에 영향을 주지 않도록 격리 |
| `preprocessing/` | 화자 정규화, 청크 생성, 도메인 용어 처리 | 청킹 정책 변경 시 extraction 재실행만 하면 되도록 단계 분리 |
| `extraction/` | Gemini LLM 호출, 스키마 검증, 재시도 | LLM 교체(Gemini→Ollama 등) 시 extractor만 교체하면 됨 |
| `db/` | PostgreSQL 스키마 초기화, upsert, 상태 변경 이력 저장 | 저장소 계층을 파이프라인·대시보드 코드에서 분리 |
| `analytics/` | 신뢰도 분석, 키워드 추출, 임베딩 | 대시보드 집계 로직을 앱 코드에서 분리해 단독 테스트 가능 |
| `integrations/` | Slack mock payload 생성 | 실제 외부 전송 없이 페이로드 구조와 운영 알림 흐름 검증 |
| `dashboard/` | Streamlit UI | 백엔드 로직과 분리해 UI 교체 여지 확보 |

### 핵심 테이블 구조

```
meetings
├── stt_runs             STT 실행 이력
├── utterances           개별 발화 원문
│   └── action_item_sources   발화 ↔ 액션 연결 (근거 추적)
├── chunks               청크 단위
├── action_items         추출된 액션아이템
│   └── action_item_events   상태 변경 이력 (append-only)
├── issue_keywords       도메인 키워드 분석 결과
└── slack_payloads       Slack Block Kit 페이로드
```

`utterances`와 `action_items`를 분리한 이유: STT를 재실행해도 추출 결과를 보존하고, `action_item_sources` 연결 테이블로 할루시네이션 탐지 및 근거 발화 표시가 가능합니다.

### 신뢰도 3-레이어

| 필드 | 의미 | 산출 방식 |
|---|---|---|
| `llm_confidence` | LLM 자체 판단 | 프롬프트 내 자기 평가 요청 |
| `validation_score` | 규칙 기반 품질 | 담당자·기한·근거발화 유무, 설명 길이 등 5개 체크 |
| `final_confidence` | 최종 신뢰도 | `min(llm_confidence, validation_score)` |

`final_confidence < 0.7` 또는 `risk_flags` 존재 시 `review_required = true`로 마킹.

---

## 실행 방법

| 모드 | 명령 | 설명 |
|---|---|---|
| 기본 데모 (STT 없음) | `.\run.ps1` | 환경설정, 패키지 설치, 샘플 transcript 처리, 대시보드 실행까지 한 번에 수행합니다. 제출 확인용 기본 경로입니다. |
| STT 음성 입력 | `.\run.ps1 -InputMode audio -InputPath "C:\path\meeting.mp3"` | 실제 음성 파일을 STT·화자분리 후 처리합니다. 오디오 모드에서만 `requirements-stt.txt`를 자동 설치합니다. |

### 빠른 테스트

Python 3.10+ 및 Git이 설치되어 있어야 합니다.

```powershell
.\run.ps1
```

최초 실행 시 `run.ps1`이 `.venv` 생성, `requirements.txt` 설치, `.env.example` 복사를 자동으로 수행합니다. 이후 샘플 transcript를 파이프라인에 넣고 대시보드를 실행합니다.

실제 Gemini 추출을 사용하려면 `.env`의 `GEMINI_API_KEY`를 채웁니다. 키가 없으면 mock extractor fallback으로 로컬 데모 확인이 가능합니다.

```env
GEMINI_API_KEY=your_key_here
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/mobidays_app
```

Git clone 후 포함되는 `data/sample_transcript.json`을 기본 입력으로 사용합니다. 이 모드는 STT와 화자분리를 생략하므로 `requirements-stt.txt`를 설치하지 않습니다.

### 실제 입력 테스트

mp3, wav, m4a, flac 파일을 직접 입력하면 `run.ps1`이 STT·화자분리 의존성을 자동 설치합니다.

`.env`에 HuggingFace 토큰과 pyannote 모델 설정을 추가합니다.

```env
HUGGINGFACE_TOKEN=your_token_here
DIARIZATION_MODEL=pyannote/speaker-diarization-3.1
```

```powershell
.\run.ps1 -InputMode audio -InputPath "C:\path\meeting.mp3"
```

회의 참여 인원 수를 알고 있을 때만 힌트를 추가합니다.

```powershell
.\run.ps1 -InputMode audio -InputPath "C:\path\meeting.mp3" -NumSpeakers 3
```

브라우저에서 `http://localhost:8501` 접속. 파이프라인 실행 후 대시보드가 열립니다.

### 파이프라인 단독 실행

**Transcript JSON 입력**

```powershell
$env:DB_BACKEND="postgres"
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/mobidays_app"
$env:LLM_PROVIDER="gemini"
$env:GEMINI_API_KEY="your_key"
.\.venv\Scripts\python.exe pipeline.py `
  --input data/interim/model_comparison_large_auto/transcript_large-v3_speakers-auto.json `
  --input-mode transcript `
  --db-backend postgres `
  --pg-dsn $env:DATABASE_URL
```

**음성 파일 입력**

```powershell
$env:DB_BACKEND="postgres"
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/mobidays_app"
$env:LLM_PROVIDER="gemini"
$env:GEMINI_API_KEY="your_key"
$env:HUGGINGFACE_TOKEN="your_token"
.\.venv\Scripts\python.exe pipeline.py `
  --input data/raw/sample.mp3 `
  --input-mode audio `
  --db-backend postgres `
  --pg-dsn $env:DATABASE_URL
```

**Gemini 실제 추출 모드**

```powershell
$env:DB_BACKEND="postgres"
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/mobidays_app"
$env:LLM_PROVIDER="gemini"
$env:GEMINI_API_KEY="your_key"
.\.venv\Scripts\python.exe pipeline.py `
  --input data/sample_transcript.json `
  --input-mode transcript `
  --db-backend postgres `
  --pg-dsn $env:DATABASE_URL
```

### 대시보드

**PostgreSQL + Vector DB 모드 (기본 제출 실행)**

```powershell
$env:DB_BACKEND="postgres"
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/mobidays_app"
$env:VECTOR_DB_PATH="data/vector/faiss_action_items"
.\.venv\Scripts\streamlit.exe run dashboard/app.py
```

**SQLite 개발용 모드**

```powershell
$env:DB_BACKEND="sqlite"
$env:DATABASE_PATH="data/app_quality.db"
$env:VECTOR_DB_PATH="data/vector/faiss_action_items"
.\.venv\Scripts\streamlit.exe run dashboard/app.py
```

브라우저에서 `http://localhost:8501` 접속.

### 대시보드 주요 기능

| 탭 | 기능 |
|---|---|
| 전체 현황 | 회의별 액션 추이, 상태별 현황, 담당 역할별 미완료 Top N, 이슈 키워드 |
| 액션 운영 | 액션 상세 조회, 상태 변경, 근거 발화 확인, Slack 페이로드 생성 |
| 품질 점검 | 신뢰도 분포, 위험 신호 요약, 검토 필요 항목 목록 |
| STT 검토 | 발화 원문 및 타임스탬프 확인 |
| 유사도 검색 | 과거 유사 액션아이템 FAISS 검색 |
| 회의 업로드 | 로컬 음성/영상 파일 업로드 후 파이프라인 실행 |
| 가이드 | 로컬 실행 가이드, Gemini API·HuggingFace 토큰 준비 안내, 녹화 영상 업로드/재생 |

---

## 기술 스택 선택 근거

| 구성요소 | 선택 | 대안 | 선택 이유 |
|---|---|---|---|
| **STT** | faster-whisper (local, large-v3) | Whisper API, Clova | 무료·오프라인 동작. 4종 모델 비교 실험에서 large-v3가 발화 수(42개)가 레퍼런스(37개)에 가장 근접하고 키워드 재현율 1.000 달성 |
| **화자분리** | pyannote/speaker-diarization-3.1 | NeMo Sortformer, pyannote community-1 | 3종 비교 결과 모두 2화자 감지 (레퍼런스 3명). 화자 수 불일치는 오디오 특성 문제이며, pyannote 3.1이 설치 단순·처리 속도 우위 |
| **LLM 추출** | Gemini 2.5 Flash Lite | GPT-4o, Claude | 무료 티어 내 한국어 구조화 출력 품질 확보. 최종 제출 실행은 Gemini API 기준으로 구성 |
| **DB** | PostgreSQL | SQLite, DuckDB, MySQL | 200명 미만 사용자가 여러 팀에서 회의 데이터를 매주·매일 업로드하는 운영 형태를 가정해 PostgreSQL을 기본 DB로 사용 |
| **대시보드** | Streamlit | React, Metabase | Python 단일 스택, 마케터 직접 운영 대상. 빠른 프로토타이핑 가능 |
| **벡터 DB** | FAISS (로컬) | ChromaDB, Pinecone | 600건 기준 평균 쿼리 0.060ms (Chroma 6.653ms 대비 약 111배 빠름). PostgreSQL이 메타데이터를 담당하므로 Chroma의 문서 관리 기능이 불필요 |
| **임베딩** | sentence-transformers (local) | Gemini text-embedding | API 키 없이 의미 검색 동작. 한국어 지원 모델 직접 선택 가능 |

**STT 모델 선택 근거 (`experiments/compare_stt_diarization.py`)**

동일 오디오(4분, 3화자)에 4종 STT 모델을 실행해 레퍼런스 transcript(37발화)와 비교했습니다.

| 모델 | 발화 수 | 키워드 재현율 | 처리 시간 |
|---|---:|---:|---:|
| `base` | 79 | 1.000 | 196초 |
| `small` | 62 | 0.857 (`A/B` 누락) | 231초 |
| `medium` | 129 | 1.000 | 507초 |
| **`large-v3`** | **42** | **1.000** | 823초 |

`large-v3`는 발화 수가 레퍼런스(37)에 가장 근접하고 도메인 키워드를 모두 포착했습니다. 처리 시간이 길지만 배치 처리이므로 허용 가능하다고 판단했습니다. 자세한 내용은 `docs/stt_diarization_model_comparison.md`를 참고하세요.

**화자분리 모델 선택 근거**

pyannote 3.1 외에 NeMo Sortformer, pyannote community-1을 추가 실험했습니다.

| 조합 | 화자분리 소요 | 감지 화자 수 |
|---|---:|---:|
| large-v3 + pyannote 3.1 (채택) | — | 2 |
| large-v3 + NeMo Sortformer | 37.9초 | 2 |
| large-v3 + pyannote community-1 | 130.4초 | 2 |

세 모델 모두 2화자를 감지했습니다. 이는 STT 모델 문제가 아니라 유사한 목소리 특성을 가진 두 화자를 화자분리 알고리즘이 하나로 묶는 오디오 특성 문제입니다. pyannote 3.1은 HuggingFace 허브에서 직접 로드 가능하고 NeMo 대비 의존성이 단순해 선택했습니다. 화자 불일치는 숨기지 않고 STT 검토 탭에 노출했습니다.

**벡터 DB 선택 근거 (`experiments/compare_vector_dbs.py`)**

200명 사용자를 기준으로 FAISS와 Chroma를 비교했습니다.

| 규모 | 엔진 | 인덱스 빌드 | 평균 쿼리 | P95 쿼리 |
|---:|---|---:|---:|---:|
| 600건 | **FAISS** | 0.357ms | **0.060ms** | 0.115ms |
| 600건 | Chroma | 898ms | 6.653ms | 22.810ms |
| 6,000건 | **FAISS** | 3.267ms | **0.621ms** | 0.718ms |
| 6,000건 | Chroma | 5,236ms | 2.499ms | 6.310ms |

FAISS가 빌드·쿼리 모두 압도적으로 빠릅니다. PostgreSQL이 이미 메타데이터를 관리하므로 Chroma의 문서 컬렉션 기능이 불필요하다는 점도 FAISS 선택의 근거입니다. 자세한 내용은 `docs/vector_db_comparison.md`를 참고하세요.

**DB 선택 근거 (벤치마크 요약)**

`experiments/db_benchmark.py`로 SQLite·DuckDB·PostgreSQL 3종을 비교했습니다. 50~200행 규모 PoC에서는 세 DB 모두 응답 속도 차이가 미미했습니다. PostgreSQL은 회의, 발화, 액션아이템, 상태 변경 이력을 관계형 구조로 안정적으로 관리하기에 적합해 기본 DB로 채택했습니다. 자세한 수치는 `docs/db_benchmark.md`를 참고하세요.

---

## 프롬프트 설계 근거

### 설계 전략

**1. 도메인 컨텍스트 주입 (System Instruction)**

광고대행사 회의 특유의 암묵적 책임 표현("제가 챙길게요", "팀장님께 드릴게요")과 도메인 용어(ROAS, CPM, CTR, CTA, A/B 테스트, 픽셀 등)를 System Instruction에 명시했습니다. 일반 목적 프롬프트로는 이 표현들을 액션아이템으로 인식하지 못하거나 담당자를 잘못 귀속시키는 경우가 있었습니다.

**2. Few-shot 예시 (3개)**

실제 회의 발화 패턴을 반영한 3개 예시를 포함했습니다. 각 예시는 입력(발화 청크)과 출력(JSON) 쌍으로 구성되며, 근거 발화 ID(`source_utterance_ids`)와 위험 신호(`risk_flags`)를 명시적으로 포함해 LLM이 같은 형식을 따르도록 유도했습니다.

**3. JSON 스키마 강제**

별도의 `JSON_SCHEMA_INSTRUCTION` 블록으로 필드 타입·포맷을 지정했습니다. 마크다운 코드블록 금지를 명시해 파싱 실패를 줄였고, `description`은 `"~한다"` 형태의 한국어 문장으로 표준화했습니다.

**4. 검증 및 재시도 전략**

LLM 응답은 3단계를 거칩니다:

```
① JSON 파싱 + Pydantic 스키마 검증
② source_utterance_ids를 청크 범위 내 ID로 정제
③ 의미론적 검증
   - 설명 길이 (8~150자)
   - 카테고리 유효성 검사
   - 할루시네이션 탐지: 설명 핵심 토큰이 청크 텍스트에 존재하는지 확인
   - 중복 제거
```

파싱 실패 시 최대 3회 재시도하며, `extraction_runs` 테이블에 재시도 횟수와 오류 내용을 저장해 추후 프롬프트 개선에 활용합니다.

**5. 이중 신뢰도 분리**

`llm_confidence`(모델 자체 평가)와 `validation_score`(규칙 기반 검증)를 별도로 저장하고, `final_confidence = min(llm, validation)`으로 계산합니다. LLM이 자신감을 과도하게 부여할 때 규칙 검증이 이를 낮춰 운영자 검토 누락을 방지합니다.

---

## 가정 사항

| 항목 | 가정 내용 | 이유 |
|---|---|---|
| **화자 수** | 기본은 모델 자동 판단, 필요 시 `--num-speakers` 또는 업로드 탭의 화자 수 힌트 제공 | 실제 회의는 참석자 수를 대략 알 수 있는 경우가 많으므로 힌트를 제공하면 화자 분리 오류를 줄일 수 있습니다. 자동 감지 결과가 실제와 다를 때도 STT 검토 탭에 노출해 운영자가 확인할 수 있도록 했습니다 |
| **화자 정규화** | 발화 순서 기반 (첫 발화자 = team_lead) | 참가자 명단이 없으므로 발화 순서와 "팀장님" 같은 호칭으로 추론. 실제 배포 시 참가자 프로필 매핑으로 교체 가능 |
| **기한 없는 액션** | `due_date_missing` 위험 신호 마킹, 삭제하지 않음 | 마케팅 회의에서 기한 미언급 액션이 다수입니다. 삭제하면 실제 업무가 누락되므로 플래그만 표시하고 운영자 판단에 맡깁니다 |
| **상대 기한 해석** | 회의일을 기준일("오늘")로 보고 상대 시간 표현을 절대 날짜로 환산 (예: "내일" = 회의일 + 1일, "이번 주 수요일" = 회의 주의 수요일) | 회의록에는 "내일 오전", "수요일까지"처럼 상대 표현이 대부분이라 절대 날짜로 환산하려면 기준일이 필요합니다. 회의 메타데이터의 회의 날짜를 기준일로 가정하면 별도 입력 없이 기한을 채울 수 있습니다 |
| **평가용 정답 데이터셋** | 추출 품질 평가 지표(정밀도·재현율 등) 산출 시 사람이 라벨링한 정답(ground-truth) 액션 아이템 데이터셋이 존재한다고 가정 | 자동 추출 결과의 정량 평가에는 비교 기준이 되는 정답셋이 필요합니다. 본 제출에서는 정답셋을 직접 포함하지 않으나, 평가 파이프라인은 정답셋이 주어지면 동작하도록 설계했습니다 |
| **Gemini 무료 티어** | 분당 15 RPM 한도 내에서 청크 단위 순차 처리 | 무료 티어 유지를 위해 병렬 처리를 사용하지 않았습니다. 유료 전환 시 `asyncio.gather`로 교체 가능 |
| **Slack 연동** | Mock payload 생성 (실제 전송 없음) | Webhook URL 없이 페이로드 구조 검증만 가능하도록 설계. `SLACK_WEBHOOK_URL` 환경변수 추가 시 실제 전송으로 교체 가능 |
| **데이터 보안** | 샘플 transcript는 가상 데이터 | 실제 회의 음성·transcript를 포함하지 않았습니다. 실 배포 시 암호화 저장 및 접근 제어 필요 |

---

## 프로젝트 구조

```
.
├── pipeline.py                  # 파이프라인 오케스트레이션
├── models.py                    # 데이터 스키마 (Pydantic)
├── requirements.txt             # 핵심 의존성
├── requirements-stt.txt         # STT 의존성
├── .env.example                 # 환경 변수 템플릿
├── run.ps1                      # 한 줄 실행 스크립트
│
├── ingestion/                   # 오디오·JSON 로드, STT, 화자분리
├── preprocessing/               # 화자 정규화, 청크 생성, 도메인 용어
├── extraction/                  # Gemini LLM 추출, 검증, 재시도
├── db/                          # PostgreSQL 클라이언트
├── analytics/                   # 품질 분석, 키워드, 임베딩
├── dashboard/                   # Streamlit 대시보드
├── integrations/                # Slack mock 연동
│
├── data/
│   ├── sample_transcript.json   # 데모용 transcript (커밋됨)
│   ├── app.db                   # 초기 SQLite PoC 산출물
│   └── app_quality.db           # SQLite 개발용 샘플 DB
│
├── experiments/                 # DB·STT·Vector DB 비교 실험
├── scripts/                     # 임베딩 생성, 시드 데이터 등 유틸
└── docs/                        # 벤치마크 결과, 기획안
```

---

## 환경 변수 전체 목록

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | 최종 실행은 `gemini` |
| `GEMINI_API_KEY` | — | Gemini API 키 (실제 추출 시 필수) |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | 사용할 Gemini 모델 |
| `LLM_FALLBACK` | `mock` | 내부 개발용 fallback 설정 |
| `DB_BACKEND` | `postgres` | `postgres` 또는 `sqlite` |
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/mobidays_app` | PostgreSQL DSN |
| `DATABASE_PATH` | `data/app_quality.db` | SQLite 개발용 파일 경로 |
| `VECTOR_DB_PATH` | `data/vector/faiss_action_items` | FAISS 인덱스 경로 |
| `STT_MODEL` | `large-v3` | `small`, `base`, `large-v3` |
| `STT_DEVICE` | `cpu` | `cpu` 또는 `cuda` |
| `STT_COMPUTE_TYPE` | `int8` | `int8`, `float16` |
| `HUGGINGFACE_TOKEN` | — | pyannote 모델 접근용 (화자분리 시 필수) |
