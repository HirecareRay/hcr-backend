"""OpenAI Chat 경계 — 면접 질문 생성·꼬리질문·답변 평가 스트림·최종 요약.

stt.py 와 대칭인 유일한 LLM 경계다. 라우터·service 는 OpenAI 세부(클라이언트·
모델·스트리밍)를 모르고, 테스트는 여기(_get_client)를 mock 해 실 API 를 호출하지
않는다(강사님 키 보호).

⚠️ 비용 주의: 모델은 저가 gpt-4o-mini 로 고정한다 — 변경은 비용에 직결. 외부 API
장애는 내부 스택을 노출하지 않고 RuntimeError 로 변환한다.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.core.config import settings
from app.interview import prompts
from app.interview.personas import Persona

logger = logging.getLogger(__name__)

# 저가 채팅 모델 고정. 바꾸면 비용이 오른다 — 변경은 신중히.
_CHAT_MODEL = 'gpt-4o-mini'

# 결과 리포트 1회 생성의 출력 토큰 상한(비용 폭주 방지). 정상 결과 JSON(종합·답변
# 지표·강약점·보완점·턴별 평가·추천 질문)은 이 안에 충분히 들어간다.
_REPORT_MAX_TOKENS = 2048

# 채점 경로(리포트·요약·평가)의 temperature. 낮게 고정해 같은 답변이 매 호출 비슷한
# 점수를 받도록 분산을 줄인다(루브릭 기준의 일관성 보강). 질문·꼬리질문 생성은 다양성이
# 필요하므로 이 값을 쓰지 않고 모델 기본값(1.0)을 그대로 둔다.
_SCORING_TEMPERATURE = 0.3

# AsyncOpenAI 클라이언트는 지연 생성한다(import 시 키를 요구하지 않도록 — 테스트는
# _get_client 를 mock 하므로 실 키 없이도 import·실행된다).
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """AsyncOpenAI 클라이언트를 지연 생성·재사용한다(키 없으면 명확히 실패)."""
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError('OPENAI_API_KEY 가 설정되지 않았습니다 (.env 확인)')
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def _complete(
    messages: list[dict[str, str]], temperature: float | None = None
) -> str:
    """채팅 완성 1회 호출 후 본문 텍스트를 다듬어 반환한다.

    temperature 를 주면 그 값으로 호출한다(채점 경로의 결정성 보강). None 이면 모델
    기본값을 써 질문 생성 등 다양성이 필요한 경로의 창의성을 유지한다.
    """
    kwargs: dict = {}
    if temperature is not None:
        kwargs['temperature'] = temperature
    try:
        resp = await _get_client().chat.completions.create(
            model=_CHAT_MODEL,
            messages=cast(list[ChatCompletionMessageParam], messages),
            **kwargs,
        )
        return (resp.choices[0].message.content or '').strip()
    except Exception as error:  # noqa: BLE001 - 외부 API 장애를 친화 메시지로 변환
        logger.error('LLM 호출 실패: %s', error)
        raise RuntimeError('면접 LLM 응답 생성에 실패했습니다') from error


async def generate_main_questions(
    company_context: str,
    user_context: str,
    job_title: str,
    personas: list[Persona],
) -> list[str]:
    """회사·지원자·직무 컨텍스트로 3인 패널 메인 질문 목록을 생성한다(빈 줄·중복 제거 후 절단).

    personas[i] 가 i번째 질문의 담당 면접관 — 질문 개수는 ``len(personas)`` 다.
    LLM 출력은 신뢰 불가 입력이라 빈 줄·앞뒤 공백·중복 질문을 후처리로 걸러낸다.
    중복은 dict.fromkeys 로 입력 순서를 유지하며 제거한다. 개수가 부족해도 여기서는
    보충하지 않는다 — 기본 질문 보충은 호출부(service)가 담당한다.
    """
    count = len(personas)
    text = await _complete(
        prompts.main_questions_messages(
            company_context, user_context, job_title, personas
        )
    )
    lines = (line.strip() for line in text.splitlines() if line.strip())
    questions = list(dict.fromkeys(lines))
    return questions[:count]


async def generate_follow_up(question: str, answer: str, persona: Persona) -> str:
    """직전 질문·답변으로 담당 면접관(persona) 말투의 꼬리질문 한 문장을 생성한다."""
    return await _complete(prompts.follow_up_messages(question, answer, persona))


async def stream_evaluation(question: str, answer: str) -> AsyncIterator[str]:
    """답변 평가를 토큰 델타로 스트리밍한다(빈 델타는 건너뜀)."""
    try:
        stream = await _get_client().chat.completions.create(
            model=_CHAT_MODEL,
            messages=cast(
                list[ChatCompletionMessageParam],
                prompts.evaluation_messages(question, answer),
            ),
            temperature=_SCORING_TEMPERATURE,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as error:  # noqa: BLE001 - 외부 API 장애를 친화 메시지로 변환
        logger.error('평가 스트림 실패: %s', error)
        raise RuntimeError('답변 평가에 실패했습니다') from error


async def generate_summary(transcript: str) -> dict:
    """면접 기록으로 최종 요약(JSON)을 생성·파싱한다(실패 시 빈 dict)."""
    text = await _complete(
        prompts.summary_messages(transcript), temperature=_SCORING_TEMPERATURE
    )
    return _parse_summary(text)


async def generate_report(transcript: str, job_title: str) -> dict:
    """면접 기록으로 결과 리포트의 LLM 영역(JSON)을 1회 생성·파싱한다(실패 시 빈 dict).

    결과 페이지(계약 ④)에 필요한 종합 점수·답변 피드백·강약점·보완점·턴별 평가·
    추천 질문을 한 호출로 만든다. response_format=json_object 로 JSON 출력을 강제해
    파싱 안정성을 높인다(코드펜스·잡설 방지). 호출부(result_builder)가 빈 dict 를
    안전 기본값으로 우회하므로, 여기서는 파싱 실패도 빈 dict 로 흡수한다.
    """
    try:
        resp = await _get_client().chat.completions.create(
            model=_CHAT_MODEL,
            messages=cast(
                list[ChatCompletionMessageParam],
                prompts.report_messages(transcript, job_title),
            ),
            temperature=_SCORING_TEMPERATURE,
            response_format={'type': 'json_object'},
            # 출력 토큰 상한 — 비정상적으로 긴 결과로 과금이 폭주하지 않게 묶는다.
            # 초과로 JSON 이 잘리면 파싱 실패 → 빈 dict → 안전 기본값으로 우회한다.
            max_tokens=_REPORT_MAX_TOKENS,
        )
        text = resp.choices[0].message.content or ''
    except Exception as error:  # noqa: BLE001 - 외부 API 장애를 친화 메시지로 변환
        logger.error('리포트 생성 실패: %s', error)
        raise RuntimeError('면접 결과 리포트 생성에 실패했습니다') from error
    return _parse_summary(text)


def _parse_summary(text: str) -> dict:
    """LLM 의 JSON 응답을 안전하게 파싱한다(코드펜스 허용, 실패 시 빈 dict).

    빈 dict 를 돌려주면 호출부(service)가 안전 기본 요약으로 우회한다.
    """
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.removeprefix('```json').removeprefix('```')
        cleaned = cleaned.removesuffix('```').strip()
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.warning('요약 JSON 파싱 실패: %s', text[:200])
        return {}
    return data if isinstance(data, dict) else {}
