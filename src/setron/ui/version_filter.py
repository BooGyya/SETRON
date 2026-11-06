"""조회용 버전 원문 선택지와 설비 단위 OR 필터. 버전 비교·위험 판정과 독립적이다."""

from setron.ui.query_state import VersionFilter


def observed_versions(record):
    """빈 SW/미입력은 None, 나머지는 접미사·대소문자·앞뒤 공백까지 원문 그대로 유지한다."""
    return tuple(pm.version_raw if pm.version_raw and pm.version_raw.strip() else None
                 for pm in record.pms) or (None,)


def version_options(source):
    """이미 읽은 관측 자료에서 선택지를 만든다. DB·외부 AI를 호출하지 않는다."""
    values = {value for record in source.records for value in observed_versions(record)}
    return (*sorted(value for value in values if value is not None), *((None,) if None in values else ()))


def matches_versions(record, selection: VersionFilter | None):
    if selection is None:
        return True
    return any((selection.include_missing if value is None else value in selection.versions)
               for value in observed_versions(record))
