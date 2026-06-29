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

from app.core.config import settings
from app.interview import llm, nonverbal, router, service, stt, ws_ticket
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
    """면접 LLM·STT 경계를 결정론적 mock 으로 대체한다(실 API 미호출).

    더미 자막 플래그는 명시적으로 끈다 — 이 헬퍼를 쓰는 테스트는 실 STT 경로
    (answer_end 통전사) 를 검증하므로, 환경변수 누수로 더미가 켜져도 영향받지 않게 한다.
    """
    monkeypatch.setattr(settings, 'interview_dummy_transcript', False)
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


def test_ws_connect_with_query_params_still_returns_first_question(monkeypatch):
    """companyId·ticket 쿼리를 받아도 DB 미연결 환경에선 mock 으로 우회해 첫 질문이 나온다."""
    _patch_llm(monkeypatch, main_questions=['자기소개를 부탁드립니다', '강점은?'])

    with client.websocket_connect(
        '/interviews/ws/s1?companyId=abc123&ticket=invalid-ticket'
    ) as ws:
        data = ws.receive_json()

    assert data['type'] == 'question'
    assert data['questionId'] == 'm0'


def test_ws_ticket_endpoint_issues_ticket_with_valid_bearer(monkeypatch):
    """Bearer JWT 가 유효하면 200 + {ticket, expiresIn} 를 발급한다."""
    monkeypatch.setattr(router, 'decode_access_token', lambda token: '42')

    res = client.post(
        '/interviews/ws-ticket',
        headers={'Authorization': 'Bearer good-token'},
    )

    assert res.status_code == 200
    body = res.json()
    assert isinstance(body['ticket'], str) and body['ticket']
    assert body['expiresIn'] == settings.interview_ws_ticket_ttl_seconds


def test_ws_ticket_endpoint_rejects_missing_bearer():
    """Authorization 헤더가 없으면 401."""
    assert client.post('/interviews/ws-ticket').status_code == 401


def test_ws_ticket_endpoint_rejects_invalid_jwt(monkeypatch):
    """JWT 가 무효면 401 (티켓 발급 거부)."""
    import jwt

    def _raise(token):
        raise jwt.InvalidTokenError('bad')

    monkeypatch.setattr(router, 'decode_access_token', _raise)

    res = client.post(
        '/interviews/ws-ticket',
        headers={'Authorization': 'Bearer bad-token'},
    )
    assert res.status_code == 401


def test_ws_ticket_personalizes_and_is_consumed(monkeypatch):
    """유효 티켓+companyId 면 그 user_id 가 질문 생성에 주입되고, 티켓은 1회용으로 폐기된다."""
    _patch_llm(monkeypatch, main_questions=['자기소개를 부탁드립니다', '강점은?'])
    captured: dict[str, object] = {}

    async def _capture(count, *, company_id=None, user_id=None, db=None, mongo=None):
        captured['company_id'] = company_id
        captured['user_id'] = user_id
        return ['자기소개를 부탁드립니다', '강점은?']

    monkeypatch.setattr(service, 'build_main_questions', _capture)

    ticket, _ = ws_ticket.issue_ticket('42', ttl_seconds=60)
    url = f'/interviews/ws/s1?companyId=6a3ca079d7da326c0781963c&ticket={ticket}'
    with client.websocket_connect(url) as ws:
        ws.receive_json()

    assert captured['user_id'] == '42'
    assert captured['company_id'] == '6a3ca079d7da326c0781963c'
    # 1회용 — 같은 티켓을 다시 소비하면 None(익명)
    assert ws_ticket.consume_ticket(ticket) is None


def test_ws_without_ticket_falls_back_to_anonymous(monkeypatch):
    """티켓이 없으면 user_id=None(익명)으로 우회하고 면접은 그대로 진행된다."""
    _patch_llm(monkeypatch, main_questions=['자기소개를 부탁드립니다', '강점은?'])
    captured: dict[str, object] = {}

    async def _capture(count, *, company_id=None, user_id=None, db=None, mongo=None):
        captured['user_id'] = user_id
        return ['자기소개를 부탁드립니다', '강점은?']

    monkeypatch.setattr(service, 'build_main_questions', _capture)

    with client.websocket_connect('/interviews/ws/s1') as ws:
        data = ws.receive_json()

    assert data['type'] == 'question'
    assert captured['user_id'] is None


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
    # 메인 1개로 줄여 빠르게 소진 → 꼬리 1번 후 요약.
    # count 도 1 로 맞춰 부족분 보충(_ensure_question_count)이 끼어들지 않게 한다.
    monkeypatch.setattr(settings, 'interview_main_question_count', 1)
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
    # 메인 1개로 빠르게 소진 → count 도 1 로 맞춰 부족분 보충이 끼어들지 않게 한다.
    monkeypatch.setattr(settings, 'interview_main_question_count', 1)
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
    # 메인 1개로 빠르게 소진 → count 도 1 로 맞춰 부족분 보충이 끼어들지 않게 한다.
    monkeypatch.setattr(settings, 'interview_main_question_count', 1)
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


