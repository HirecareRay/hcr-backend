"""더미 실시간 자막 생성 — 실제 STT 없이 부분 자막을 흐르게 한다(Phase 1 워킹 스켈레톤).

오디오 청크가 들어올 때마다 미리 정의된 모범답변 토큰을 하나씩 흘려보내, 프론트가
'말하는 동안 자막이 차오르는' 실시간 UX 를 OpenAI 호출·키 없이 시연한다. whisper-1
은 스트리밍이 불가(answer_end 에 통전사)라 실 STT 로는 이 흐르는 효과를 낼 수 없는데,
더미 모드가 그 자리를 메운다. 실 전사는 settings.interview_dummy_transcript=False 로
끄면 stt 경로로 돌아간다.

비용 0: 외부 호출이 전혀 없는 순수 함수다. 강사님 대여 키를 건드리지 않는다.
"""

# 흐를 더미 답변 토큰(끝 공백 포함). 청크 순번으로 순환 인덱싱해 무한히 이어 붙는다.
_DUMMY_TOKENS: tuple[str, ...] = (
    '제 ', '가장 ', '큰 ', '강점은 ', '팀워크와 ', '문제 ', '해결 ', '능력입니다. ',
    '이전 ', '프로젝트에서 ', '일정이 ', '촉박했지만 ', '동료들과 ', '역할을 ',
    '나눠 ', '맡아 ', '기한 ', '내에 ', '안정적으로 ', '마무리했습니다. ',
)


def token_at(index: int) -> str:
    """청크 순번에 대응하는 더미 자막 토큰을 돌려준다(토큰 풀을 순환).

    index 가 풀 길이를 넘으면 처음부터 다시 순환하므로, 답변이 길어도 안전하다.
    """
    return _DUMMY_TOKENS[index % len(_DUMMY_TOKENS)]


def answer_text(count: int) -> str:
    """지금까지 흘린 count 개 토큰을 이어 붙인 누적 답변 텍스트를 돌려준다.

    answer_end 시점에 '평가·요약의 입력이 될 답변 본문'으로 쓴다(count 0 이면 빈 답변).
    """
    return ''.join(token_at(i) for i in range(count)).strip()
