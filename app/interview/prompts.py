"""LLM 면접관 프롬프트 — 질문 생성·꼬리질문·답변 평가·최종 요약.

모든 프롬프트는 한국어다. 회사 컨텍스트는 context.py 가 주입한다. 톤·기준을 한
곳에서 조정하려고 프롬프트만 모아 둔다. 각 함수는 OpenAI chat ``messages`` 형식
(role/content dict 리스트)을 반환한다 — llm.py 가 그대로 호출에 넘긴다.

질문 생성·꼬리질문은 3인 패널(인사·기술·실무 면접관, personas.py)을 반영한다 —
슬롯마다 담당 면접관의 focus·tone 을 프롬프트에 실어 색깔 있는 질문을 만든다.
"""

from app.interview.personas import Persona

# 답변 채점 기준(루브릭) — 점수가 매 호출 출렁이지 않도록 명시적 구간·감점 규칙을 준다.
# report_messages(결과 리포트)·summary_messages(라이브 요약) 양쪽이 같은 기준을 공유해
# 두 경로 점수가 어긋나지 않게 한다. evaluation_messages(스트림 피드백)도 같은 잣대를 쓴다.
SCORING_RUBRIC = (
    '채점 기준(0~100, 반드시 이 구간을 따른다):\n'
    '- 90~100: 구체적 경험·사례·수치로 뒷받침되고 논리 전개가 완결되며 질문 의도에 정확히 답함.\n'
    '- 70~89: 핵심은 답했으나 근거·사례가 부분적이거나 구체성이 다소 부족함.\n'
    '- 50~69: 방향은 맞지만 피상적·추상적이고 근거가 거의 없음(일반론·교과서적 답변 위주).\n'
    '- 30~49: 질문과 부분적으로만 관련되거나 내용이 매우 빈약함.\n'
    '- 0~29: 무응답·동문서답이거나 질문과 무관함.\n'
    '감점·상한 규칙:\n'
    '- 답변이 "(무응답)"이거나 사실상 답하지 않았으면 무조건 0점.\n'
    '- 구체적 근거(실제 경험·사례·수치) 없이 일반론·추상론만 있으면 최대 60점을 넘기지 않는다.\n'
    '- 암기·미사여구는 가점 요인이 아니다. 실제 내용의 구체성·논리를 기준으로만 채점한다.\n'
    '- 확신이 서지 않으면 후하게 주지 말고 근거가 확인되는 만큼만 준다.'
)


