from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from analytics.embeddings import (
    search_similar,
    vector_db_path,
    vector_record_count,
)
from analytics.evaluation import full_evaluation_report
from analytics.keywords import regenerate_issue_keywords
from analytics.quality import (
    confidence_summary,
    low_confidence_items,
    validation_mismatches,
)
from db.pg_client import DEFAULT_DSN, PostgreSQLClient
from db.sqlite_client import SQLiteClient
from integrations.slack_mock import generate_and_store_payloads
from models import stable_hash


DEFAULT_DB_BACKEND = os.getenv("DB_BACKEND", "postgres")
DEFAULT_DB = os.getenv("DATABASE_PATH", "data/app_quality.db")
DEFAULT_DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("PGDSN") or DEFAULT_DSN


st.set_page_config(page_title="모비데이즈 회의 액션 대시보드", layout="wide")


COLUMN_LABELS = {
    "action_item_id": "액션 ID",
    "action_no": "액션 아이템",
    "assignee": "담당자",
    "assignee_normalized": "담당 역할 코드",
    "assignee_display": "담당 역할",
    "category": "분류",
    "priority": "우선순위",
    "status": "상태",
    "review_required": "검토 필요",
    "final_confidence": "최종 신뢰도",
    "llm_confidence": "LLM 신뢰도",
    "validation_score": "검증 점수",
    "description": "작업 내용",
    "display_description": "후속 작업",
    "due_date": "기한",
    "keyword": "키워드",
    "keyword_type": "키워드 유형",
    "score": "점수",
    "frequency": "빈도",
    "source_action_count": "관련 액션 수",
    "sequence_no": "순번",
    "speaker_raw": "원본 화자",
    "speaker_normalized": "정규화 화자",
    "start_sec": "시작초",
    "end_sec": "종료초",
    "text": "발화 내용",
    "old_status": "이전 상태",
    "new_status": "변경 상태",
    "changed_by": "변경자",
    "note": "메모",
    "created_at": "생성 시각",
    "model_name": "모델명",
    "provider": "제공자",
    "mode": "모드",
    "parsed_ok": "파싱 성공",
    "retry_count": "재시도",
    "error_message": "오류",
    "stt_model": "STT 모델",
    "diarization_model": "화자분리 모델",
    "speaker_count": "화자 수",
    "segment_count": "발화 수",
    "duration_sec": "길이초",
    "meeting_title": "회의명",
    "campaign": "캠페인",
    "advertiser": "광고주",
    "action_count": "전체 액션",
    "open_count": "미완료 액션",
    "similarity": "유사도",
    "open_count": "미완료 수",
    "evidence_summary": "근거 발화 요약",
    "priority_reason": "우선순위 근거",
    "review_reason": "검토 필요 사유",
    "risk_flag": "위험 신호",
    "count": "건수",
}


RISK_FLAG_LABELS = {
    "assignee_missing": "담당자 불명확",
    "due_date_missing": "기한 없음",
    "source_missing": "근거 발화 없음",
    "category_unclear": "분류 불명확",
    "description_too_short": "설명 부족",
}


KEYWORD_TYPE_LABELS = {
    "domain": "도메인 용어",
    "bigram": "연관어",
    "risk_flag": "위험 신호",
}


ASSIGNEE_LABELS = {
    "team_lead": "팀장",
    "performance_marketer": "퍼포먼스 마케터",
    "content_designer": "콘텐츠 디자이너",
    "speaker_from_context": "발화자 추론 필요",
    "unassigned": "담당자 미배정",
}


@st.cache_resource
def get_client(db_backend: str, db_path: str, database_url: str) -> object:
    if db_backend == "postgres":
        client = PostgreSQLClient(dsn=database_url)
    else:
        client = SQLiteClient(db_path)
    client.init_schema()
    return client


