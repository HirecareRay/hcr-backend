"""모의 면접 실시간 WebSocket 왕복 테스트.

한 세션 = WS 연결 1개. 검증:
  - 접속 시 첫 질문 송신
  - binary(audio_chunk)는 즉시 응답 없이 누적 → answer_end 에 한 번에 전사
  - control answer_start → 누적 버퍼 리셋
  - control answer_end → transcript_delta(전사) + eval_delta / 빈 버퍼면 eval 만
  - control next → 다음 질문 / 마지막엔 종료 요약
  - landmark_frame 은 다운스트림 없이 흘려보냄(루프 안 깨짐)

⚠️ STT(stt.transcribe_audio)는 전부 mock — 실 OpenAI API 미호출(강사님 키 보호).
DB 가 필요 없으므로 lifespan 을 띄우지 않는 TestClient 를 그대로 쓴다.
"""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_STT_PATH = 'app.interview.stt.transcribe_audio'


def test_ws_sends_question_on_connect():
    with client.websocket_connect('/interviews/ws/s1') as ws:
        data = ws.receive_json()
        assert data['type'] == 'question'
        assert data['questionId'] == 'q1'
        assert data['ttsText']  # camelCase 직렬화 확인


def test_ws_audio_accumulates_then_transcribes_on_answer_end(monkeypatch):
    transcribe = AsyncMock(return_value='누적된 답변 전사')
    monkeypatch.setattr(_STT_PATH, transcribe)

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_start'})
        ws.send_bytes(b'chunk-1')
        ws.send_bytes(b'chunk-2')  # binary 는 즉시 다운스트림 없음
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        transcript = ws.receive_json()
        eval_event = ws.receive_json()

    assert transcript['type'] == 'transcript_delta'
    assert transcript['delta'] == '누적된 답변 전사'
    assert transcript['isFinal'] is True
    assert eval_event['type'] == 'eval_delta'
    # 두 청크가 합쳐져 한 번에 전사됨
    transcribe.assert_awaited_once()
    assert transcribe.await_args.args[0] == b'chunk-1chunk-2'


def test_ws_answer_end_without_audio_streams_eval_only(monkeypatch):
    transcribe = AsyncMock(return_value='쓰이면 안 됨')
    monkeypatch.setattr(_STT_PATH, transcribe)

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        event = ws.receive_json()

    assert event['type'] == 'eval_delta'  # 자막 없이 평가만
    transcribe.assert_not_awaited()  # 빈 버퍼 → 전사 호출 안 함(과금 방지)


def test_ws_answer_start_resets_buffer(monkeypatch):
    transcribe = AsyncMock(return_value='두번째 답변')
    monkeypatch.setattr(_STT_PATH, transcribe)

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_bytes(b'stale-chunk')  # 이전 누적
        ws.send_json({'type': 'control', 'action': 'answer_start'})  # 리셋
        ws.send_bytes(b'fresh-chunk')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        ws.receive_json()  # transcript
        ws.receive_json()  # eval

    # answer_start 이후 청크만 전사됨 — stale 은 버려짐
    assert transcribe.await_args.args[0] == b'fresh-chunk'


def test_ws_next_advances_question_then_summary():
    with client.websocket_connect('/interviews/ws/s1') as ws:
        assert ws.receive_json()['questionId'] == 'q1'
        ws.send_json({'type': 'control', 'action': 'next'})
        assert ws.receive_json()['questionId'] == 'q2'
        ws.send_json({'type': 'control', 'action': 'next'})
        summary = ws.receive_json()
        assert summary['type'] == 'summary'
        assert summary['overallScore'] == 80.0


def test_ws_landmark_frame_ignored_then_feedback_still_flows(monkeypatch):
    monkeypatch.setattr(_STT_PATH, AsyncMock(return_value='ok'))

    with client.websocket_connect('/interviews/ws/s1') as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({'type': 'landmark_frame', 'gaze_x': 0.1})  # 다운스트림 없음
        ws.send_bytes(b'audio')
        ws.send_json({'type': 'control', 'action': 'answer_end'})
        first = ws.receive_json()
        assert first['type'] == 'transcript_delta'  # landmark 가 루프를 깨지 않음
