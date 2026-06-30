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

logger = logging.getLogger(__name__)

# 저가 채팅 모델 고정. 바꾸면 비용이 오른다 — 변경은 신중히.
_CHAT_MODEL = 'gpt-4o-mini'

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


async def _complete(messages: list[dict[str, str]]) -> str:
    """채팅 완성 1회 호출 후 본문 텍스트를 다듬어 반환한다."""
    try:
        resp = await _get_client().chat.completions.create(
            model=_CHAT_MODEL,
            messages=cast(list[ChatCompletionMessageParam], messages),
        )
        return (resp.choices[0].message.content or '').strip()
    except Exception as error:  # noqa: BLE001 - 외부 API 장애를 친화 메시지로 변환
        logger.error('LLM 호출 실패: %s', error)
        raise RuntimeError('면접 LLM 응답 생성에 실패했습니다') from error


async def generate_main_questions(
    company_context: str, user_context: str, job_title: str, count: int
) -> list[str]:
    """회사·지원자·직무 컨텍스트로 메인 면접 질문 목록을 생성한다(빈 줄·중복 제거 후 count 절단).

    LLM 출력은 신뢰 불가 입력이라 빈 줄·앞뒤 공백·중복 질문을 후처리로 걸러낸다.
    중복은 dict.fromkeys 로 입력 순서를 유지하며 제거한다. 개수가 count 에 못 미쳐도
    여기서는 보충하지 않는다 — 기본 질문 보충은 호출부(service)가 담당한다.
    """
    text = await _complete(
        prompts.main_questions_messages(
            company_context, user_context, job_title, count
        )
    )
    lines = (line.strip() for line in text.splitlines() if line.strip())
    questions = list(dict.fromkeys(lines))
    return questions[:count]


async def generate_follow_up(question: str, answer: str) -> str:
    """직전 질문·답변을 바탕으로 꼬리질문 한 문장을 생성한다."""
    return await _complete(prompts.follow_up_messages(question, answer))


async def stream_evaluation(question: str, answer: str) -> AsyncIterator[str]:
    """답변 평가를 토큰 델타로 스트리밍한다(빈 델타는 건너뜀)."""
    try:
        stream = await _get_client().chat.completions.create(
            model=_CHAT_MODEL,
            messages=cast(
                list[ChatCompletionMessageParam],
                prompts.evaluation_messages(question, answer),
            ),
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
    text = await _complete(prompts.summary_messages(transcript))
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
