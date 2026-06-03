# 모비데이즈 회의 액션아이템 자동화 시스템

> **한 줄 실행**: `.\run.ps1`

회의 음성(mp3) 또는 transcript JSON을 입력받아 액션아이템을 자동 추출하고, SQLite/PostgreSQL에 적재한 뒤 Streamlit 대시보드에서 운영자가 확인·관리할 수 있는 PoC입니다.

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
[extraction] GeminiExtractor  ──실패──▶  MockExtractor (fallback)
         │  structured JSON (담당자·기한·카테고리·신뢰도·위험신호)
         │
         ▼
[validation] validation_score 계산 → dedup_key 중복 제거 → DB 저장
         │
         ▼
[db] SQLite (기본) / PostgreSQL (운영)
         │
         ▼
[dashboard] Streamlit 5탭 + FAISS 유사도 검색
```

### 모듈 분리 기준

| 모듈 | 책임 | 분리 이유 |
|---|---|---|
| `ingestion/` | 오디오·JSON 로드, STT, 화자분리, Transcript 조립 | 입력 포맷 다양성 흡수. STT 재실행이 추출 결과에 영향을 주지 않도록 격리 |
| `preprocessing/` | 화자 정규화, 청크 생성, 도메인 용어 처리 | 청킹 정책 변경 시 extraction 재실행만 하면 되도록 단계 분리 |
| `extraction/` | LLM 호출, 검증, fallback | LLM 교체(Gemini→Ollama 등) 시 extractor만 교체하면 됨 |
| `analytics/` | 신뢰도 분석, 키워드 추출, 임베딩 | 대시보드 집계 로직을 앱 코드에서 분리해 단독 테스트 가능 |
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

### 사전 준비

Python 3.10+ 및 Git이 설치되어 있어야 합니다.

```powershell
# 가상환경 생성 및 기본 패키지 설치
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

STT·화자분리 기능까지 사용하려면 추가 설치가 필요합니다 (CUDA 환경 권장, CPU도 동작):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-stt.txt
```

### 환경 변수 설정

`.env.example`을 `.env`로 복사 후 필요한 항목을 채웁니다.

```powershell
Copy-Item .env.example .env
```

```env
# Gemini 실제 추출 시 필요 (없으면 mock 모드로 자동 동작)
GEMINI_API_KEY=your_key_here

# STT·화자분리 시 필요 (없으면 transcript JSON 입력 모드 사용)
HUGGINGFACE_TOKEN=your_token_here
```

### 빠른 데모 (API 키 없이)

```powershell
.\run.ps1
```

브라우저에서 `http://localhost:8501` 접속. 샘플 transcript가 자동으로 파이프라인을 통과하고 대시보드가 열립니다.

### 파이프라인 단독 실행

**Transcript JSON 입력 (추천, STT 불필요)**

```powershell
$env:LLM_PROVIDER="mock"
.\.venv\Scripts\python.exe pipeline.py `
  --input data/interim/model_comparison_large_auto/transcript_large-v3_speakers-auto.json `
  --input-mode transcript `
  --db data/app.db
```

**음성 파일 입력**

```powershell
.\.venv\Scripts\python.exe pipeline.py `
  --input data/raw/sample.mp3 `
  --input-mode audio `
  --db data/app.db
```

**Gemini 실제 추출 모드**

```powershell
$env:LLM_PROVIDER="gemini"
$env:GEMINI_API_KEY="your_key"
.\.venv\Scripts\python.exe pipeline.py `
  --input data/sample_transcript.json `
  --input-mode transcript `
  --db data/app.db
```

### 대시보드

**SQLite 모드 (기본)**

```powershell
.\.venv\Scripts\streamlit.exe run dashboard/app.py
```

**PostgreSQL + Vector DB 모드**

```powershell
$env:DB_BACKEND="postgres"
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/mobidays_app"
$env:VECTOR_DB_PATH="data/vector/faiss_action_items"
.\.venv\Scripts\streamlit.exe run dashboard/app.py
```

브라우저에서 `http://localhost:8501` 접속.

### 대시보드 주요 기능

| 탭 | 기능 |
|---|---|
| 전체 현황 | 회의별 액션 추이, 상태별 현황, 담당자별 미완료 Top N, 이슈 키워드 |
| 액션 운영 | 액션 상세 조회, 상태 변경, 근거 발화 확인, Slack 페이로드 생성 |
| 품질 점검 | 신뢰도 분포, 위험 신호 요약, 검토 필요 항목 목록 |
| STT 검토 | 발화 원문 및 타임스탬프 확인 |
| 유사도 검색 | 과거 유사 액션아이템 FAISS 검색 |

---

## 기술 스택 선택 근거

