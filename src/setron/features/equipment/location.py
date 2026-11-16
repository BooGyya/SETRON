"""가져오기와 직접 수정이 데이터 관리에서 정한 위치 분류를 덮어쓰지 않게 한다."""

from setron.features.hierarchy import _clean_line, area_labels
from setron.features.storage.inputs import require


def validate_location(tx, site, line, area):
    require(area is None or area in area_labels(tx), "등록되지 않은 생산 분류입니다. 데이터 관리에서 먼저 등록해 주세요.")
    try:
        value = tx.get("app_setting", {"setting_key": "registered_lines.v1"})["setting_value"]
    except LookupError:
        return
    entries = value.get("lines", ()) if isinstance(value, dict) else ()
    for entry in entries:
        if (isinstance(entry, dict) and str(entry.get("site") or "").strip().casefold() == str(site or "").strip().casefold()
                and _clean_line(entry.get("line")).casefold() == _clean_line(line).casefold()
                and entry.get("fab") is not None):
            require(area is None or area == entry["fab"],
                    "해당 Line의 등록된 생산 분류와 다릅니다. 위치 탭에서 Line의 소속을 변경해 주세요.")
            return
