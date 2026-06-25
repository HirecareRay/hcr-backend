"""모의 면접 실시간 WebSocket 왕복 테스트 (Phase 3: LLM 두뇌).

한 세션 = WS 연결 1개. B안 진행 검증:
  - 접속 시 컨텍스트 기반 메인 질문(m0) 송신
  - binary(audio_chunk)는 즉시 응답 없이 누적 → answer_end 에 한 번에 전사
  - answer_end → transcript_delta(전사) + eval_delta 토큰 스트림 / 빈 버퍼면 평가 생략
  - next: 메인 답변 직후 → 꼬리질문(f{idx}) / 꼬리 답변 직후 → 다음 메인(m{idx})
  - 메인 소진 후 next → summary
  - landmark_frame 은 다운스트림 없이 흘려보냄(루프 안 깨짐)

⚠️ llm·stt 는 전부 mock — 실 OpenAI API 미호출(강사님 키 보호). DB 가 필요 없으므로
lifespan 을 띄우지 않는 TestClient 를 그대로 쓴다.
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from app.interview import llm, nonverbal, stt
from app.main import app

client = TestClient(app)


def _drain(ws, count: int) -> None:
    """다운스트림 이벤트 count 개를 받아 버린다(다음 단계 검증 준비)."""
    for _ in range(count):
        ws.receive_json()


def _stream_factory(deltas: list[str]):
    """llm.stream_evaluation 을 대체할 async 제너레이터 팩토리."""

    async def _gen(question: str, answer: str) -> AsyncIterator[str]:
        for delta in deltas:
            yield delta

    return _gen


def _patch_llm(
    monkeypatch,
    *,
    main_questions: list[str] | None = None,
    follow_up: str = '그 경험에서 가장 어려웠던 점은 무엇이었나요?',
    eval_deltas: list[str] | None = None,
    summary: dict | None = None,
    transcribe: str = '제 강점은 협업입니다',
) -> None:
    """면접 LLM·STT 경계를 결정론적 mock 으로 대체한다(실 API 미호출)."""
    monkeypatch.setattr(
        llm,
        'generate_main_questions',
        AsyncMock(return_value=main_questions or ['자기소개 부탁드립니다', '지원 동기는?']),
    )
    monkeypatch.setattr(llm, 'generate_follow_up', AsyncMock(return_value=follow_up))
    monkeypatch.setattr(
        llm, 'stream_evaluation', _stream_factory(eval_deltas or ['좋은 ', '답변이네요'])
    )
    monkeypatch.setattr(
        llm,
        'generate_summary',
        AsyncMock(return_value=summary or {'overall_score': 88, 'language_feedback': '논리적', 'improvements': ['결론 강화']}),
    )
    monkeypatch.setattr(stt, 'transcribe_audio', AsyncMock(return_value=transcribe))


def test_ws_sends_generated_main_question_on_connect(monkeypatch):
    _patch_llm(monkeypatch, main_questions=['자기소개를 부탁드립니다', '강점은?'])

    with client.websocket_connect('/interviews/ws/s1') as ws:
        data = ws.receive_json()

    assert data['type'] == 'question'
    assert data['questionId'] == 'm0'
    assert data['text'] == '자기소개를 부탁드립니다'
    assert data['ttsText'] == '자기소개를 부탁드립니다'  # camelCase 직렬화 확인


def test_ws_audio_accumulates_then_transcribes_and_streams_eval(monkeypatch):
    _patch_llm(monkeypatch, eval_deltas=['답변 ', '구조가 ', '명확합니다'])

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        ws.send_bytes(b'chunk-1')
        ws.send_bytes(b'chunk-2')  # binary 는 즉시 다운스트림 없음
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        transcript = ws.receive_json()
        evals = [ws.receive_json() for _ in range(3)]

    assert transcript['type'] == 'transcript_delta'
    assert transcript['delta'] == '제 강점은 협업입니다'
    assert transcript['isFinal'] is True
    assert [e['type'] for e in evals] == ['eval_delta'] * 3  # 토큰 스트림
    assert ''.join(e['delta'] for e in evals) == '답변 구조가 명확합니다'
    # 두 청크가 합쳐져 한 번에 전사됨
    stt.transcribe_audio.assert_awaited_once()
    assert stt.transcribe_audio.await_args.args[0] == b'chunk-1chunk-2'


def test_ws_answer_end_without_audio_skips_transcribe_and_eval(monkeypatch):
    _patch_llm(monkeypatch)

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_end'})  # 빈 답변
        # 빈 답변은 자막·평가 모두 생략 → 다음 메인으로 넘어가는지로 확인
        ws.send_json({'type': 'control', 'action': 'next'})
        nxt = ws.receive_json()

    # 답변이 없어 꼬리질문도 생략 → 곧장 다음 메인 질문
    assert nxt['questionId'] == 'm1'
    stt.transcribe_audio.assert_not_awaited()  # 빈 버퍼 → 전사 호출 안 함(과금 방지)


def test_ws_answer_start_resets_buffer(monkeypatch):
    _patch_llm(monkeypatch, eval_deltas=['ok'])

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_bytes(b'stale-chunk')  # 이전 누적
        ws.send_json({'type': 'control', 'action': 'answer_start'})  # 리셋
        ws.send_bytes(b'fresh-chunk')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        ws.receive_json()  # transcript
        ws.receive_json()  # eval

    # answer_start 이후 청크만 전사됨 — stale 은 버려짐
    assert stt.transcribe_audio.await_args.args[0] == b'fresh-chunk'


def test_ws_next_after_main_answer_sends_follow_up(monkeypatch):
    _patch_llm(monkeypatch, follow_up='그 협업에서 본인의 역할은 무엇이었나요?')

    with client.websocket_connect('/interviews/ws/s1') as ws:
        assert ws.receive_json()['questionId'] == 'm0'
        ws.send_bytes(b'audio')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        ws.receive_json()  # transcript
        ws.receive_json()  # eval(들) — 기본 2개 중 첫
        ws.receive_json()
        ws.send_json({'type': 'control', 'action': 'next'})
        follow = ws.receive_json()

    assert follow['type'] == 'question'
    assert follow['questionId'] == 'f0'  # 메인 m0 의 꼬리질문
    assert follow['text'] == '그 협업에서 본인의 역할은 무엇이었나요?'


def test_ws_next_after_followup_answer_advances_to_next_main(monkeypatch):
    _patch_llm(monkeypatch)

    with client.websocket_connect('/interviews/ws/s1') as ws:
        assert ws.receive_json()['questionId'] == 'm0'
        # 메인 답변 → 꼬리질문
        ws.send_bytes(b'a1')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        _drain(ws, 3)  # transcript + eval 2
        ws.send_json({'type': 'control', 'action': 'next'})
        assert ws.receive_json()['questionId'] == 'f0'
        # 꼬리 답변 → 다음 메인
        ws.send_bytes(b'a2')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        _drain(ws, 3)
        ws.send_json({'type': 'control', 'action': 'next'})
        assert ws.receive_json()['questionId'] == 'm1'


def test_ws_summary_after_main_questions_exhausted(monkeypatch):
    # 메인 1개로 줄여 빠르게 소진 → 꼬리 1번 후 요약
    _patch_llm(monkeypatch, main_questions=['자기소개 부탁드립니다'])

    with client.websocket_connect('/interviews/ws/s1') as ws:
        assert ws.receive_json()['questionId'] == 'm0'
        ws.send_bytes(b'a1')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        _drain(ws, 3)
        ws.send_json({'type': 'control', 'action': 'next'})  # 꼬리질문
        assert ws.receive_json()['questionId'] == 'f0'
        ws.send_bytes(b'a2')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        _drain(ws, 3)
        ws.send_json({'type': 'control', 'action': 'next'})  # 메인 소진 → 요약
        summary = ws.receive_json()

    assert summary['type'] == 'summary'
    assert summary['overallScore'] == 88.0
    assert summary['languageFeedback'] == '논리적'
    assert summary['improvements'] == ['결론 강화']


def test_ws_landmark_frame_has_no_downstream_response(monkeypatch):
    """비언어 프레임은 누적만 — 즉시 다운스트림 응답이 없어 루프를 깨지 않는다."""
    _patch_llm(monkeypatch, eval_deltas=['ok'])

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'landmark_frame', 'gaze_x': 0.1})  # 다운스트림 없음
        ws.send_bytes(b'audio')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        first = ws.receive_json()

    assert first['type'] == 'transcript_delta'  # landmark 다음 바로 전사 — 끼어든 응답 없음


def test_ws_accumulated_landmarks_reflected_in_summary(monkeypatch):
    """답변 중 보낸 시선이탈 landmark 가 최종 요약의 비언어 피드백·점수에 반영된다."""
    _patch_llm(
        monkeypatch,
        main_questions=['자기소개 부탁드립니다'],
        eval_deltas=['ok'],  # transcript + eval 1개 → _drain(2)
        summary={'overall_score': 90, 'language_feedback': '논리적', 'improvements': []},
    )

    with client.websocket_connect('/interviews/ws/s1') as ws:
        assert ws.receive_json()['questionId'] == 'm0'
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        for _ in range(5):
            ws.send_json({'type': 'landmark_frame', 'gaze_x': 0.95})  # 시선 이탈
        ws.send_json({'type': 'event_snapshot', 'event': 'gaze_away', 'image': 'data:,'})
        ws.send_bytes(b'audio')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        _drain(ws, 2)  # transcript + eval
        ws.send_json({'type': 'control', 'action': 'next'})  # 꼬리질문
        assert ws.receive_json()['questionId'] == 'f0'
        ws.send_bytes(b'a2')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        _drain(ws, 2)
        ws.send_json({'type': 'control', 'action': 'next'})  # 메인 소진 → 요약
        summary = ws.receive_json()

    assert summary['type'] == 'summary'
    assert '시선' in summary['nonverbalFeedback']  # 누적 landmark 반영
    assert summary['overallScore'] < 90.0  # 비언어 감점 반영


def test_ws_summary_sent_even_if_nonverbal_aggregate_raises(monkeypatch):
    """비언어 집계가 예외를 던져도 최종 요약은 끊기지 않고 전송된다(데모 보호)."""
    _patch_llm(
        monkeypatch,
        main_questions=['자기소개 부탁드립니다'],
        eval_deltas=['ok'],
        summary={'overall_score': 80, 'language_feedback': '논리적', 'improvements': []},
    )
    monkeypatch.setattr(nonverbal, 'aggregate', Mock(side_effect=RuntimeError('boom')))

    with client.websocket_connect('/interviews/ws/s1') as ws:
        assert ws.receive_json()['questionId'] == 'm0'
        ws.send_bytes(b'a1')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        _drain(ws, 2)  # transcript + eval
        ws.send_json({'type': 'control', 'action': 'next'})  # 꼬리질문
        assert ws.receive_json()['questionId'] == 'f0'
        ws.send_bytes(b'a2')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        _drain(ws, 2)
        ws.send_json({'type': 'control', 'action': 'next'})  # 메인 소진 → 요약
        summary = ws.receive_json()

    assert summary['type'] == 'summary'
    assert summary['overallScore'] == 80.0  # 집계 실패 → 감점 0 으로 우회