| 구성요소 | 선택 | 대안 | 선택 이유 |
|---|---|---|---|
| **STT** | faster-whisper (local, large-v3) | Whisper API, Clova | 무료·오프라인 동작. large-v3 한국어 성능이 제공 transcript와 가장 근접. base 대비 WER 개선 확인 |
| **화자분리** | pyannote/speaker-diarization-3.1 | NeMo, resemblyzer | HuggingFace 허브 직접 사용, 설치 단순. 실시간 불필요하므로 오프라인 배치 처리로 충분 |
| **LLM 추출** | Gemini 2.5 Flash Lite | GPT-4o, Claude | 무료 티어 내 한국어 구조화 출력 품질 확보. API 오류 시 mock fallback으로 파이프라인 중단 없음 |
| **DB** | SQLite (기본) / PostgreSQL (운영) | DuckDB, MySQL | SQLite는 설치 없이 로컬 재현 가능. DB 벤치마크(`experiments/db_benchmark.py`) 수행 결과 집계 쿼리 성능 충분. 다중 접속 확장 시 PostgreSQL 전환 |
| **대시보드** | Streamlit | React, Metabase | Python 단일 스택, 마케터 직접 운영 대상. 빠른 프로토타이핑 가능 |
| **벡터 DB** | FAISS (로컬) | ChromaDB, Pinecone | API 키 없이 로컬 동작. 384차원 IndexFlatIP로 소규모 액션아이템 검색에 충분. 비교 실험(`experiments/compare_vector_dbs.py`) 포함 |
| **임베딩** | sentence-transformers (local) | Gemini text-embedding | API 키 없이 의미 검색 동작. 한국어 지원 모델 직접 선택 가능 |

**DB 선택 근거 (벤치마크 요약)**

`experiments/db_benchmark.py`로 SQLite·DuckDB·PostgreSQL 3종을 비교했습니다. 50~200행 규모 PoC에서는 세 DB 모두 응답 속도 차이가 미미했고, SQLite가 설치 없이 단일 파일로 재현 가능하다는 점이 리뷰어 편의성 측면에서 결정적이었습니다. 자세한 수치는 `docs/db_benchmark.md`를 참고하세요.

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

파싱 실패 시 최대 3회 재시도하며, 3회 모두 실패하면 MockExtractor로 자동 폴백합니다. `extraction_runs` 테이블에 재시도 횟수와 오류 내용을 기록해 추후 프롬프트 개선에 활용합니다.

**5. 이중 신뢰도 분리**

`llm_confidence`(모델 자체 평가)와 `validation_score`(규칙 기반 검증)를 별도로 저장하고, `final_confidence = min(llm, validation)`으로 계산합니다. LLM이 자신감을 과도하게 부여할 때 규칙 검증이 이를 낮춰 운영자 검토 누락을 방지합니다.

---

## 가정 사항

| 항목 | 가정 내용 | 이유 |
|---|---|---|
| **화자 수** | STT·화자분리 결과를 그대로 사용 (고정값 미설정) | 샘플 transcript는 3명이지만 pyannote가 2명으로 분리했습니다. 실수를 숨기지 않고 STT 검토 탭에 노출해 리뷰어가 직접 확인할 수 있도록 했습니다 |
| **화자 정규화** | 발화 순서 기반 (첫 발화자 = team_lead) | 참가자 명단이 없으므로 발화 순서와 "팀장님" 같은 호칭으로 추론. 실제 배포 시 참가자 프로필 매핑으로 교체 가능 |
| **기한 없는 액션** | `due_date_missing` 위험 신호 마킹, 삭제하지 않음 | 마케팅 회의에서 기한 미언급 액션이 다수입니다. 삭제하면 실제 업무가 누락되므로 플래그만 표시하고 운영자 판단에 맡깁니다 |
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
├── extraction/                  # LLM 추출, 검증, fallback
├── db/                          # SQLite/PostgreSQL 클라이언트
├── analytics/                   # 품질 분석, 키워드, 임베딩
├── dashboard/                   # Streamlit 대시보드
├── integrations/                # Slack mock 연동
│
├── data/
│   ├── sample_transcript.json   # 데모용 transcript (커밋됨)
│   ├── app.db                   # SQLite DB (mock 모드)
│   └── app_quality.db           # large-v3 + pyannote 기반 고품질 DB
│
├── experiments/                 # DB·STT·Vector DB 비교 실험
├── scripts/                     # 임베딩 생성, 시드 데이터 등 유틸
└── docs/                        # 벤치마크 결과, 기획안
```

---

## 환경 변수 전체 목록

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `gemini` 또는 `mock` |
| `GEMINI_API_KEY` | — | Gemini API 키 (실제 추출 시 필수) |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | 사용할 Gemini 모델 |
| `LLM_FALLBACK` | `mock` | API 실패 시 폴백 프로바이더 |
| `DB_BACKEND` | `sqlite` | `sqlite` 또는 `postgres` |
| `DATABASE_URL` | — | PostgreSQL DSN |
| `DATABASE_PATH` | `data/app.db` | SQLite 파일 경로 |
| `VECTOR_DB_PATH` | `data/vector/faiss_action_items` | FAISS 인덱스 경로 |
| `STT_MODEL` | `large-v3` | `small`, `base`, `large-v3` |
| `STT_DEVICE` | `cpu` | `cpu` 또는 `cuda` |
| `STT_COMPUTE_TYPE` | `int8` | `int8`, `float16` |
| `HUGGINGFACE_TOKEN` | — | pyannote 모델 접근용 (화자분리 시 필수) |
| `DIARIZATION_NUM_SPEAKERS` | — | 화자 수 고정 (미설정 시 자동 감지) |
