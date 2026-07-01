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
        '- 실제 면접관이 말로 묻듯, 자연스러운 구어체로 작성합니다.\n'
        '- "~에 맞춰", "~와 연관된" 같은 기계적·문어체 표현을 쓰지 않습니다.\n'
        '- 한 질문에는 한 가지만 묻습니다(인재상·협업·문제해결처럼 여러 주제를 '
        '한 문장에 욱여넣지 않습니다).\n'
        '- 회사명이나 인재상을 언급할 땐 그대로 나열하지 말고, 질문 의도에 '
        '자연스럽게 녹여 묻습니다.\n'
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
    """직전 질문·답변을 바탕으로 꼬리질문 하나를 생성하는 메시지.

    답변이 부실하면(이름·소속만 말하거나, 너무 짧거나, 질문에 실질적으로 답하지
    않으면) 억지 꼬리질문을 만들지 않고 정확히 ``SKIP`` 한 단어만 출력하도록 한다 —
    "홍길동이라는 이름에 대해 더 말해보라" 같은 사소한 단어를 붙드는 헛질문을 막는다.
    호출부(service.generate_follow_up)가 이 SKIP 를 감지해 꼬리질문을 건너뛴다.
    """
    system = (
        '당신은 면접관입니다. 지원자의 직전 답변을 듣고 더 깊이 파고드는 '
        '꼬리질문을 하나만 생성하세요. 실제 면접관이 말로 되묻듯 자연스러운 '
        '구어체 한 문장으로, "~에 맞춰"·"~와 연관된" 같은 기계적 표현 없이 '
        '질문만 출력합니다.\n'
        '단, 답변이 이름·소속만 말하거나, 너무 짧거나, 질문에 실질적으로 답하지 '
        '않아 더 파고들 내용이 없으면 억지 질문을 만들지 말고 정확히 SKIP 한 단어만 '
        '출력하세요. 답변 속 사소한 단어(이름·숫자 등)를 붙들고 늘어지지 마세요.'
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


def report_messages(transcript: str, job_title: str) -> list[dict[str, str]]:
    """면접 전체 기록으로 결과 리포트의 LLM 영역을 한 JSON 으로 생성하는 메시지.

    요약(summary_messages)과 달리, 결과 페이지(계약 ④)에 필요한 풍부한 산출물을
    1회 호출로 모두 만든다 — 종합 점수·답변 피드백·강약점·보완점·턴별 평가·추천 질문.
    응답은 JSON 한 덩어리만(키는 내부 표기 snake_case). 점수는 모두 0~100 정수.

    script 평가는 면접 기록의 각 턴 번호(no, 1부터)에 1:1 대응시킨다 — no 는 기록에
    나온 질문 순서다. 추천 질문은 기록에 드러난 회사·직무 맥락에 근거해 생성한다.
    """
    job_line = f'지원 직무: {job_title}\n' if job_title else ''
    system = (
        '당신은 면접관입니다. 아래 면접 전체 기록을 보고 지원자 평가 리포트를 '
        'JSON 으로만 출력하세요. 모든 점수는 0~100 사이 정수입니다. '
        '키와 형식은 정확히 다음을 따릅니다:\n'
        '{\n'
        '  "overall": {"score": 정수, "grade": "A/B+/B 같은 등급 라벨", '
        '"headline": "한 줄 총평"},\n'
        '  "answer_feedback": {"score": 정수, "summary": "답변 영역 총평", '
        '"metrics": [{"label": "논리 구조", "score": 정수, "value": "우수/보통 같은 짧은 평", '
        '"comment": "한 줄 코멘트"}]},\n'
        '  "strengths": ["강점 문장", ...],\n'
        '  "weaknesses": ["약점 문장", ...],\n'
        '  "improvements": [{"area": "보완 영역", "problem": "무엇이 부족했나", '
        '"method": "구체적 보완 방법"}],\n'
        '  "script": [{"no": 정수, "category": "company 또는 job 또는 common", '
        '"score": 정수, "good": "잘한 점", "improve": "개선점"}],\n'
        '  "recommended_questions": {"company": ["회사 관련 예상 질문", ...], '
        '"job": ["직무 관련 예상 질문", ...]}\n'
        '}\n'
        '규칙:\n'
        '- answer_feedback.metrics 는 논리 구조·구체성·직무 적합성·질문 이해도 등 '
        '4개 내외로 채웁니다.\n'
        '- script 는 면접 기록의 각 턴(no=질문 순서, 1부터)마다 하나씩 만들고, '
        'category 는 회사 특정 질문이면 "company", 직무 역량 질문이면 "job", '
        '일반 질문이면 "common" 으로 분류합니다.\n'
        '- 답변이 "(무응답)"이거나 부실한 턴은 score 를 0 으로 두고 good 은 비우며, '
        'improve·weaknesses 에만 답변이 없었음을 반영합니다.\n'
        '- strengths·weaknesses 는 실제 답변에 근거가 있을 때만 각 최대 4개로 만들고, '
        '근거가 없으면(전부 무응답·부실) 억지로 지어내지 말고 빈 배열([])로 둡니다. '
        'improvements 는 최대 3개, recommended_questions 의 company·job 은 각 3~4개로 만듭니다.\n'
        '- JSON 외의 어떤 텍스트도 출력하지 마세요.'
    )
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': f'{job_line}면접 기록:\n{transcript}'},
    ]