def as_dataframe(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def display_dataframe(df: pd.DataFrame, **kwargs: object) -> None:
    if df.empty:
        st.dataframe(df, width="stretch", hide_index=True, **kwargs)
        return
    st.dataframe(
        df.rename(columns={k: v for k, v in COLUMN_LABELS.items() if k in df.columns}),
        width="stretch",
        hide_index=True,
        **kwargs,
    )


def _with_assignee_display(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "assignee_normalized" not in df.columns:
        return df
    enriched = df.copy()
    enriched["assignee_display"] = enriched["assignee_normalized"].apply(_assignee_label)
    return enriched


def _assignee_label(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return "담당자 미배정"
    return ASSIGNEE_LABELS.get(normalized, normalized)


def main() -> None:
    st.title("모비데이즈 회의 액션 대시보드")
    db_backend = st.sidebar.selectbox(
        "운영 DB",
        ["postgres", "sqlite"],
        index=0 if DEFAULT_DB_BACKEND == "postgres" else 1,
    )
    database_url = st.sidebar.text_input("PostgreSQL DSN", DEFAULT_DATABASE_URL)
    db_path = DEFAULT_DB
    if db_backend == "sqlite":
        db_path = st.sidebar.text_input("SQLite DB 경로", DEFAULT_DB)
    st.sidebar.caption(f"Vector DB: {vector_db_path()}")
    client = get_client(db_backend, db_path, database_url)

    meetings = client.fetch_all(
        """
        SELECT meeting_id, title, meeting_date, source_type, created_at
        FROM meetings
        ORDER BY created_at DESC
        """
    )
    meeting_df = as_dataframe(meetings)

    tabs = st.tabs(
        ["전체 현황", "액션 운영", "품질 점검", "STT 검토", "유사도 검색", "회의 업로드", "가이드"]
    )

    with tabs[5]:
        render_upload(db_backend, db_path, database_url)
    with tabs[6]:
        render_guide()

    if meeting_df.empty:
        for tab in tabs[:5]:
            with tab:
                st.info("회의 데이터가 없습니다. '회의 업로드' 탭에서 파일을 업로드해 주세요.")
        return

    meeting_id = st.sidebar.selectbox(
        "회의 선택",
        meeting_df["meeting_id"].tolist(),
        format_func=lambda value: _meeting_label(meeting_df, value),
    )

    with tabs[0]:
        render_overview(client, meeting_id)
    with tabs[1]:
        render_action_ops(client, meeting_id)
    with tabs[2]:
        render_quality(client, meeting_id)
    with tabs[3]:
        render_stt_review(client, meeting_id)
    with tabs[4]:
        render_similar_decisions(client, meeting_id)


def render_overview(client: object, meeting_id: str) -> None:
    _render_meeting_summary(client, meeting_id)
    st.divider()

    actions = client.list_action_items(meeting_id)
    summary = confidence_summary(client, meeting_id)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("액션 수", int(summary["total"]))
    col2.metric("검토 필요", int(summary["review_required_count"]))
    col3.metric("최종 신뢰도", summary["avg_final_confidence"])
    col4.metric("검증 점수", summary["avg_validation_score"])

    # ── 위젯 1: 회의별 액션아이템 발생 추이 ──────────────────────────
    st.subheader("회의별 액션아이템 발생 추이")
    trend_rows = client.fetch_all(
        """
        SELECT
            m.title,
            COALESCE(m.meeting_date, CAST(m.created_at AS VARCHAR)) AS meeting_date,
            COUNT(a.action_item_id) AS action_count,
            SUM(CASE WHEN a.status != 'done' THEN 1 ELSE 0 END) AS open_count
        FROM meetings m
        LEFT JOIN action_items a ON a.meeting_id = m.meeting_id
        GROUP BY m.meeting_id, m.title, meeting_date
        ORDER BY meeting_date ASC
        """
    )
    trend_df = as_dataframe(trend_rows)
    if trend_df.empty:
        st.info("회의 데이터가 없습니다.")
    else:
        trend_df["meeting_date"] = trend_df["meeting_date"].astype(str).str[:10]
        trend_melt = trend_df.melt(
            id_vars=["title", "meeting_date"],
            value_vars=["action_count", "open_count"],
            var_name="구분",
            value_name="건수",
        )
        trend_melt["구분"] = trend_melt["구분"].map(
            {"action_count": "전체 액션", "open_count": "미완료 액션"}
        )
        x_labels = (trend_df["meeting_date"] + "\n" + trend_df["title"]).tolist()
        st.altair_chart(
            alt.Chart(trend_melt).mark_bar().encode(
                x=alt.X(
                    "meeting_date:N",
                    title="회의 날짜",
                    axis=alt.Axis(labelAngle=-30),
                ),
                y=alt.Y("건수:Q", title="건수", scale=alt.Scale(domainMin=0)),
                color=alt.Color(
                    "구분:N",
                    scale=alt.Scale(
                        domain=["전체 액션", "미완료 액션"],
                        range=["#4C8BF5", "#E8433A"],
                    ),
                ),
                xOffset="구분:N",
                tooltip=["title", "meeting_date", "구분", "건수"],
            ).properties(width="container", height=260),
            width="stretch",
        )

    # ── 이 회의 상태별 현황 ──────────────────────────────────────────
    action_df = _with_assignee_display(as_dataframe(actions))
    if not action_df.empty:
        st.subheader("이 회의 상태별 현황")
        _status_ko = {
            "open": "미시작",
            "in_progress": "진행 중",
            "blocked": "블로킹",
            "done": "완료",
        }
        status_order = ["open", "in_progress", "blocked", "done"]
        status_order_ko = [_status_ko[s] for s in status_order]
        status_counts = action_df.groupby("status").size().reset_index(name="건수")
        status_counts["상태명"] = status_counts["status"].map(_status_ko)
        st.altair_chart(
            alt.Chart(status_counts).mark_bar().encode(
                x=alt.X("상태명:N", title="상태", sort=status_order_ko),
                y=alt.Y("건수:Q", title="건수", scale=alt.Scale(domainMin=0), axis=alt.Axis(tickMinStep=1, format="d")),
                color=alt.Color(
                    "상태명:N",
                    sort=status_order_ko,
                    scale=alt.Scale(
                        domain=status_order_ko,
                        range=["#4C8BF5", "#F5A623", "#E8433A", "#34A853"],
                    ),
                    legend=alt.Legend(title="상태"),
                ),
                tooltip=["상태명:N", "건수:Q"],
            ).properties(width="container", height=220),
            width="stretch",
        )

        # ── 위젯 2: 담당 역할별 미완료 Top N ────────────────────────
        st.subheader("담당 역할별 미완료 액션 Top N")
        assignee_backlog = (
            action_df[action_df["status"] != "done"]
            .groupby("assignee_display")
            .size()
            .reset_index(name="open_count")
            .sort_values("open_count", ascending=False)
        )
        if assignee_backlog.empty:
            st.success("미완료 액션이 없습니다.")
        else:
            st.altair_chart(
                alt.Chart(assignee_backlog).mark_bar().encode(
                    x=alt.X("open_count:Q", title="미완료 건수", scale=alt.Scale(domainMin=0), axis=alt.Axis(tickMinStep=1, format="d")),
                    y=alt.Y("assignee_display:N", title="담당 역할", sort="-x"),
                    color=alt.value("#F5A623"),
                    tooltip=["assignee_display", "open_count"],
                ).properties(width="container", height=max(120, len(assignee_backlog) * 40)),
                width="stretch",
            )

    # ── 위젯 3: 캠페인/광고주별 반복 이슈 키워드 ────────────────────
    st.subheader("캠페인/광고주별 이슈 키워드")
    if st.button("이슈 키워드 재생성"):
        regenerate_issue_keywords(client, meeting_id)
        st.rerun()

    kw_rows = client.fetch_all(
        """
        SELECT
            keyword,
            keyword_type,
            score,
            frequency,
            source_action_count,
            campaign_context AS campaign,
            advertiser_context AS advertiser
        FROM issue_keywords
        WHERE meeting_id = ?
        ORDER BY score DESC
        """,
        (meeting_id,),
    )
    kw_df = as_dataframe(kw_rows)
    if not kw_df.empty:
        campaigns = ["전체"] + sorted({c for c in kw_df["campaign"].unique() if c != "(미분류)"})
        advertisers = ["전체"] + sorted({a for a in kw_df["advertiser"].unique() if a != "(미분류)"})
        filter_col1, filter_col2 = st.columns(2)
        sel_campaign = filter_col1.selectbox("캠페인 필터", campaigns, key="kw_campaign")
        sel_advertiser = filter_col2.selectbox("광고주 필터", advertisers, key="kw_advertiser")

        if sel_campaign != "전체":
            kw_df = kw_df[kw_df["campaign"] == sel_campaign]
        if sel_advertiser != "전체":
            kw_df = kw_df[kw_df["advertiser"] == sel_advertiser]
        kw_df = kw_df.head(20)
        kw_df["keyword_type"] = kw_df["keyword_type"].map(
            lambda value: KEYWORD_TYPE_LABELS.get(value, value)
        )
        display_dataframe(
            kw_df[["keyword", "keyword_type", "frequency", "source_action_count", "campaign", "advertiser"]]
        )
    else:
        st.info("키워드가 없습니다. 이슈 키워드 재생성 버튼을 눌러주세요.")


def render_action_ops(client: object, meeting_id: str) -> None:
    actions = client.list_action_items(meeting_id)
    action_df = _sort_actions(as_dataframe(actions))
    action_df = _with_action_numbers(action_df)
    action_df = _add_display_descriptions(client, meeting_id, action_df)
    st.subheader("액션 요약")
    summary_df = build_action_summary_df(client, meeting_id)
    display_dataframe(summary_df)

    st.subheader("액션 상세")
    visible_columns = [
        "action_no",
        "assignee_display",
        "category",
        "priority",
        "status",
        "review_required",
        "final_confidence",
        "display_description",
        "due_date",
    ]
    display_dataframe(
        action_df[[col for col in visible_columns if col in action_df.columns]],
    )

    if action_df.empty:
        return

    selected = st.selectbox(
        "액션 선택",
        action_df["action_item_id"].tolist(),
        format_func=lambda value: _action_label(action_df, value),
    )
    selected_row = action_df[action_df["action_item_id"] == selected].iloc[0]

    detail_left, detail_right = st.columns([2, 3])
    with detail_left:
        st.markdown("**선택한 액션**")
        st.write(f"액션 아이템: {selected_row['action_no']}")
        st.write(f"담당 역할: {selected_row['assignee_display']}")
        st.write(f"분류: {selected_row['category']}")
        st.write(f"우선순위: {selected_row['priority']}")
        st.write(f"우선순위 근거: {_priority_reason(selected_row)}")
        st.write(f"상태: {selected_row['status']}")
        st.write(f"신뢰도: {selected_row['final_confidence']}")
        st.write(f"검토 필요: {bool(selected_row['review_required'])}")
        st.write(f"후속 작업: {selected_row['display_description']}")
        if selected_row["display_description"] != selected_row["description"]:
            st.caption(f"원본 추출 문구: {selected_row['description']}")

    with detail_right:
        st.markdown("**근거 발화**")
        evidence = client.fetch_all(
            """
            SELECT
                u.sequence_no,
                u.speaker_raw,
                u.speaker_normalized,
                u.start_sec,
                u.end_sec,
                u.text
            FROM action_item_sources s
            JOIN utterances u ON u.utterance_id = s.utterance_id
            WHERE s.action_item_id = ?
            ORDER BY u.sequence_no
            """,
            (selected,),
        )
        display_dataframe(as_dataframe(evidence))

    new_status = st.selectbox("상태 변경", ["open", "in_progress", "done", "blocked"])
    note = st.text_input("메모", "")

    if st.button("상태 업데이트"):
        event_id = stable_hash(
            "event",
            selected,
            new_status,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        client.update_action_status(
            action_item_id=selected,
            new_status=new_status,
            event_id=event_id,
            note=note or None,
        )
        st.rerun()

    if st.button("Slack payload 생성"):
        generate_and_store_payloads(client, meeting_id)
        st.rerun()

    payloads = client.fetch_all(
        """
        SELECT payload_json, created_at
        FROM slack_payloads
        WHERE action_item_id IN (
            SELECT action_item_id FROM action_items WHERE meeting_id = ?
        )
        ORDER BY created_at DESC
        """,
        (meeting_id,),
    )
    if payloads:
        st.json(json.loads(payloads[0]["payload_json"]))

    events = client.fetch_all(
        """
        SELECT e.action_item_id, e.old_status, e.new_status, e.changed_by, e.note, e.created_at
        FROM action_item_events e
        JOIN action_items a ON a.action_item_id = e.action_item_id
        WHERE a.meeting_id = ?
        ORDER BY e.created_at DESC
        """,
        (meeting_id,),
    )
    display_dataframe(as_dataframe(events))


def render_quality(client: object, meeting_id: str) -> None:
    actions = client.list_action_items(meeting_id)
    action_df = as_dataframe(actions)
    action_df = _add_display_descriptions(client, meeting_id, action_df)

    if action_df.empty:
        st.info("이 회의에는 품질 점검할 액션아이템이 없습니다.")
        return

    st.caption(
        "LLM 신뢰도는 모델의 자체 판단, 검증 점수는 담당자/기한/근거 발화 같은 규칙 기반 점검입니다. "
        "최종 신뢰도는 둘 중 낮은 값이며, 위험 신호가 있으면 검토 필요로 표시됩니다."
    )

    review_df = _build_review_reason_df(action_df)
    flagged_df = review_df[review_df["review_required"] == 1]
    risk_summary_df = _build_risk_flag_summary_df(review_df)

    col1, col2, col3 = st.columns(3)
    col1.metric("검토 필요", f"{len(flagged_df)} / {len(action_df)}")
    col2.metric("주요 위험 신호", _risk_summary_metric(risk_summary_df))
    col3.metric("최저 최종 신뢰도", round(float(action_df["final_confidence"].min()), 3))

    if not risk_summary_df.empty:
        st.subheader("검토 필요 사유 요약")
        display_dataframe(risk_summary_df)
    else:
        st.success("담당자, 기한, 근거 발화 기준에서 감지된 위험 신호가 없습니다.")

    low_items = low_confidence_items(client, meeting_id)
    mismatches = validation_mismatches(client, meeting_id)

    left, right = st.columns(2)
    left.subheader("낮은 신뢰도")
    with left:
        low_df = _with_action_numbers(as_dataframe(low_items))
        if low_df.empty:
            st.info("최종 신뢰도 0.7 미만 항목은 없습니다.")
        else:
            display_dataframe(_quality_table(low_df))
    right.subheader("신뢰도-검증 불일치")
    with right:
        mismatch_df = _with_action_numbers(as_dataframe(mismatches))
        if mismatch_df.empty:
            st.info("LLM 신뢰도와 검증 점수 차이가 0.2 이상인 항목은 없습니다.")
        else:
            display_dataframe(_quality_table(mismatch_df))

    st.subheader("검토 필요 항목")
    if flagged_df.empty:
        st.info("사람이 확인해야 하는 항목이 없습니다.")
    else:
        display_dataframe(
            flagged_df[
                [
                    "action_no",
                    "assignee_display",
                    "display_description",
                    "llm_confidence",
                    "validation_score",
                    "final_confidence",
                    "review_reason",
                ]
            ]
        )

    st.divider()
    st.subheader("평가 지표")

    gold_path = st.text_input(
        "정답 라벨 경로 (선택)",
        value=f"data/gold/meeting_{meeting_id}.json",
        help="정답 액션아이템 JSON 파일입니다. 비워두면 proxy metric만 표시합니다.",
    )

    report = full_evaluation_report(
        client,
        meeting_id,
        gold_path=gold_path or None,
    )

    st.markdown("**Proxy metrics** (정답 라벨 없이 저장 데이터로 계산)")
    proxy_df = as_dataframe(
        [{"metric": k, "value": v} for k, v in report.proxy.items()]
    )
    display_dataframe(proxy_df)

    if report.has_gold:
        st.markdown("**정답 라벨 평가**")
        col1, col2, col3 = st.columns(3)
        col1.metric("정밀도", report.precision)
        col2.metric("재현율", report.recall)
        col3.metric("F1", report.f1)

        col4, col5 = st.columns(2)
        col4.metric("담당자 정확도", report.assignee_accuracy)
        col5.metric("분류 정확도", report.category_accuracy)

        if report.per_category:
            st.markdown("**분류별 P/R/F1**")
            cat_rows = [
                {
                    "category": cat,
                    "precision": m.precision,
                    "recall": m.recall,
                    "f1": m.f1,
                    "predicted": m.predicted_count,
                    "gold": m.gold_count,
                }
                for cat, m in report.per_category.items()
            ]
            display_dataframe(as_dataframe(cat_rows))

        if report.matched_pairs:
            with st.expander("매칭된 항목"):
                display_dataframe(as_dataframe(report.matched_pairs))
    else:
        st.info(
            "정답 라벨 파일이 없거나 찾을 수 없습니다. "
            "정답 JSON을 제공하면 정밀도/재현율/F1을 볼 수 있습니다."
        )


def render_stt_review(client: object, meeting_id: str) -> None:
    stt_runs = client.fetch_all(
        """
        SELECT *
        FROM stt_runs
        WHERE meeting_id = ?
        ORDER BY created_at DESC
        """,
        (meeting_id,),
    )
    utterances = client.fetch_all(
        """
        SELECT sequence_no, speaker_raw, speaker_normalized, start_sec, end_sec, text
        FROM utterances
        WHERE meeting_id = ?
        ORDER BY sequence_no
        """,
        (meeting_id,),
    )

    display_dataframe(as_dataframe(stt_runs))
    display_dataframe(as_dataframe(utterances))


def render_similar_decisions(client: object, meeting_id: str) -> None:
    total_stored = vector_record_count()
    meeting_stored = vector_record_count(meeting_id)

    st.caption(f"Vector DB: 전체 {total_stored}건 (이 회의 {meeting_stored}건)")

    st.divider()

    with st.form("similar_search_form"):
        search_scope = st.radio(
            "검색 범위",
            ["현재 회의", "전체 회의"],
            horizontal=True,
            help="'현재 회의'는 이 회의 아이템만, '전체 회의'는 모든 회의 아이템을 검색합니다.",
        )
        query = st.text_input("검색어", placeholder="예: ROAS 리포트 확인")
        submitted = st.form_submit_button("검색")

    if submitted and query.strip():
        scope_meeting_id = meeting_id if search_scope == "현재 회의" else None
        results = search_similar(client, query.strip(), top_k=5, meeting_id=scope_meeting_id)
        if results:
            result_df = _with_assignee_display(as_dataframe(results))
            display_dataframe(
                result_df[
                    [
                        "description",
                        "assignee_display",
                        "category",
                        "priority",
                        "status",
                        "final_confidence",
                        "meeting_title",
                        "similarity",
                        "evidence_summary",
                    ]
                ],
            )
        else:
            st.info("Vector DB에 저장된 임베딩이 없습니다. 먼저 임베딩을 생성하세요.")


def render_guide() -> None:
    st.subheader("로컬 실행 가이드")
    st.caption("한 줄 실행 스크립트가 환경설정까지 처리하며, 기본 데모와 STT 음성 입력 모드를 분리했습니다.")

    quick_tab, real_tab, key_tab, video_tab = st.tabs(
        ["기본 데모", "STT 음성 입력", "키 준비", "녹화본"]
    )

    with quick_tab:
        st.markdown("**용도**")
        st.write("제출 확인용 기본 경로입니다. STT와 화자분리를 생략하고 샘플 transcript로 추출, DB 저장, 대시보드를 확인합니다.")

        st.markdown("**1. 한 줄 실행**")
        st.code(
            ".\\run.ps1",
            language="powershell",
        )

        st.caption("최초 실행 시 `.venv` 생성, `requirements.txt` 설치, `.env.example` 복사까지 자동으로 수행합니다.")

        st.markdown("**2. 입력과 의존성**")
        st.write("기본 입력은 `data/sample_transcript.json`입니다. 이 모드는 `requirements-stt.txt`를 설치하지 않습니다.")

        st.markdown("**3. 선택 설정**")
        st.code(
            """GEMINI_API_KEY=your_gemini_api_key
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/mobidays_app""",
            language="dotenv",
        )
        st.caption("Gemini API 키가 없으면 mock extractor fallback으로 로컬 데모를 확인할 수 있습니다. PostgreSQL 연결이 안 되면 SQLite로 자동 전환됩니다.")

    with real_tab:
        st.markdown("**용도**")
        st.write("mp3, wav, m4a, flac 파일을 입력해 STT, 화자분리, 추출, DB 저장, 대시보드까지 확인합니다.")

        st.markdown("**1. 음성 파일 실행**")
        st.code(
            ".\\run.ps1 -InputMode audio -InputPath \"C:\\path\\meeting.mp3\"",
            language="powershell",
        )
        st.caption("오디오 모드에서는 `requirements.txt`와 `requirements-stt.txt`가 자동 설치됩니다.")

        st.markdown("**2. 필요한 키**")
        st.code(
            """GEMINI_API_KEY=your_gemini_api_key
HUGGINGFACE_TOKEN=your_huggingface_token
DIARIZATION_MODEL=pyannote/speaker-diarization-3.1
DIARIZATION_REQUIRE_SUCCESS=0""",
            language="dotenv",
        )

        st.markdown("**3. 회의 인원 수를 알고 있을 때**")
        st.code(
            ".\\run.ps1 -InputMode audio -InputPath \"C:\\path\\meeting.mp3\" -NumSpeakers 3",
            language="powershell",
        )

    with key_tab:
        st.markdown("**API 키와 모델 접근 준비**")
        guide_rows = [
            {
                "항목": "Gemini API 키",
                "링크": "https://aistudio.google.com/app/apikey",
                "설정값": "GEMINI_API_KEY",
                "메모": "Google 계정 로그인 후 API key 생성",
            },
            {
                "항목": "HuggingFace 토큰",
                "링크": "https://huggingface.co/settings/tokens",
                "설정값": "HUGGINGFACE_TOKEN",
                "메모": "Read 권한 토큰 생성",
            },
            {
                "항목": "pyannote 모델 약관",
                "링크": "https://huggingface.co/pyannote/speaker-diarization-3.1",
                "설정값": "DIARIZATION_MODEL",
                "메모": "speaker-diarization-3.1 및 segmentation-3.0 접근 조건 동의",
            },
        ]
        st.dataframe(pd.DataFrame(guide_rows), width="stretch", hide_index=True)

    with video_tab:
        default_video_path = Path("data/guide/모비데이즈.mkv")
        if default_video_path.exists():
            st.video(str(default_video_path))

        video_url = st.text_input(
            "영상 링크",
            placeholder="예: Loom 링크, unlisted YouTube URL",
        )
        if video_url.strip():
            st.video(video_url.strip())

        uploaded_video = st.file_uploader(
            "녹화 영상 업로드",
            type=["mp4", "mov", "webm", "m4v", "mkv"],
            help="로컬에서 실행 과정을 녹화한 파일을 올리면 이 탭에서 바로 재생됩니다.",
        )
        if uploaded_video is not None:
            st.video(uploaded_video)


def build_action_summary_df(client: object, meeting_id: str) -> pd.DataFrame:
    evidence_expr = _evidence_concat_sql(client)
    group_by = _action_summary_group_by_sql(client)
    rows = client.fetch_all(
        f"""
        SELECT
            a.action_item_id,
            a.sequence_no,
            c.start_sequence_no,
            a.assignee_normalized,
            a.category,
            a.priority,
            a.status,
            a.review_required,
            a.final_confidence,
            a.description,
            {evidence_expr} AS evidence_summary
        FROM action_items a
        LEFT JOIN chunks c ON c.chunk_id = a.chunk_id
        LEFT JOIN action_item_sources s ON s.action_item_id = a.action_item_id
        LEFT JOIN utterances u ON u.utterance_id = s.utterance_id
        WHERE a.meeting_id = ?
        GROUP BY {group_by}
        ORDER BY
            CASE WHEN a.due_date IS NOT NULL THEN 0 ELSE 1 END ASC,
            a.due_date ASC,
            CASE a.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END ASC,
            COALESCE(c.start_sequence_no, a.sequence_no) ASC,
            a.sequence_no ASC
        """,
        (meeting_id,),
    )
    df = as_dataframe(rows)
    if df.empty:
        return df
    df = _with_action_numbers(df)
    df = _attach_display_description_from_evidence(df)
    return df[
        [
            "action_no",
            "assignee_display",
            "display_description",
            "category",
            "priority",
            "status",
            "review_required",
            "final_confidence",
            "evidence_summary",
        ]
    ]


def _add_display_descriptions(
    client: object,
    meeting_id: str,
    action_df: pd.DataFrame,
) -> pd.DataFrame:
    if action_df.empty:
        return action_df

    text_concat_expr = _text_concat_sql(client)
    evidence_rows = client.fetch_all(
        f"""
        SELECT
            a.action_item_id,
            {text_concat_expr} AS evidence_summary
        FROM action_items a
        LEFT JOIN action_item_sources s ON s.action_item_id = a.action_item_id
        LEFT JOIN utterances u ON u.utterance_id = s.utterance_id
        WHERE a.meeting_id = ?
        GROUP BY a.action_item_id
        """,
        (meeting_id,),
    )
    evidence_by_action = {
        row["action_item_id"]: row.get("evidence_summary") or ""
        for row in evidence_rows
    }

    enriched = action_df.copy()
    enriched["evidence_summary"] = enriched["action_item_id"].map(evidence_by_action)
    return _attach_display_description_from_evidence(enriched)


def _evidence_concat_sql(client: object) -> str:
    if isinstance(client, PostgreSQLClient):
        return "STRING_AGG('[' || u.sequence_no || '] ' || u.speaker_raw || ': ' || u.text, CHR(10) ORDER BY u.sequence_no)"
    return "GROUP_CONCAT('[' || u.sequence_no || '] ' || u.speaker_raw || ': ' || u.text, CHAR(10))"


def _text_concat_sql(client: object) -> str:
    if isinstance(client, PostgreSQLClient):
        return "STRING_AGG(u.text, CHR(10) ORDER BY u.sequence_no)"
    return "GROUP_CONCAT(u.text, CHAR(10))"


def _action_summary_group_by_sql(client: object) -> str:
    # PostgreSQL requires all non-aggregate SELECT columns in GROUP BY
    if isinstance(client, PostgreSQLClient):
        return (
            "a.action_item_id, a.sequence_no, c.start_sequence_no, "
            "a.assignee_normalized, a.category, a.priority, a.status, "
            "a.review_required, a.final_confidence, a.description"
        )
    return "a.action_item_id, c.start_sequence_no"


def _attach_display_description_from_evidence(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    enriched = df.copy()
    enriched["display_description"] = enriched.apply(_display_description, axis=1)
    return _with_assignee_display(enriched)


def _display_description(row: pd.Series) -> str:
    description = str(row.get("description") or "").strip()
    if description and not _is_generic_description(description):
        return description

    evidence = str(row.get("evidence_summary") or "")
    inferred = _infer_task_from_evidence(evidence)
    return inferred or description or "후속 작업 내용을 확인한다"


def _is_generic_description(description: str) -> bool:
    generic_markers = [
        "회의에서 언급된 후속 작업",
        "후속 작업을 진행",
        "후속 작업을 확인",
        "관련 작업을 진행",
    ]
    return any(marker in description for marker in generic_markers)


def _infer_task_from_evidence(evidence: str) -> str:
    text = " ".join(evidence.split())
    rules = [
        (
            ["헤드라인", "AB", "카피"],
            "변경된 카피로 헤드라인 A/B 테스트를 다시 세팅한다",
        ),
        (
            ["비주얼 카드", "빈 슬롯", "카피"],
            "비주얼 카드 순서와 빈 슬롯 카피를 정리한다",
        ),
        (
            ["담당자", "푸시", "임시 컷"],
            "담당자에게 추가 푸시하고 미응답 시 임시 컷으로 진행한다",
        ),
        (
            ["픽셀 보장", "다시 드릴게요"],
            "픽셀 보장 내용을 다시 전달한다",
        ),
        (
            ["픽셀 정리", "같이 챙길게요"],
            "픽셀 정리 이후 관련 후속 이슈를 함께 챙긴다",
        ),
        (
            ["인사이트", "비주얼", "톤"],
            "지난 캠페인 인사이트를 기준으로 비주얼 톤을 결정한다",
        ),
        (
            ["캠페인", "정리할 게"],
            "다음 달 캠페인 전 정리 항목을 확인한다",
        ),
    ]

    for keywords, task in rules:
        if all(keyword in text for keyword in keywords):
            return task
    return _first_action_like_sentence(text)


def _first_action_like_sentence(text: str) -> str:
    for sentence in _split_sentences(text):
        if any(marker in sentence for marker in ["할게", "드릴게", "정리", "세팅", "진행", "확인", "챙기"]):
            return sentence
    return ""


def _split_sentences(text: str) -> list[str]:
    normalized = text.replace("?", ".").replace("!", ".")
    return [part.strip() for part in normalized.split(".") if part.strip()]


def _risk_summary_metric(risk_summary_df: pd.DataFrame) -> str:
    if risk_summary_df.empty:
        return "없음"
    top = risk_summary_df.iloc[0]
    if len(risk_summary_df) == 1:
        return str(top["risk_flag"])
    return f"{top['risk_flag']} 외 {len(risk_summary_df) - 1}종"


def _build_review_reason_df(action_df: pd.DataFrame) -> pd.DataFrame:
    df = _with_action_numbers(action_df.copy())
    if "risk_flags_json" not in df.columns:
        df["risk_flags_json"] = "[]"
    if "review_required" not in df.columns:
        df["review_required"] = 0

    df["risk_flags"] = df["risk_flags_json"].apply(_parse_risk_flags)
    df["review_reason"] = df.apply(_review_reason, axis=1)
    return _with_assignee_display(df)


def _build_risk_flag_summary_df(review_df: pd.DataFrame) -> pd.DataFrame:
    counts: dict[str, int] = {}
    for flags in review_df.get("risk_flags", []):
        for flag in flags:
            label = RISK_FLAG_LABELS.get(flag, flag)
            counts[label] = counts.get(label, 0) + 1

    if not counts:
        return pd.DataFrame()

    rows = [
        {"risk_flag": flag, "count": count}
        for flag, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]
    return pd.DataFrame(rows)


def _quality_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    review_df = _attach_display_description_from_evidence(_build_review_reason_df(df))
    columns = [
        "action_no",
        "assignee_display",
        "display_description",
        "llm_confidence",
        "validation_score",
        "final_confidence",
        "review_required",
        "review_reason",
    ]
    return review_df[[col for col in columns if col in review_df.columns]]


def _parse_risk_flags(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(flag) for flag in value]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    if isinstance(parsed, list):
        return [str(flag) for flag in parsed]
    return [str(parsed)]


def _review_reason(row: pd.Series) -> str:
    flags = row.get("risk_flags", [])
    reasons = [RISK_FLAG_LABELS.get(flag, flag) for flag in flags]

    final_confidence = float(row.get("final_confidence") or 0)
    if final_confidence < 0.7:
        reasons.append("최종 신뢰도 0.7 미만")

    if reasons:
        return ", ".join(dict.fromkeys(reasons))
    if bool(row.get("review_required")):
        return "검토 필요로 표시됨"
    return "검토 불필요"


def _with_action_numbers(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "action_no" in df.columns:
        return df
    numbered = df.copy()
    numbered.insert(0, "action_no", range(1, len(numbered) + 1))
    return numbered


def _sort_actions(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    # due_date가 있으면 가장 빠른 날짜 우선, null은 후순위
    df["_due"] = pd.to_datetime(df.get("due_date"), errors="coerce")
    df["_has_due"] = df["_due"].notna().astype(int)
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    df["_pri"] = df.get("priority", pd.Series("medium", index=df.index)).map(priority_rank).fillna(1)
    seq_cols = [c for c in ["start_sequence_no", "sequence_no", "action_item_id"] if c in df.columns]
    sorted_df = df.sort_values(
        ["_has_due", "_due", "_pri"] + seq_cols,
        ascending=[False, True, True] + [True] * len(seq_cols),
    ).reset_index(drop=True)
    return sorted_df.drop(columns=["_due", "_has_due", "_pri"])


def _priority_reason(row: object) -> str:
    get_value = row.get if hasattr(row, "get") else lambda key, default=None: default
    priority = get_value("priority", "medium")
    evidence = f"{get_value('description', '')} {get_value('evidence_summary', '')}".lower()
    if priority == "high":
        if any(marker in evidence for marker in ["오늘", "내일", "오전까지", "이번 주", "바로", "급", "안 오면", "컨펌"]):
            return "마감/긴급/블로킹 표현이 포함됨"
        return "추출기가 높은 우선순위로 분류"
    if priority == "low":
        return "추후/선택적 후속 작업 성격"
    return "명확한 긴급 마감이 없어 기본 후속 작업으로 분류"


def _meeting_label(meeting_df: pd.DataFrame, meeting_id: str) -> str:
    row = meeting_df[meeting_df["meeting_id"] == meeting_id].iloc[0]
    return f"{row['title']} ({meeting_id})"


def _action_label(action_df: pd.DataFrame, action_item_id: str) -> str:
    row = action_df[action_df["action_item_id"] == action_item_id].iloc[0]
    prefix = f"액션 아이템 {row['action_no']} " if "action_no" in row else ""
    assignee = row.get("assignee_display") or _assignee_label(row.get("assignee_normalized"))
    return f"{prefix}{assignee} - {row['description']}"


def _render_meeting_summary(client: object, meeting_id: str) -> None:
    row = client.fetch_one(
        "SELECT agenda_json, decisions_json, summary_text, provider FROM meeting_summaries WHERE meeting_id = ?",
        (meeting_id,),
    )

    st.subheader("회의록 요약")

    if not row:
        st.info("회의록 요약이 없습니다. 파이프라인을 재실행하면 자동으로 생성됩니다.")
        if st.button("요약 생성"):
            from extraction.summarizer import summarize_and_store
            from ingestion.transcript_builder import load_transcript_json
            meeting = client.fetch_one("SELECT transcript_path FROM meetings WHERE meeting_id = ?", (meeting_id,))
            if meeting and meeting.get("transcript_path"):
                transcript = load_transcript_json(meeting["transcript_path"])
                summarize_and_store(client, meeting_id, transcript)
                st.rerun()
            else:
                st.warning("저장된 트랜스크립트 파일을 찾을 수 없습니다.")
        return

    agenda = json.loads(row["agenda_json"] or "[]")
    decisions = json.loads(row["decisions_json"] or "[]")
    summary_text = row.get("summary_text") or ""

    col_agenda, col_decisions = st.columns(2)

    with col_agenda:
        st.markdown("**논의 안건**")
        for item in agenda:
            st.markdown(f"- {item}")

    with col_decisions:
        st.markdown("**주요 결정 사항**")
        for item in decisions:
            st.markdown(f"- {item}")

    if summary_text:
        st.markdown("**전체 요약**")
        st.write(summary_text)



def render_upload(db_backend: str, db_path: str, database_url: str) -> None:
    st.subheader("회의 파일 업로드")

    uploaded_file = st.file_uploader(
        "파일 선택",
        type=["mp3", "wav", "m4a", "flac", "mp4", "mkv"],
        help="MP4는 Zoom·Teams 화면 녹화 등 영상 파일도 가능합니다. 오디오 트랙을 자동 추출합니다.",
    )

    title = st.text_input(
        "회의 제목",
        placeholder="예: 노바드림 5월 캠페인 킥오프",
        help="대시보드와 DB에 저장될 회의 이름입니다.",
    )
    num_speakers = st.number_input(
        "회의 참여 인원 수",
        min_value=1,
        max_value=20,
        value=None,
        step=1,
        placeholder="예: 3",
        help="비워두면 모델이 화자 수를 자동 판단합니다.",
    )
    st.caption("비워두면 모델이 화자 수를 자동 판단합니다.")

    run_disabled = uploaded_file is None or not title.strip()
    if st.button("처리 시작", disabled=run_disabled, type="primary"):
        _run_upload_pipeline(
            uploaded_file=uploaded_file,
            title=title.strip(),
            num_speakers=int(num_speakers) if num_speakers is not None else None,
            db_backend=db_backend,
            db_path=db_path,
            database_url=database_url,
        )


def _run_upload_pipeline(
    uploaded_file: object,
    title: str,
    num_speakers: int | None,
    db_backend: str,
    db_path: str,
    database_url: str,
) -> None:
    from analytics.embeddings import generate_and_store_embeddings
    from ingestion.audio_loader import load_audio_metadata
    from ingestion.diarization import PyannoteDiarizer, fallback_single_speaker
    from ingestion.stt import FasterWhisperSTT
    from ingestion.transcript_builder import build_transcript, save_transcript_json
    from models import stable_hash, utc_now_iso
    from pipeline import (
        _extract_and_store,
        _store_chunks,
        _store_transcript,
        build_db_client,
        check_evidence_integrity,
    )
    from preprocessing.chunker import build_chunks
    from preprocessing.normalizer import normalize_speakers, remove_consecutive_duplicates

    # ── 파일 저장 ────────────────────────────────────────────────────
    upload_dir = Path("data/raw")
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w가-힣\-]", "_", title)[:60]
    suffix = Path(uploaded_file.name).suffix.lower()
    save_path = upload_dir / f"{safe_name}{suffix}"
    save_path.write_bytes(uploaded_file.getbuffer())

    audio_path = save_path
    if suffix in {".mp4", ".mkv"}:
        with st.spinner("MP4에서 오디오 추출 중 ..."):
            wav_path = save_path.with_suffix(".wav")
            ok, err = _convert_mp4_to_wav(save_path, wav_path)
        if not ok:
            st.error(
                f"MP4 오디오 추출 실패: {err}\n\n"
                "ffmpeg가 설치되어 있지 않으면 MP3 파일로 변환 후 다시 업로드해 주세요."
            )
            return
        audio_path = wav_path

    transcript_output = Path(f"data/processed/transcript_{safe_name}.json")
    pg_dsn = database_url if db_backend == "postgres" else None

    try:
        client = build_db_client(db_backend=db_backend, db_path=db_path, pg_dsn=pg_dsn)
        client.init_schema()
        audio_metadata = load_audio_metadata(audio_path, title=title)

        # ── 1단계: STT ───────────────────────────────────────────────
        with st.spinner("음성 인식(STT) 중 ..."):
            stt_result = FasterWhisperSTT().transcribe(audio_metadata.audio_path)
        st.success(f"음성 인식 완료 — 발화 {len(stt_result.segments)}개")

        # ── 2단계: 화자 분리 ─────────────────────────────────────────
        speaker_hint_text = f"{num_speakers}명 힌트" if num_speakers else "자동 판단"
        with st.spinner(f"화자 분리 중 ({speaker_hint_text}) ..."):
            diarization_end = max(
                (s.end_sec for s in stt_result.segments),
                default=audio_metadata.duration_sec or 0.0,
            )
            try:
                diarization_result = PyannoteDiarizer(num_speakers=num_speakers).diarize(audio_metadata.audio_path)
                diarization_status = "completed"
                diarization_error = None
            except Exception as exc:
                diarization_result = fallback_single_speaker(0.0, diarization_end)
                diarization_status = "fallback"
                diarization_error = str(exc)
        st.success(f"화자 분리 완료 — {diarization_result.speaker_count}명 감지")

        # ── 3단계: 트랜스크립트 구성 + DB 저장 ──────────────────────
        with st.spinner("트랜스크립트 구성 및 저장 중 ..."):
            transcript = build_transcript(audio_metadata, stt_result, diarization_result)
            saved_path = save_transcript_json(transcript, transcript_output)
            transcript.meeting.transcript_path = str(saved_path)

            # 정규화·중복 제거를 먼저 적용한 뒤 저장·청킹해야
            # utterances와 chunk/추출이 같은 utterance_id를 공유한다.
            # (CLI 파이프라인 run_pipeline과 동일한 순서)
            transcript = remove_consecutive_duplicates(normalize_speakers(transcript))

            _store_transcript(client, transcript)
            client.upsert(
                "stt_runs",
                {
                    "stt_run_id": stable_hash("stt", transcript.meeting.meeting_id),
                    "meeting_id": transcript.meeting.meeting_id,
                    "audio_path": str(audio_metadata.audio_path),
                    "audio_hash": audio_metadata.audio_hash,
                    "stt_model": stt_result.model_name,
                    "diarization_model": diarization_result.model_name,
                    "language": stt_result.language or "ko",
                    "duration_sec": audio_metadata.duration_sec,
                    "segment_count": len(stt_result.segments),
                    "speaker_count": diarization_result.speaker_count,
                    "status": diarization_status,
                    "error_message": diarization_error,
                },
                conflict_columns=["stt_run_id"],
                update_columns=[
                    "audio_path", "audio_hash", "stt_model", "diarization_model",
                    "language", "duration_sec", "segment_count", "speaker_count",
                    "status", "error_message",
                ],
            )

            chunks = build_chunks(transcript)
            _store_chunks(client, chunks)

        # ── 4단계: 액션 추출 ─────────────────────────────────────────
        with st.spinner("액션 추출 중 (LLM) ..."):
            _extract_and_store(client, transcript.meeting.meeting_id, chunks)
        st.success("액션 추출 완료")

        # 근거 발화 연결 정합성 점검 (저장/추출 utterance_id 불일치 방어막)
        for warning in check_evidence_integrity(client, transcript.meeting.meeting_id):
            st.warning(f"정합성 경고: {warning}")

        # ── 5단계: 회의록 요약 ───────────────────────────────────────
        with st.spinner("회의록 요약 생성 중 ..."):
            from extraction.summarizer import summarize_and_store
            summarize_and_store(client, transcript.meeting.meeting_id, transcript)
        st.success("회의록 요약 완료")

        # ── 6단계: 임베딩 ────────────────────────────────────────────
        with st.spinner("유사도 검색용 임베딩 생성 중 ..."):
            generate_and_store_embeddings(client, transcript.meeting.meeting_id)

        st.balloons()
        st.success(f"'{title}' 회의 처리가 모두 완료됐습니다. 사이드바에서 회의를 선택하세요.")
        get_client.clear()
        st.rerun()

    except Exception as exc:
        st.error(f"처리 중 오류 발생: {exc}")


def _convert_mp4_to_wav(mp4_path: Path, wav_path: Path) -> tuple[bool, str | None]:
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(mp4_path),
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(wav_path),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            return True, None
        return False, (result.stderr or "ffmpeg 오류")[-300:]
    except FileNotFoundError:
        return False, "ffmpeg를 찾을 수 없음"
    except Exception as exc:
        return False, str(exc)


if __name__ == "__main__":
    main()
