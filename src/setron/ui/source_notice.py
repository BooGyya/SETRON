"""출처 설명을 연결 성공 여부로 해석하지 않는 화면 안내."""


def source_description(description: str) -> str:
    """전달된 설명 또는 명시적인 출처 미확인 안내를 만든다.

    param
    - description: 자료가 제공한 설명. 빈 문자열은 출처 설명 미제공.

    return
    - 출처 안내 문구. DB 연결 여부는 추정하지 않는다.
    """
    return "자료 출처: " + (description if isinstance(description, str) and description.strip() else "설명 미제공")


def observation_description(data) -> str:
    """명시된 관측 연결 상태와 날짜를 출처 설명과 구분한다.

    param
    - data: 검증된 ItemManagerData.

    return
    - 관측 미연결 또는 전달된 기준일·건수와 출처 설명.
    """
    state = (f"관측 기준일 {data.base_date.isoformat()} · 전달된 설비 {len(data.records)}대"
             " · 전체 관측 범위 · 공통 조회 조건과 독립"
             if data.observations_connected else "설비 관측 자료 미연결 · 적용 현황 미확인")
    return state + "\n" + source_description(data.observation_label)