# ── 텍스트 모드 답변 (text_answer) ─────────────────────────────────────


def test_ws_text_answer_used_as_answer_and_streams_eval(monkeypatch):
    """타이핑 답변(text_answer)이 오면 전사 없이 그 텍스트로 자막(final)+평가를 낸다."""
    _patch_llm(monkeypatch, eval_deltas=['좋은 ', '답변'])

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        ws.send_json({'type': 'text_answer', 'text': '제 강점은 끈기입니다'})
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        transcript = ws.receive_json()
        evals = [ws.receive_json() for _ in range(2)]

    assert transcript['type'] == 'transcript_delta'
    assert transcript['delta'] == '제 강점은 끈기입니다'  # 타이핑 본문이 자막으로
    assert transcript['isFinal'] is True
    assert ''.join(e['delta'] for e in evals) == '좋은 답변'  # 그 텍스트로 평가
    stt.transcribe_audio.assert_not_awaited()  # 타이핑 답변 → 전사 미호출(과금 0)


def test_ws_text_answer_takes_precedence_over_audio(monkeypatch):
    """타이핑 답변이 있으면 같은 답변에 보낸 오디오는 무시하고 텍스트를 쓴다."""
    _patch_llm(monkeypatch, eval_deltas=['ok'])

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        ws.send_bytes(b'audio-chunk')  # 오디오도 보냄
        ws.send_json({'type': 'text_answer', 'text': '타이핑이 우선'})
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        transcript = ws.receive_json()
        ws.receive_json()  # eval

    assert transcript['delta'] == '타이핑이 우선'
    stt.transcribe_audio.assert_not_awaited()  # 오디오 무시 → 전사 안 함


def test_ws_answer_start_clears_typed_answer(monkeypatch):
    """answer_start 가 직전 타이핑 답변을 비워, 새 답변이 빈 채면 평가를 생략한다."""
    _patch_llm(monkeypatch)

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'text_answer', 'text': '버려질 답변'})
        ws.send_json({'type': 'control', 'action': 'answer_start'})  # 리셋
        ws.send_json({'type': 'control', 'action': 'answer_end'})  # 빈 답변
        ws.send_json({'type': 'control', 'action': 'next'})
        nxt = ws.receive_json()

    # 타이핑 답변이 리셋돼 빈 답변 → 꼬리질문 생략 → 곧장 다음 메인
    assert nxt['questionId'] == 'm1'
    stt.transcribe_audio.assert_not_awaited()


# ── 실시간 부분 자막 (interview_partial_transcript) ────────────────────


def _enable_partial(monkeypatch, *, every: int, transcripts: list[str]) -> None:
    """부분 자막 모드를 켜고, 누적 버퍼 재전사가 점점 길어지는 텍스트를 반환하게 한다."""
    monkeypatch.setattr(settings, 'interview_partial_transcript', True)
    monkeypatch.setattr(settings, 'interview_partial_transcript_every', every)
    monkeypatch.setattr(stt, 'transcribe_audio', AsyncMock(side_effect=transcripts))


def test_ws_partial_transcript_streams_while_answering(monkeypatch):
    """부분 자막 모드: every 청크마다 부분 자막(isFinal=False)을 흘리고,
    answer_end 에 최종 전사로 남은 꼬리만 final 로 보낸다."""
    _patch_llm(monkeypatch, eval_deltas=['ok'])
    _enable_partial(
        monkeypatch, every=2, transcripts=['안녕하세요', '안녕하세요 반갑습니다']
    )

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        ws.send_bytes(b'c1')
        ws.send_bytes(b'c2')  # every=2 → 부분 전사 1회
        partial = ws.receive_json()
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        final = ws.receive_json()
        ws.receive_json()  # eval

    assert partial['type'] == 'transcript_delta'
    assert partial['isFinal'] is False
    assert partial['delta'] == '안녕하세요'
    assert final['isFinal'] is True
    assert final['delta'] == ' 반갑습니다'  # 이미 보낸 부분 뒤 꼬리만
    assert stt.transcribe_audio.await_count == 2  # 부분 1 + 최종 1


