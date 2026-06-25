"""모의 면접 실시간 WebSocket 왕복 테스트 (Phase 1 — walking skeleton).

한 세션 = WS 연결 1개. 더미 데이터 왕복을 검증한다:
  - 접속 시 첫 질문 송신
  - control answer_end → 전사·평가 스트림
  - control next → 다음 질문 / 마지막엔 종료 요약
  - binary(audio_chunk) → 수신 확인 자막
  - landmark_frame 은 다운스트림 없이 흘려보냄(루프 안 깨짐)

DB 가 필요 없으므로 lifespan 을 띄우지 않는 TestClient 를 그대로 쓴다.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ws_sends_question_on_connect():
    with client.websocket_connect("/interviews/ws/s1") as ws:
        data = ws.receive_json()
        assert data["type"] == "question"
        assert data["questionId"] == "q1"
        assert data["ttsText"]  # camelCase 직렬화 확인


def test_ws_answer_end_streams_transcript_then_eval():
    with client.websocket_connect("/interviews/ws/s1") as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({"type": "control", "action": "answer_end"})
        first = ws.receive_json()
        second = ws.receive_json()
        assert first["type"] == "transcript_delta"
        assert first["isFinal"] is True
        assert second["type"] == "eval_delta"


def test_ws_next_advances_question_then_summary():
    with client.websocket_connect("/interviews/ws/s1") as ws:
        assert ws.receive_json()["questionId"] == "q1"
        ws.send_json({"type": "control", "action": "next"})
        assert ws.receive_json()["questionId"] == "q2"
        ws.send_json({"type": "control", "action": "next"})
        summary = ws.receive_json()
        assert summary["type"] == "summary"
        assert summary["overallScore"] == 80.0


def test_ws_binary_audio_is_acked():
    with client.websocket_connect("/interviews/ws/s1") as ws:
        ws.receive_json()  # 첫 질문
        ws.send_bytes(b"fake-audio-bytes")
        ack = ws.receive_json()
        assert ack["type"] == "transcript_delta"
        assert "16" in ack["delta"]  # 16바이트 수신 확인


def test_ws_landmark_frame_ignored_then_feedback_still_flows():
    with client.websocket_connect("/interviews/ws/s1") as ws:
        ws.receive_json()  # 첫 질문
        ws.send_json({"type": "landmark_frame", "gaze_x": 0.1})  # 다운스트림 없음
        ws.send_json({"type": "control", "action": "answer_end"})
        first = ws.receive_json()
        assert first["type"] == "transcript_delta"  # landmark 가 루프를 깨지 않음
