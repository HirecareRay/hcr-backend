"""result_service 단위 테스트 — 영속화 오케스트레이션·직전 세션 비교·조회 소유권.

DB·LLM 은 mock 한다 — 속도·결정성·네트워크 독립성(빌린 OpenAI 키 보호) 때문이다.
저장할 곳이 없으면 LLM 도 부르지 않는 비용 단축, comparison 방향 계산, 조회 시
소유자 검증(남의 결과 차단)을 검증한다.
"""

import asyncio
from datetime import datetime, timezone

from unittest.mock import AsyncMock, Mock

from app.interview import result_builder, result_repository, result_service, service
from app.interview.result_schemas import ResultMeta


def _meta(result_id='r1') -> ResultMeta:
    return ResultMeta(
        result_id=result_id,
        company_id='c1',
        company_name='CJ ENM',
        job_title='마케팅',
        conducted_at='2026-06-29T00:00:00+00:00',
        duration_sec=120,
        mode='voice',
        question_count=1,
    )


def _result_dump(overall=78, answer=82):
    report = {
        'overall': {'score': overall, 'grade': 'B+', 'headline': 'h'},
        'answer_feedback': {'score': answer, 'summary': 's', 'metrics': []},
    }
    result = result_builder.build_result(
        meta=_meta(), history=(service.Turn('q', 'a', 'e', 'common'),), report=report
    )
    return result.model_dump(by_alias=False)


# ── summarize_and_persist: 단일 채점 소스(리포트) + 조건부 저장 ─────────────


def _summarize(monkeypatch, *, history, mongo, job_title='마케팅'):
    """summarize_and_persist 를 빈 비언어/음성 입력으로 실행하는 헬퍼."""
    return asyncio.run(
        result_service.summarize_and_persist(
            history=history,
            landmarks=(),
            events=(),
            voice_frames=(),
            user_id='u1',
            company_id=None,  # company 조회 우회(회사명 빈 값)
            job_title=job_title,
            mode='voice',
            started_at=datetime.now(timezone.utc),
            mongo=mongo,
        )
    )


def test_summarize_skips_save_but_still_summarizes_without_mongo(monkeypatch):
    """저장할 곳(mongo)이 없어도 라이브 summary 는 리포트에서 파생돼 내려간다(저장만 생략)."""
    report = {
        'overall': {'score': 77, 'grade': 'B', 'headline': 'h'},
        'answer_feedback': {'score': 77, 'summary': '논리적', 'metrics': []},
    }
    monkeypatch.setattr(
        'app.interview.llm.generate_report', AsyncMock(return_value=report)
    )
    save = Mock()
    monkeypatch.setattr(result_repository, 'save_session_result', save)

    summary = _summarize(
        monkeypatch,
        history=(service.Turn('q', 'a', 'e', 'common'),),
        mongo=None,
    )
    save.assert_not_called()  # 저장 못 하면 저장은 생략
    assert summary.overall_score == 77.0  # 그래도 리포트에서 종합점수 파생


def test_summarize_saves_and_summary_matches_report(monkeypatch):
    """저장된 결과의 overall.score 와 라이브 summary 종합점수가 일치한다(단일 소스)."""
    report = {
        'overall': {'score': 78, 'grade': 'B+', 'headline': '안정'},
        'answer_feedback': {'score': 82, 'summary': '논리', 'metrics': []},
    }
    monkeypatch.setattr(
        'app.interview.llm.generate_report', AsyncMock(return_value=report)
    )
    monkeypatch.setattr(result_repository, 'find_latest_by_user', Mock(return_value=None))
    monkeypatch.setattr(result_repository, 'count_by_user', Mock(return_value=0))
    saved = {}
    monkeypatch.setattr(
        result_repository,
        'save_session_result',
        Mock(side_effect=lambda db, doc: saved.update(doc)),
    )

    summary = _summarize(
        monkeypatch,
        history=(service.Turn('자기소개', '안녕', '명확', 'common'),),
        mongo=object(),
    )
    assert saved['user_id'] == 'u1'
    assert saved['result']['overall']['score'] == 78
    assert saved['result']['comparison'] is None  # 첫 면접
    assert summary.overall_score == 78.0  # 저장값 == 라이브 summary(어긋나지 않음)


def test_summarize_skips_llm_when_all_answers_empty(monkeypatch):
    """전부 무응답이면 리포트 LLM 을 부르지 않고, 강점 없이 0점으로 정직하게 저장·요약한다."""
    gen = AsyncMock(return_value={'strengths': ['지어낸 강점'], 'overall': {'score': 70}})
    monkeypatch.setattr('app.interview.llm.generate_report', gen)
    monkeypatch.setattr(result_repository, 'find_latest_by_user', Mock(return_value=None))
    monkeypatch.setattr(result_repository, 'count_by_user', Mock(return_value=0))
    saved = {}
    monkeypatch.setattr(
        result_repository,
        'save_session_result',
        Mock(side_effect=lambda db, doc: saved.update(doc)),
    )

    summary = _summarize(
        monkeypatch,
        history=(
            service.Turn('자기소개', '', '', 'common'),  # 무응답
            service.Turn('지원동기', '   ', '', 'common'),  # 공백만
        ),
        mongo=object(),
    )
    gen.assert_not_called()  # 빌린 키 낭비 방지 + 환각 차단
    assert saved['result']['strengths'] == []
    assert saved['result']['overall']['score'] == 0
    assert summary.overall_score == 0.0


