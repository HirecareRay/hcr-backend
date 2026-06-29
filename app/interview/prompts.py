"""LLM 면접관 프롬프트 — 질문 생성·꼬리질문·답변 평가·최종 요약.

모든 프롬프트는 한국어다. 회사 컨텍스트는 context.py 가 주입한다. 톤·기준을 한
곳에서 조정하려고 프롬프트만 모아 둔다. 각 함수는 OpenAI chat ``messages`` 형식
(role/content dict 리스트)을 반환한다 — llm.py 가 그대로 호출에 넘긴다.
"""


def main_questions_messages(
    company_context: str, user_context: str, job_title: str, count: int
) -> list[dict[str, str]]:
    """회사·지원자·직무 컨텍스트로 메인 면접 질문 ``count`` 개를 생성하는 메시지.

    셋 다 선택적이다 — 있는 것만 user 메시지에 담는다. 회사 컨텍스트가 비면 일반
    면접 질문, 지원자 정보가 있으면 그 경력·기술·프로젝트에 근거한 개인화 질문,
    직무가 있으면 그 직무 적합성 질문을 섞도록 규칙을 준다. (셋 다 비어 이 함수까지
    오는 일은 드물다 — 호출부가 빈 컨텍스트면 LLM 없이 기본질문으로 폴백한다.)
    """
    system = (
        '당신은 한국 기업의 면접관입니다. 아래에 주어진 회사·지원자·직무 정보를 '
        '참고해 지원자에게 던질 면접 질문을 만드세요.\n'
        '규칙:\n'
        f'- 정확히 {count}개의 질문을 생성합니다.\n'
        '- 첫 번째 질문은 반드시 자기소개 요청으로 시작합니다.\n'
        '- 회사 정보가 주어지면 그 인재상·기술·직무와 연관된 질문을 포함합니다.\n'
        '- 지원자 정보(이력서·자기소개서·포트폴리오·경력기술서)가 주어지면, 그 '
        '경력·기술·프로젝트에 근거한 개인화 질문을 최소 1개 이상 포함합니다.\n'
        '- 지원 직무가 주어지면 그 직무 적합성을 묻는 질문을 포함합니다.\n'
        '- 회사·지원자·직무 정보가 비어 있으면 직무 무관한 일반 신입 면접 '
        '기본 질문(지원동기·강점·성장경험 등)으로 구성합니다.\n'
        '- 각 질문은 한 줄에 하나씩, 번호나 기호 없이 질문 문장만 출력합니다.'
    )
    blocks: list[str] = []
    if company_context:
        blocks.append(f'회사 컨텍스트:\n{company_context}')
    if user_context:
        blocks.append(f'지원자 정보:\n{user_context}')
    if job_title:
        blocks.append(f'지원 직무: {job_title}')
    user = '\n\n'.join(blocks) if blocks else '(추가 정보 없음 — 일반 면접 질문 생성)'
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user},
    ]


def follow_up_messages(question: str, answer: str) -> list[dict[str, str]]:
    """직전 질문·답변을 바탕으로 꼬리질문 하나를 생성하는 메시지."""
    system = (
        '당신은 면접관입니다. 지원자의 직전 답변을 듣고 더 깊이 파고드는 '
        '꼬리질문을 하나만 생성하세요. 한국어 한 문장으로, 질문만 출력합니다.'
    )
    user = f'직전 질문: {question}\n지원자 답변: {answer}'
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user},
    ]


def evaluation_messages(question: str, answer: str) -> list[dict[str, str]]:
    """답변을 평가하는 메시지(2~3문장 피드백)."""
    system = (
        '당신은 면접관입니다. 지원자의 답변을 평가하세요. 내용의 구체성·논리 '
        '구조·직무 적합성을 기준으로 2~3문장의 간결한 피드백을 한국어로 작성합니다.'
    )
    user = f'질문: {question}\n답변: {answer}'
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user},
    ]


def summary_messages(transcript: str) -> list[dict[str, str]]:
    """면접 전체 기록으로 최종 요약(JSON)을 생성하는 메시지.

    응답은 JSON 한 덩어리만 — 키는 내부 표기(snake_case)로 받아 service 가
    SummaryEvent 로 감싼다(직렬화 시 camelCase 변환은 CamelModel 이 처리).
    """
    system = (
        '당신은 면접관입니다. 아래 면접 전체 기록을 보고 최종 평가를 JSON 으로만 '
        '출력하세요. 키는 다음 셋입니다:\n'
        '- overall_score: 0~100 사이 정수(전반적 답변 품질)\n'
        '- language_feedback: 답변 내용에 대한 종합 피드백 문자열\n'
        '- improvements: 개선점 문자열 배열(2~4개)\n'
        'JSON 외의 어떤 텍스트도 출력하지 마세요.'
    )
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': f'면접 기록:\n{transcript}'},
    ]
