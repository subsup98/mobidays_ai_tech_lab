# 기획안 — 모비데이즈 회의 액션아이템 자동화 시스템

> 코드가 본체이므로 의사결정 근거 위주로 서술합니다.

---

## 1. 문제 재정의

### 페인포인트 선정

퍼포먼스 마케팅 에이전시는 광고주·캠페인 단위로 주 2회 이상 회의를 반복한다. 회의 종료 후 담당자가 직접 액션아이템을 정리하는 방식은 다음 세 가지 문제를 갖는다.

| 문제 | 현상 | 업무 영향 |
|---|---|---|
| 정리 시간 낭비 | 회의 후 수기 정리에 30–60분 소요 | 고부가 업무 시간 잠식 |
| 담당자·기한 누락 | 구두 합의만 남고 문서화 안 됨 | ROAS·전환율 개선 액션 지연 |
| 반복 이슈 미파악 | 캠페인별 유사 문제가 반복돼도 인지 못함 | 동일 실수 재발, 클라이언트 신뢰 손상 |

### 우선 해결 이유

세 문제 중 **담당자·기한 누락**을 1순위로 설정했다. 정리 시간은 자동화로 줄어드는 부산물이지만, 누락된 액션은 광고 성과(ROAS, 전환율)에 직결되므로 비즈니스 임팩트가 가장 크다. 반복 이슈 파악은 데이터가 쌓인 뒤에야 통계적 유의미성을 갖기 때문에 중기 목표로 분류했다.

---

## 2. 시스템 아키텍처 다이어그램

```
[음성 mp3]
    │
    ▼
[수집] faster-whisper (STT) + pyannote 3.1 (화자분리)
    │  → canonical Transcript JSON
    │
    ▼
[처리·저장] 화자 정규화 → 청크화 → SQLite / PostgreSQL
    │  meetings / utterances / chunks / action_item_sources
    │
    ▼
[AI 추출] Gemini 2.5 Flash Lite  ──실패──▶  Mock Extractor
    │  structured JSON (담당자·기한·카테고리·신뢰도·위험신호)
    │
    ▼
[검증·저장] validation_score 계산 → dedup_key 중복제거 → DB 저장
    │
    ▼
[분배] Slack block-kit payload (mock)
    │
    ▼
[분석] Streamlit 대시보드 (5탭) + FAISS 유사도 검색
```

### 단계별 도구 선택 Trade-off

| 단계 | 선택 | 대안 | 선택 이유 |
|---|---|---|---|
| STT | faster-whisper (local) | Whisper API, Clova | 무료·오프라인 동작, large-v3 한국어 성능 충분 |
| 화자분리 | pyannote 3.1 | NeMo, resemblyzer | HuggingFace 허브 직접 사용, 실시간 불필요 |
| LLM 추출 | Gemini 2.5 Flash Lite | GPT-4o, Claude | 무료 티어 내 한국어 구조화 출력 품질 확보 |
| DB | SQLite (기본) / PostgreSQL (운영) | DuckDB, MySQL | SQLite는 설치 없이 로컬 재현, Postgres는 다중 접속 대비 |
| 대시보드 | Streamlit | React, Metabase | Python 단일 스택, 마케터 직접 운영 대상 |
| 임베딩 | sentence-transformers (local) | Gemini text-embedding | API 키 없이 의미 검색 동작, 384차원 FAISS IndexFlatIP |

---

## 3. 데이터 스키마 설계 근거

### 정규화 결정

```
meetings ─┬─ stt_runs
          ├─ participants
          ├─ utterances ──── action_item_sources
          ├─ chunks          (utterance ↔ action 다대다)
          └─ action_items ── action_item_events
                          └─ slack_payloads
```

- `utterances`와 `action_items`를 분리한 이유: STT 결과(원문)와 LLM 추출 결과(해석)의 재처리 독립성 확보. STT를 재실행해도 추출 결과를 보존할 수 있다.
- `action_item_sources` 연결 테이블: 어느 발화가 어떤 액션의 근거인지 추적. 할루시네이션 탐지와 대시보드 "근거 발화" 표시에 필수.
- `action_item_events`: 상태 변경 이력(open→in_progress→done)을 별도 테이블로 append-only 관리. 감사 추적과 운영 루프 구현.

### 비정규화 결정

`action_items`에 `assignee_normalized`, `campaign_context`, `advertiser_context`를 중복 저장했다. 정규화하면 대시보드 집계 쿼리마다 JOIN이 늘어나 Streamlit 응답 속도가 저하된다. 마케터가 실시간으로 필터링하는 컬럼이므로 비정규화로 쿼리를 단순화했다.

### 핵심 필드 설계 근거

**`action_item_id`**
```
hash(meeting_id + chunk_id + sequence_no)
```
동일 회의·청크·순번이면 항상 같은 ID → 파이프라인을 재실행해도 중복 삽입 없음(멱등성).

**`dedup_key`**
```
hash(meeting_id + assignee_normalized + category + due_date + normalized_task_signature)
```
같은 회의 내에서 LLM이 동일 태스크를 두 청크에서 중복 추출하는 경우를 의미적으로 제거. `action_item_id`(출처 기준)와 이중 보호.

