"""B-2 화면 초안 계약. DB 필드·편집 정책·저장 구현을 정의하지 않는다."""

from dataclasses import dataclass
from typing import Callable, Literal, Protocol


@dataclass(frozen=True)
class EditField:
    """외부에서 명시적으로 제공한 텍스트 입력 항목.

    ivar
    - key, title: 요청 키와 표시 이름. 실제 릴리즈 필드 목록은 별도 확정한다.
    - original: 보존된 원본 표시값. None은 원본 미연결이며 빈 문자열과 다르다.
    - current: 편집 시작 시 관리값. 공통 기반에서는 문자열만 취급한다.
    - validate: 문자열을 검사해 오류 문구 또는 None을 반환하는 함수.
    """

    key: str
    title: str
    original: str | None
    current: str
    validate: Callable[[str], str | None]


@dataclass(frozen=True)
class EditRequest:
    """검토 완료한 변경의 불변 스냅샷. SQL·테이블 이름은 포함하지 않는다.

    ivar
    - release_id: 수정 대상 릴리즈 ID.
    - changes: 변경한 항목의 키·이전 관리값·새 값 튜플.
    """

    release_id: int
    changes: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class SaveOutcome:
    """저장 어댑터가 반환하는 결과. 오류 원문 대신 화면의 고정 안내를 사용한다.

    ivar
    - status: 성공, 확정 실패, 결과 미확인 중 하나. 미확인은 재저장을 금지한다.
    """

    status: Literal["success", "failed", "unknown"]


class EditGateway(Protocol):
    """비동기 저장 요청 경계. 이번 작업에는 실제 구현이나 DB 연결이 없다."""

    def submit(self, request: EditRequest, complete: Callable[[SaveOutcome], None]) -> None:
        """요청을 즉시 반환하고 완료 결과를 한 번 전달한다.

        param
        - request: 검토한 변경 스냅샷.
        - complete: 어느 스레드에서든 호출 가능한 완료 함수.

        raises
        - Exception: 제출 중 오류. 저장 여부를 보장할 수 없어 UI는 미확인으로 처리한다.
        """
        ...


class EditDraft:
    """원본·시작 관리값과 분리한 화면 초안.

    ivar
    - fields: 명시된 항목 튜플. 기본 화면은 빈 튜플을 제공한다.
    - values: 사용자가 수정하는 문자열 값.
    """

    def __init__(self, release_id: int, fields: tuple[EditField, ...] = ()):
        """필드 식별과 형식을 검증하고 독립 초안을 만든다.

        param
        - release_id: 양의 정수 릴리즈 ID.
        - fields: 외부에서 확정해 제공하는 텍스트 항목.

        raises
        - ValueError: ID·키 중복·항목 형식이 올바르지 않은 경우.
        """
        if type(release_id) is not int or release_id <= 0:
            raise ValueError("릴리즈 ID가 올바르지 않습니다.")
        self.release_id, self.fields = release_id, tuple(fields)
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)) or any(
            not isinstance(f.key, str) or not f.key.strip() or not f.title
            or not isinstance(f.current, str)
            or (f.original is not None and not isinstance(f.original, str))
            or not callable(f.validate) for f in self.fields
        ):
            raise ValueError("편집 항목 계약이 올바르지 않습니다.")
        self.values = {f.key: f.current for f in self.fields}

    def request(self) -> EditRequest:
        """원본을 변경하지 않고 실제 차이만 반환한다.

        return
        - EditRequest: 명시된 항목의 변경 스냅샷.
        """
        return EditRequest(self.release_id, tuple(
            (f.key, f.current, self.values[f.key]) for f in self.fields
            if self.values[f.key] != f.current))

    def errors(self) -> dict[str, str]:
        """변경 항목만 검사하며 검증기 실패는 저장 불가로 처리한다.

        return
        - dict: 항목 키별 오류 문구.
        """
        errors = {}
        for key, _, value in self.request().changes:
            field = next(f for f in self.fields if f.key == key)
            try:
                error = field.validate(value)
                if error is not None and (not isinstance(error, str) or not error):
                    error = "입력 검증 결과를 확인할 수 없습니다."
            except Exception:
                error = "입력 검증을 완료하지 못했습니다."
            if error:
                errors[key] = error
        return errors