def test_ws_partial_transcript_final_close_marker_when_no_new_text(monkeypatch):
    """최종 전사가 부분 자막과 같으면 새 꼬리가 없어 빈 종료 마커(delta='')를 보낸다."""
    _patch_llm(monkeypatch, eval_deltas=['ok'])
    _enable_partial(monkeypatch, every=2, transcripts=['안녕하세요', '안녕하세요'])

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        ws.send_bytes(b'c1')
        ws.send_bytes(b'c2')
        partial = ws.receive_json()
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        final = ws.receive_json()
        ws.receive_json()  # eval

    assert partial['delta'] == '안녕하세요'
    assert final['delta'] == ''  # 새 내용 없음 → 종료 마커
    assert final['isFinal'] is True


def test_ws_partial_below_threshold_skips_partial_but_finalizes(monkeypatch):
    """청크가 간격(every)에 못 미치면 부분 자막 없이, answer_end 에 전체를 final 로 낸다."""
    _patch_llm(monkeypatch, eval_deltas=['ok'])
    _enable_partial(monkeypatch, every=5, transcripts=['전체 답변입니다'])

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        ws.send_bytes(b'c1')
        ws.send_bytes(b'c2')  # every=5 미달 → 부분 자막 없음
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        final = ws.receive_json()
        ws.receive_json()  # eval

    assert final['type'] == 'transcript_delta'
    assert final['isFinal'] is True
    assert final['delta'] == '전체 답변입니다'  # 부분이 없었으니 전체가 꼬리
    assert stt.transcribe_audio.await_count == 1  # 최종 전사 1회만


# ── 더미 자막 스트리밍 모드 (interview_dummy_transcript=True) ──────────


def test_ws_dummy_mode_streams_partial_transcript_per_chunk(monkeypatch):
    """더미 모드: 오디오 청크마다 부분 자막(isFinal=False)이 즉시 흐르고,
    answer_end 에 종료 마커(isFinal=True)가 온 뒤 평가가 스트리밍된다.
    실 STT(gpt-4o-mini-transcribe)는 호출되지 않는다(과금 0)."""
    _patch_llm(monkeypatch, eval_deltas=['좋은 ', '답변'])
    monkeypatch.setattr(settings, 'interview_dummy_transcript', True)

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        ws.send_bytes(b'c1')
        p1 = ws.receive_json()  # 청크 즉시 부분 자막
        ws.send_bytes(b'c2')
        p2 = ws.receive_json()
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        final = ws.receive_json()  # 종료 마커
        evals = [ws.receive_json() for _ in range(2)]

    assert p1['type'] == 'transcript_delta' and p1['isFinal'] is False
    assert p1['delta']  # 비지 않은 토큰
    assert p2['type'] == 'transcript_delta' and p2['isFinal'] is False
    assert p1['delta'] != p2['delta']  # 청크마다 다른 토큰이 이어짐
    assert final['type'] == 'transcript_delta' and final['isFinal'] is True
    assert [e['type'] for e in evals] == ['eval_delta'] * 2  # 더미 답변도 평가됨
    stt.transcribe_audio.assert_not_awaited()  # 더미 모드 → STT 미호출(과금 0)


def test_ws_dummy_mode_empty_answer_skips_final_and_eval(monkeypatch):
    """더미 모드라도 청크가 하나도 없으면 종료 마커·평가를 생략한다(실 경로와 동일 규칙)."""
    _patch_llm(monkeypatch)
    monkeypatch.setattr(settings, 'interview_dummy_transcript', True)

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_start'})  # 청크 없음
        ws.send_json({'type': 'control', 'action': 'answer_end'})  # 빈 답변
        ws.send_json({'type': 'control', 'action': 'next'})
        nxt = ws.receive_json()

    # 빈 답변 → 꼬리질문도 생략 → 곧장 다음 메인 질문
    assert nxt['questionId'] == 'm1'
    stt.transcribe_audio.assert_not_awaited()


def test_ws_dummy_mode_resets_token_sequence_each_answer(monkeypatch):
    """answer_start 가 자막 토큰 순번을 리셋해, 새 답변의 첫 자막이 다시 첫 토큰부터 시작한다."""
    _patch_llm(monkeypatch, main_questions=['자기소개', '강점은?'], eval_deltas=['ok'])
    monkeypatch.setattr(settings, 'interview_dummy_transcript', True)

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # m0
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        ws.send_bytes(b'a1')
        first_answer_token = ws.receive_json()['delta']
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        _drain(ws, 2)  # 종료 마커 + eval
        ws.send_json({'type': 'control', 'action': 'next'})  # 꼬리질문
        ws.receive_json()
        # 새 답변 시작 → 토큰 순번 리셋
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        ws.send_bytes(b'b1')
        second_answer_token = ws.receive_json()['delta']

    assert first_answer_token == second_answer_token  # 둘 다 첫 토큰부터