def main_questions_messages(
    company_context: str,
    user_context: str,
    job_title: str,
    personas: list[Persona],
) -> list[dict[str, str]]:
    """3인 패널이 슬롯 순서대로 각자 담당 색깔의 메인 질문을 하나씩 생성하는 메시지.

    personas[i] 가 i번째(1-based) 질문의 담당 면접관. LLM 은 슬롯 순서대로 질문만
    한 줄씩 출력하고, service 가 그 순서를 personas 와 zip 해 담당을 붙인다
    (구분자 파싱 없이 순서로만 매칭 — 견고). 질문 개수 = ``len(personas)``.

    회사·지원자·직무는 선택 — 있는 것만 user 메시지에 담는다. 셋 다 비어 이 함수까지
    오는 일은 드물다(호출부가 빈 컨텍스트면 LLM 없이 기본질문으로 폴백한다).
    """
    count = len(personas)
    slot_lines = '\n'.join(
        f'{i}. [{p.role_label}] 담당 주제: {p.focus} / 말투: {p.tone}'
        for i, p in enumerate(personas, start=1)
    )
    system = (
        '당신은 한 기업의 면접 패널을 총괄합니다. 인사담당자·기술담당자·실무담당자 '
        '세 면접관이 번갈아 질문합니다. 아래 슬롯 순서대로, 각 슬롯에 지정된 담당자의 '
        '주제와 말투에 맞는 질문을 정확히 한 개씩 만드세요.\n\n'
        f'슬롯(순서대로):\n{slot_lines}\n\n'
        '규칙:\n'
        f'- 정확히 {count}개의 질문을 슬롯 번호 순서대로 생성합니다.\n'
        '- 1번 질문은 반드시 자기소개 요청으로 시작합니다.\n'
        '- 각 질문은 그 슬롯 담당자의 주제 영역 안에서, 그 담당자의 말투로 씁니다.\n'
        '- 회사·지원자·직무 정보가 주어지면 담당자 영역에 맞게 자연스럽게 녹여 '
        '묻습니다(정보를 그대로 나열하지 않습니다).\n'
        '- 지원자 정보(이력서·자기소개서·포트폴리오·경력기술서)가 있으면 그 경력·'
        '기술·프로젝트에 근거한 개인화 질문을 기술담당자·실무담당자 슬롯에 최소 '
        '1개 포함합니다.\n'
        '- 한 질문에는 한 가지만 묻습니다.\n'
        '- 실제 면접관이 말로 묻듯 자연스러운 구어체로, "~에 맞춰"·"~와 연관된" 같은 '
        '기계적·문어체 표현 없이 씁니다.\n'
        '- 각 질문은 한 줄에 하나씩, 번호나 담당자 이름 없이 질문 문장만 출력합니다.'
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


def follow_up_messages(
    question: str, answer: str, persona: Persona
) -> list[dict[str, str]]:
    """직전 질문·답변으로 담당자 말투의 꼬리질문 하나를 생성하는 메시지.

    persona 는 그 메인질문을 던진 면접관 — 꼬리질문도 같은 담당자의 주제·말투를
    유지한다. 기본값은 "생성" 이다 — 답변에 파고들 내용이 조금이라도 있으면 꼬리질문을
    만든다. 답변이 사실상 비었을 때(순수 인사말·이름만, 무응답)만 정확히 ``SKIP`` 한
    단어를 출력한다. 호출부(service.generate_follow_up)가 이 SKIP 를 감지해 건너뛴다.

    기본값 반전 이력: 예전엔 "애매하면 무조건 SKIP" 이라 꼬리질문이 거의 안 나왔다
    (자기소개·웬만한 답변까지 SKIP). 실사용에서 꼬리질문이 사라져 보여, "애매하면
    생성" 으로 뒤집었다. 이름·숫자 하나를 붙들고 되묻는 헛질문 차단만 유지한다.
    """
    system = (
        f'당신은 면접의 {persona.role_label} 입니다. 담당 주제는 [{persona.focus}] '
        f'이고, 말투는 다음과 같습니다: {persona.tone}\n'
        '지원자의 직전 답변에서, 그 답변에 실제로 담긴 내용(경험·기술·판단·근거·수치·'
        '사례·동기·지향)을 더 깊이 파고드는 꼬리질문을 하나만 생성하세요. 실제 면접관이 '
        '되묻듯 자연스러운 구어체 한 문장으로, 기계적 표현 없이 질문만 출력합니다.\n\n'
        '기본은 "생성" 입니다 — 파고들 내용이 조금이라도 있으면 만드세요. 자기소개 '
        '답변이어도 경력·경험·강점·지원동기 같은 내용이 담겨 있으면 그 지점을 파고드는 '
        '꼬리질문을 만듭니다(애매하면 SKIP 하지 말고 생성).\n\n'
        '아래처럼 답변이 사실상 비어 파고들 게 전혀 없을 때만 정확히 SKIP 한 단어를 '
        '출력하세요:\n'
        '- 답변이 순수 인사말·이름·소속뿐이고 그 외 내용이 전혀 없다(예: "안녕하세요 '
        '박초롱입니다").\n'
        '- 답변이 한두 마디로 너무 짧거나, 질문에 실질적으로 전혀 답하지 않았다.\n'
        '절대로 답변 속 이름·숫자·사소한 단어 하나를 붙들고 되묻지 마세요 — '
        '예를 들어 "박초롱입니다"라는 답에 이름의 뜻·유래·별명 같은 질문을 만드는 것은 '
        '금지입니다. 그런 경우 SKIP 하세요.'
    )
    user = f'직전 질문: {question}\n지원자 답변: {answer}'
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user},
    ]


def evaluation_messages(question: str, answer: str) -> list[dict[str, str]]:
    """답변을 평가하는 메시지(2~3문장 피드백).

    점수를 내는 경로는 아니지만(스트림 텍스트 피드백), report·summary 와 같은 채점
    잣대(SCORING_RUBRIC)를 근거로 톤을 맞춘다 — 무응답·일반론을 후하게 칭찬하지 않도록.
    """
    system = (
        '당신은 면접관입니다. 지원자의 답변을 평가하세요. 내용의 구체성·논리 '
        '구조·직무 적합성을 기준으로 2~3문장의 간결한 피드백을 한국어로 작성합니다. '
        '아래 잣대를 근거로, 근거 없는 일반론이나 무응답을 과하게 칭찬하지 마세요.\n\n'
        f'{SCORING_RUBRIC}'
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
        '- improvements: 개선점 문자열 배열(2~4개)\n\n'
        f'{SCORING_RUBRIC}\n\n'
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
        f'{SCORING_RUBRIC}\n'
        '규칙:\n'
        '- overall.score 와 script[].score 는 위 채점 기준을 그대로 적용합니다.\n'
        '- answer_feedback.metrics 는 논리 구조·구체성·직무 적합성·질문 이해도 등 '
        '4개 내외로 채우고, 각 metric.score 도 위 채점 기준을 따릅니다. 특히 '
        '구체성은 실제 경험·사례·수치가 있을 때만 70 이상을 줍니다.\n'
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