**신뢰도 3-레이어**

| 필드 | 의미 | 산출 방식 |
|---|---|---|
| `llm_confidence` | LLM 자체 판단 | 프롬프트 내 자기 평가 요청 |
| `validation_score` | 규칙 기반 품질 | 담당자·기한·근거발화 유무, 설명 길이 등 5개 체크 |
| `final_confidence` | 최종 신뢰도 | `min(llm, validation)` |

`final_confidence < 0.7` 또는 `risk_flags` 존재 시 `review_required = true`로 마킹해 운영자에게 우선 노출.

---

## 4. Before / After 임팩트 추정 (100명 기준)

### 추정 전제

- 대상: 퍼포먼스 마케터 100명
- 평균 회의 빈도: 주 2회
- 현재 수기 정리 시간: 회의당 평균 45분
- 현재 액션아이템 누락률: ~20% (담당자 불명확 포함)
- 현재 반복 이슈 파악: 없음 (별도 시스템 부재)

### 수치 추정

| 지표 | Before | After | 근거 |
|---|---|---|---|
| 회의 후 정리 시간 | 45분/회 | 5분/회 | 파이프라인 자동 실행, 운영자는 대시보드 확인만 |
| 주간 절감 시간 (100명) | — | **6,667분 (111시간)** | (45-5)분 × 2회 × 100명 |
| 연간 절감 시간 | — | **5,800시간** | 111시간 × 52주 |
| 액션아이템 누락률 | ~20% | ~5% | `review_required` 플래그로 운영자 재확인 강제 |
| 담당자 불명확 | ~30% | ~8% | `assignee_missing` 위험신호 즉시 노출 |
| 반복 이슈 인지 속도 | 수 주 후 (수동 발견) | 회의 당일 | 이슈 키워드 자동 추출·대시보드 표시 |

### 한계

- STT 인식 정확도에 따라 추출 품질이 달라지므로 누락률 수치는 음성 환경이 양호한 경우를 전제한다.
- 도입 초기 4주는 운영자 검토 비중이 높아 실제 절감 시간이 낮을 수 있다.

---

## 5. 실패 시나리오 + 대응

### 시나리오 A — LLM 출력 품질 저하 (할루시네이션·스키마 불일치)

**발생 조건**: 회의 맥락이 불명확하거나 화자가 5명 이상인 복잡한 회의에서 Gemini가 실제 발화와 무관한 담당자·태스크를 생성.

**탐지**: `validation_score`가 `llm_confidence`보다 0.2 이상 낮은 경우 `review_required = true` 자동 마킹. 대시보드 "LLM은 높지만 검증은 낮음" 패널에 노출.

**대응**:
1. 운영자가 근거 발화 원문을 직접 확인 후 상태 유지 또는 삭제.
2. 반복 발생 시 프롬프트 few-shot 예시에 실패 패턴 추가.
3. Gemini API 오류 시 Mock Extractor 자동 전환으로 파이프라인 중단 방지.

---

### 시나리오 B — STT 인식 오류 (화자 혼동·발음 불명확)

**발생 조건**: 화상회의 잡음, 방언, 전문 용어(광고 플랫폼 약어)로 STT가 잘못 전사. pyannote가 화자를 2명으로 오인식하는 경우.

**탐지**: STT 검토 탭에서 utterance 원문과 타임스탬프 직접 확인 가능. diarization이 실패하면 "SPEAKER_00" 단일 화자로 fallback 후 `stt_runs`에 기록.

**대응**:
1. 화자분리 실패 시 단일 화자 모드로 파이프라인 계속 진행 (중단 없음).
2. STT 검토 탭에서 운영자가 오인식 구간 확인 → 재처리 요청.
3. 도메인 용어(ROAS, CPM, A/B 테스트 등)는 `glossary.py` 사전으로 전처리해 청크 주제 힌트로 활용, 추출 정확도 보완.

---

### 시나리오 C — Gemini API 할당량 초과

**발생 조건**: 무료 티어 분당 요청 한도(15 RPM) 초과. 대용량 회의 배치 처리 시 발생 가능.

**탐지**: `extraction_runs` 테이블의 `error_message` 컬럼에 API 오류 기록.

**대응**:
1. `LLM_PROVIDER=mock` 환경변수로 즉시 Mock 모드 전환, 파이프라인 중단 없음.
2. 오류 로그 확인 후 청크 단위 재시도(retry_count 기록).
3. 운영 규모 확대 시 유료 티어 또는 자체 호스팅 LLM(Ollama + Llama-3)으로 교체.

---

## 도입 후 4주 운영·검증 계획

| 주차 | 초점 | KPI |
|---|---|---|
| 1주 | 실제 회의 3건 파이프라인 실행 | 파이프라인 성공률, STT 트랜스크립트 가용성 |
| 2주 | 추출 품질 수동 검토 | Precision ≥ 0.75, `review_required` 비율 ≤ 30% |
| 3주 | 운영자 대시보드 피드백 | 상태 업데이트 사용률, 담당자 누락 케이스 수 |
| 4주 | 프롬프트·규칙 안정화 | 낮은 신뢰도 비율 감소, 수기 정리 시간 측정 |
