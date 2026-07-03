"""면접 패널 페르소나 — 3인 면접관의 담당 영역·말투·목소리. 데이터로만 정의한다.

role_label 만 프론트로 노출(질문 배지). focus·tone 은 프롬프트 내부 전용,
voice 는 프론트 TTS(SpeechSynthesis)가 담당별 목소리로 매핑하는 데 쓴다.
'한 면접관 = 한 메인질문 + 그 꼬리질문' 원칙 — 꼬리질문 로직은 그대로다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    """한 면접관의 고정 정의. 불변(frozen) — 전역 상수로만 쓴다."""

    id: str          # 안정 식별자(프론트 배지·목소리 매핑)
    role_label: str  # 화면 노출 라벨(질문과 함께 내려감)
    voice: str       # TTS 목소리 힌트(프론트가 id→pitch/rate 또는 voice 로 매핑)
    focus: str       # 담당 주제(내부 프롬프트 전용 — 노출 안 함)
    tone: str        # 말투 지시문(내부 프롬프트 전용 — 노출 안 함)


CULTURE = Persona(
    id='culture_fit',
    role_label='인사담당자',
    voice='soft_high',
    focus='지원 동기, 협업 방식, 가치관, 갈등 해결, 성장 태도',
    tone='따뜻하고 공감적으로 묻는다. 지원자가 편하게 경험을 풀어놓도록 유도하고 '
    '사람·태도에 초점을 둔다. 몰아붙이지 않는다.',
)

TECH = Persona(
    id='tech_pressure',
    role_label='기술담당자',
    voice='low_firm',
    focus='기술적 깊이, 문제 해결 과정, 설계 트레이드오프 판단, 실패·디버깅 경험',
    tone='직설적이고 냉정하게 파고든다. 두루뭉술한 답에는 근거·수치·구체 사례를 '
    '요구하고 군더더기 없이 핵심만 묻는다.',
)

PRACTICAL = Persona(
    id='practical',
    role_label='실무담당자',
    voice='calm_mid',
    focus='직무 적합성, 실제 업무 시나리오 대응, 우선순위 판단, 협업 도구·프로세스 경험',
    tone="현실적이고 시나리오 중심으로 묻는다. '이런 상황이면 어떻게 하겠냐'식으로 "
    '실제 업무를 가정해 구체적 행동을 확인한다.',
)

PANEL: tuple[Persona, ...] = (CULTURE, TECH, PRACTICAL)
_BY_ID: dict[str, Persona] = {p.id: p for p in PANEL}


def persona_by_id(persona_id: str) -> Persona:
    """id 로 페르소나를 찾는다(없으면 진행자 CULTURE 로 폴백 — 데모 안전)."""
    return _BY_ID.get(persona_id, CULTURE)


def assign_interviewers(count: int) -> list[Persona]:
    """질문 슬롯 count 개에 면접관 배정.

    Q1=인사담당자(자기소개), 이후 기술→실무→인사 라운드로빈. 반환 리스트의
    i 번째가 i 번째 메인질문의 담당 면접관이다(그 질문의 꼬리질문도 같은 면접관).
    """
    if count <= 0:
        return []
    seq = [CULTURE]
    rotation = (TECH, PRACTICAL, CULTURE)
    while len(seq) < count:
        seq.append(rotation[(len(seq) - 1) % len(rotation)])
    return seq[:count]
