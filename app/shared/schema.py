"""프론트로 나가는 응답의 공통 베이스 스키마.

백엔드 내부 코드는 전부 snake_case로 쓰고, 직렬화(JSON 출력)할 때만
camelCase로 변환한다. 프론트(Next.js)는 camelCase를 기대하므로, 응답용
Pydantic 스키마는 이 CamelModel을 상속해 표기 변환을 자동화한다.

라우터에서 내보낼 때 response_model_by_alias=True 를 함께 준다.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """snake_case 필드를 camelCase JSON으로 직렬화하는 응답용 베이스.

    - alias_generator=to_camel: company_name → companyName
    - populate_by_name=True: 코드에서는 snake_case 이름으로도 생성 가능
    - from_attributes=True: ORM 객체(SQLAlchemy)에서 바로 직렬화 가능
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
