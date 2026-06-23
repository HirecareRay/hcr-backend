"""헬스체크·기본 라우트 스모크 테스트.

실행: pip install -r requirements-dev.txt 후 `pytest`
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_company_report_returns_camel_case():
    """응답이 camelCase 키로 직렬화되는지 확인 (경계 변환 검증)."""
    res = client.get("/companies/cj-enm/report")
    assert res.status_code == 200
    body = res.json()
    assert "companyId" in body  # snake_case(company_id) 가 아니라 camelCase
    assert body["companyId"] == "cj-enm"