def test_summarize_survives_persist_failure(monkeypatch):
    """저장 중 예외가 나도 라이브 summary 는 내려간다(면접 종료를 막지 않음)."""
    monkeypatch.setattr(
        'app.interview.llm.generate_report',
        AsyncMock(
            return_value={
                'overall': {'score': 65, 'grade': 'C+', 'headline': 'h'},
                'answer_feedback': {'score': 65, 'summary': 's', 'metrics': []},
            }
        ),
    )
    monkeypatch.setattr(
        result_repository,
        'find_latest_by_user',
        Mock(side_effect=RuntimeError('db down')),
    )
    # 저장 예외가 전파되면 summary 를 못 받아 이 테스트가 실패한다(흡수되어야 정상)
    summary = _summarize(
        monkeypatch,
        history=(service.Turn('q', 'a', 'e', 'common'),),
        mongo=object(),
    )
    assert summary.overall_score == 65.0  # 저장 실패해도 요약은 내려간다


def test_summarize_falls_back_when_build_result_raises(monkeypatch):
    """결과 조립이 예외를 던져도 기본 요약을 내려보낸다(면접이 끊기지 않게 — 데모 보호)."""
    monkeypatch.setattr(
        result_service,
        '_build_result',
        AsyncMock(side_effect=RuntimeError('조립 폭발')),
    )
    save = Mock()
    monkeypatch.setattr(result_repository, 'save_session_result', save)

    summary = _summarize(
        monkeypatch,
        history=(service.Turn('q', 'a', 'e', 'common'),),
        mongo=object(),
    )
    assert summary.type == 'summary'
    assert summary.overall_score == 0.0  # 안전 기본 요약
    assert summary.language_feedback  # 비어 있지 않은 안내 문구
    save.assert_not_called()  # 조립이 터졌으니 저장 경로도 타지 않는다


# ── comparison ────────────────────────────────────────────────────────


def test_build_comparison_none_when_no_previous():
    result = result_builder.build_result(
        meta=_meta(), history=(service.Turn('q', 'a', 'e', 'common'),), report={}
    )
    assert result_service._build_comparison(None, result, 1) is None


def test_build_comparison_directions_and_attempt():
    current = result_builder.build_result(
        meta=_meta(),
        history=(service.Turn('q', 'a', 'e', 'common'),),
        report={
            'overall': {'score': 78},
            'answer_feedback': {'score': 82, 'summary': 's', 'metrics': []},
        },
    )
    previous = {'result': _result_dump(overall=70, answer=82)}
    cmp = result_service._build_comparison(previous, current, attempt_count=2)
    assert cmp.attempt_count == 2
    by_label = {d.label: d for d in cmp.deltas}
    assert by_label['종합'].direction == 'up' and by_label['종합'].delta == 8
    assert by_label['답변'].direction == 'same'  # 82 == 82


def test_comparison_skips_expression_when_no_data_this_session():
    """이번 세션에 표정 데이터가 없으면(카메라 미사용) 표정 델타를 넣지 않는다.

    빈 모달의 0 을 직전 실제 점수와 비교해 '점수가 떨어졌다'고 왜곡하지 않는다.
    """
    from app.interview.result_schemas import FeedbackMetric, ModalFeedback

    turn = (service.Turn('q', 'a', 'e', 'common'),)
    report = {'overall': {'score': 80}, 'answer_feedback': {'score': 80, 'summary': 's', 'metrics': []}}
    # 직전 세션은 표정 지표가 있었다(실제 점수 75)
    prev_result = result_builder.build_result(
        meta=_meta(),
        history=turn,
        report=report,
        expression=ModalFeedback(
            score=75,
            summary='시선 양호',
            metrics=[FeedbackMetric(label='시선 처리', score=75, value='이탈 25%', comment='c')],
        ),
    )
    previous = {'result': prev_result.model_dump(by_alias=False)}
    # 이번 세션은 표정 데이터 없음(빈 모달)
    current = result_builder.build_result(meta=_meta(), history=turn, report=report)

    cmp = result_service._build_comparison(previous, current, attempt_count=2)
    labels = {d.label for d in cmp.deltas}
    assert '표정' not in labels  # 데이터 없어 비교 제외
    assert '종합' in labels and '답변' in labels  # 언어 평가는 항상 비교


# ── 조회 소유권 ────────────────────────────────────────────────────────


def test_get_result_by_id_rejects_non_owner(monkeypatch):
    monkeypatch.setattr(
        result_repository,
        'find_by_id',
        Mock(return_value={'user_id': 'owner', 'result': _result_dump()}),
    )
    assert result_service.get_result_by_id(object(), 'intruder', 'r1') is None
    assert result_service.get_result_by_id(object(), 'owner', 'r1') is not None


def test_get_result_by_company_restores_or_none(monkeypatch):
    monkeypatch.setattr(
        result_repository,
        'find_latest_by_company',
        Mock(return_value={'user_id': 'u1', 'result': _result_dump(overall=91)}),
    )
    result = result_service.get_result_by_company(object(), 'u1', 'c1')
    assert result is not None and result.overall.score == 91

    monkeypatch.setattr(
        result_repository, 'find_latest_by_company', Mock(return_value=None)
    )
    assert result_service.get_result_by_company(object(), 'u1', 'c1') is None
