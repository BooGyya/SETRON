"""공통 입력값 및 원본 위치 검증."""

from .validation import StorageValidationError


def require(condition, message):
    """검증 조건이 충족되는지 검사한다.

    param
    - condition: 검사 결과. False로 평가되면 검증 실패로 처리.
    - message: 검증 실패 시 오류에 담을 설명.

    raises
    - StorageValidationError: 조건이 충족되지 않은 경우.
    """
    if not condition:
        raise StorageValidationError(message)


def identifier(value, field):
    """DB 식별자로 사용할 수 있는 양의 정수인지 검사한다.

    True와 숫자 문자열은 정수 ID로 허용하지 않는다.

    param
    - value: 검사할 ID 값.
    - field: 오류 메시지에 표시할 항목 이름.

    raises
    - StorageValidationError: 정수가 아니거나 1~4294967295 범위를 벗어난 경우.
    """
    require(type(value) is int and 1 <= value <= 4294967295, f"{field}: 양의 정수 ID가 필요합니다.")


def required_text(value, field, limit):
    """필수 문자열의 입력 여부와 길이를 검사한다.

    앞뒤 공백은 빈 값 판정에만 사용하며 원문은 변경하지 않는다.

    param
    - value: 검사할 문자열.
    - field: 오류 메시지에 표시할 항목 이름.
    - limit: 허용하는 최대 글자 수.

    raises
    - StorageValidationError: 문자열이 아니거나 공백뿐이거나 길이 제한을 넘은 경우.
    """
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= limit,
            f"{field}: {limit}자 이내의 비어 있지 않은 원문이 필요합니다.")


def merge_sources(*references, allow_batch=False):
    """필드별 원본 위치를 병합하고 중복된 셀 주소를 제거한다.

    같은 시트의 셀은 하나로 모으고 다른 시트의 위치는 각각 보존한다.
    전달받은 출처 객체는 변경하지 않는다.

    param
    - references: 병합할 필드별 출처 객체들. 출처가 없는 None 항목은 제외.
    - allow_batch: 설비 출처에서 위치별 batch_id를 허용할지 여부. 기본값은 False.
      생략한 batch_id는 해당 행의 입력 작업을 뜻하며, 명시한 작업과 다른 작업은 합치지 않음.

    return
    - 필드별 출처 객체. 여러 시트의 출처는 {sheet, cells} 목록으로 보관하며, 출처가 없으면 None.

    raises
    - StorageValidationError: 필드명·시트명·셀 목록의 형식이 잘못된 경우.
    """
    result = {}
    for reference in references:
        if reference is None:
            continue
        require(isinstance(reference, dict), "source_reference: 필드별 출처 객체가 필요합니다.")
        for field, value in reference.items():
            required_text(field, "source_reference 필드명", 255)
            locations = value if isinstance(value, list) else [value]
            require(bool(locations), f"{field}: 원본 위치가 필요합니다.")
            saved = result.setdefault(field, [])
            for location in locations:
                allowed = ({"sheet", "cells"}, {"sheet", "cells", "batch_id"}) if allow_batch else ({"sheet", "cells"},)
                require(isinstance(location, dict) and set(location) in allowed,
                        f"{field}: 출처는 sheet와 cells로 지정해야 합니다.")
                if "batch_id" in location:
                    identifier(location["batch_id"], f"{field} batch_id")
                required_text(location["sheet"], "sheet", 255)
                cells = location["cells"]
                require(isinstance(cells, list) and bool(cells), f"{field}: 원본 셀 목록이 필요합니다.")
                for cell in cells:
                    required_text(cell, "cell", 50)
                target = next((item for item in saved if item["sheet"] == location["sheet"]
                               and item.get("batch_id") == location.get("batch_id")), None)
                if target is None:
                    target = {"sheet": location["sheet"], "cells": []}
                    if "batch_id" in location:
                        target["batch_id"] = location["batch_id"]
                    saved.append(target)
                target["cells"].extend(cell for cell in cells if cell not in target["cells"])
    return {field: values[0] if len(values) == 1 else values for field, values in result.items()} or None
