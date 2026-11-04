# 폴더 아키텍처

setron은 Python 기반 Windows 데스크톱 애플리케이션입니다. 단일 저장소와 실행 패키지 `src/setron/` 안에서 역할별 모듈을 분리합니다.

화면, 업무 로직, 외부 기술 연동을 나누는 최소 구조를 사용합니다. 현재는 열린 Excel 읽기, 버전 비교·집계·Item·위험 판정, 예제 응답으로 동작하는 AI 호출 흐름, DB 연결·공통 저장 서비스와 해당 테스트가 구현되어 있습니다. 화면과 각 업무 흐름을 DB에 연결하는 코드는 후속 작업입니다.

개발·테스트용 MySQL 5.6.19는 Docker Compose로 분리해 실행합니다. Python 앱과 Excel은 Windows에서 실행하며, 실제 사용 PC의 앱은 Windows에 설치된 MySQL에 접속합니다.

DB 연동은 두 환경에서 같은 코드를 사용하고, `SETRON_DB_*` 환경변수로 접속 대상을 바꿉니다. SQL과 스키마는 MySQL 5.6.19를 기준으로 작성하며, 앱이 Docker 실행 명령이나 컨테이너 내부 경로에 의존하지 않도록 합니다. Windows 서버에는 같은 업무 스키마와 필요한 계정 권한을 준비하고, 실사용 전 해당 서버에서 저장·전체 취소·삭제 전 위험 근거 보존을 검증합니다. 환경별 접속 설정과 개발 기준은 [README](../README.md#db-연동-개발-기준)를 참고합니다.

## 현재 구조

```text
S15P31B106/
├── src/
│   └── setron/
│       ├── __init__.py
│       ├── __main__.py
│       ├── ui/
│       │   ├── __init__.py
│       │   ├── app.py
│       │   ├── main_window.py
│       │   ├── components.py
│       │   ├── query_state.py
│       │   ├── query_panel.py
│       │   ├── dashboard.py
│       │   ├── dashboard_charts.py
│       │   ├── dashboard_data.py
│       │   ├── dashboard_demo.py
│       │   ├── heatmap.py
│       │   ├── heatmap_data.py
│       │   ├── equipment.py
│       │   ├── equipment_data.py
│       │   ├── target_data.py
│       │   ├── target_dialog.py
│       │   ├── versions.py
│       │   ├── version_data.py
│       │   ├── version_demo.py
│       │   ├── import_page.py
│       │   ├── import_demo.py
│       │   ├── ai_page.py
│       │   ├── ai_data.py
│       │   ├── theme.py
│       │   ├── assets/
│       │   └── check_environment.py
│       ├── features/
│       │   ├── __init__.py
│       │   ├── aggregation/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── aging/
│       │   │   ├── __init__.py
│       │   │   ├── repository.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── ai/
│       │   │   ├── __init__.py
│       │   │   ├── examples.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── analysis/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── db_export/
│       │   │   ├── __init__.py
│       │   │   └── service.py
│       │   ├── export/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── forecast/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── import_save/
│       │   │   ├── __init__.py
│       │   │   └── service.py
│       │   ├── item/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── priority/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── query/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── report/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── risk/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── excel_import/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── storage/
│       │   │   ├── __init__.py
│       │   │   ├── repository.py
│       │   │   ├── service.py
│       │   │   └── validation.py
│       │   └── version/
│       │       ├── __init__.py
│       │       ├── schemas.py
│       │       └── service.py
│       └── infrastructure/
│           ├── __init__.py
│           ├── ai_client.py
│           ├── database.py
│           ├── env_file.py
│           └── excel_reader.py
├── database/
│   ├── reset_demo_db.py
│   ├── schema_mysql5619.sql
│   └── seed_demo_catalog.sql
├── packaging/
│   ├── build_windows.ps1
│   ├── run_setron.py
│   └── setron.spec
├── tests/
│   ├── test_aggregation_service.py
│   ├── test_aging_mysql.py
│   ├── test_aging_service.py
│   ├── test_analysis_service.py
│   ├── test_ai_service.py
│   ├── test_database.py
│   ├── test_database_environment.py
│   ├── test_database_mysql.py
│   ├── test_excel_import_service.py
│   ├── test_excel_reader.py
│   ├── test_export_service.py
│   ├── test_forecast_service.py
│   ├── test_item_service.py
│   ├── test_priority_service.py
│   ├── test_query_service.py
│   ├── test_report_service.py
│   ├── test_risk_service.py
│   ├── test_schema_mysql.py
│   ├── test_storage_service.py
│   ├── test_storage_validation.py
│   ├── test_ui_common.py
│   ├── test_ui_query.py
│   ├── test_ui_dashboard.py
│   ├── test_ui_heatmap.py
│   ├── test_ui_equipment.py
│   ├── test_ui_targets.py
│   ├── test_ui_import.py
│   ├── test_ui_ai.py
│   └── test_version_service.py
├── docs/
│   ├── architecture.md
│   ├── git-conventions.md
│   ├── testing.md
│   └── mermaid-diagram.png
├── .gitignore
├── .env.example
├── .env.windows.example
├── .python-version
├── compose.yaml
├── docker/mysql5619/
├── pyproject.toml
├── uv.lock
└── README.md
```

각 Python 패키지에는 `__init__.py`를 둡니다. `__main__.py`는 `ui/app.py`의 `create_window()`를 통해 공통 UI에 예시 대시보드·히트맵과 설비 목록/상세 하위 화면을 등록합니다. 조회 적용 시 기존 순수 집계 서비스를 사용하지만 DB·Excel에는 연결하지 않습니다. 설치와 실행 방법은 [README](../README.md)를 참고합니다.

`ui/check_environment.py`는 화면 개발 전 Python·PySide6·Qt 버전과 창 표시·종료를 확인하는 진단용 실행 모듈입니다. 업무 서비스나 DB를 호출하지 않습니다.

### 공통 UI (작업 단위 2)

작업 단위 2는 공통 UI 기반입니다. 작업 단위 3의 공통 조회 조건은 아래 계약으로 연결하며, 대시보드 지표·그래프, 히트맵, 설비 목록·상세, 목표 N 선택 및 저장은 별도 작업입니다.

| 모듈 | 책임 |
| --- | --- |
| `ui/app.py` | QApplication·테마·공통 창 시작, 실행 확인용 자동 종료 옵션 |
| `ui/main_window.py` | 사이드바와 활성 메뉴, 담당 페이지 등록·이동, 공통 도움말 |
| `ui/components.py` | `Card`, `StatusBadge`, `EmptyState`, `PageHeader`, `label`, `button` |
| `ui/theme.py`, `ui/assets/` | VER3 Light 공통 스타일·원본 로고·아이콘·폰트와 폰트 라이선스 |
| `tests/test_ui_common.py` | 외부 연결 없는 Qt 위젯·키보드·라우팅·레이아웃 테스트 |

`MainWindow` 자체의 대시보드는 안내용 자리입니다. 앱 시작점이 작업 단위 4의 `DashboardPage`를 등록해 교체합니다. 등록한 위젯의 소유권은 공통 창으로 넘어갑니다.

```python
window = MainWindow()
window.register_page("dashboard", dashboard_page)
window.register_page("import", import_page)
window.navigate("import")
```

메뉴 key는 `dashboard`, `heatmap`, `items`, `ai`, `import`, `replay`, `versions`, `history`, `reports`, `settings`입니다. 등록 전에는 시작 안내용 `dashboard` 외의 메뉴를 비활성화합니다. 같은 key 또는 같은 QWidget의 중복 등록과 알 수 없는 key는 거부합니다. 알 수 없는 메뉴로 이동하면 `ValueError`, 알려진 미등록 메뉴로 이동하면 `False`를 반환하고 현재 화면은 유지합니다.

공통 창은 페이지 인스턴스를 보존하며 현재 페이지만 레이아웃에 배치합니다. 화면 이동 후에도 담당 위젯의 입력 상태는 유지되고, 숨긴 긴 페이지의 최소 높이가 다른 화면을 늘리지 않습니다. 페이지 이동 때 공통 콘텐츠 스크롤은 맨 위로 이동합니다. `page_changed(str)` 시그널로 이동 결과를 받을 수 있습니다.

헤더의 조회·내보내기·설정 액션은 연결 전까지 비활성화합니다. 후속 담당자가 `window.header.actions[key]`의 클릭 처리를 연결하고 활성화해야 합니다. 액션 key는 `equipment_list`, `export`, `settings`, `help`이며, 공통 창은 `help`만 처리합니다. 등록 자체로 DB 조회·저장·내보내기가 발생하지 않습니다.

공통 창·표시 컴포넌트·조회 조건 모듈 자체는 `features/`나 `infrastructure/`에 의존하지 않습니다. 업무 페이지의 데이터 어댑터가 서비스와 연결하며, 오래 걸리는 실제 조회·저장은 아래 스레드 규칙에 따라 워커에서 실행해야 합니다.

### 공통 조회 조건 (작업 단위 3)

`ui/query_state.py`는 Qt·DB에 의존하지 않는 조회 조건 계약이고, `ui/query_panel.py`는 VER3의 필드·빠른 선택 버튼·적용/초기화·상태 표시를 담당합니다. `MainWindow`가 상태와 패널을 하나씩 소유하므로 페이지를 이동해도 선택과 적용 조건을 유지합니다. 파일·DB에 저장하지 않으며 앱 재시작 시 초기 상태로 돌아갑니다.

| 값 | 의미 |
| --- | --- |
| `QuerySelection.site_key` | Site 선택지 키 또는 `None`(전체) |
| `fab_key`, `line_key` | 서로 독립된 항목. 선택지 키, `None`(전체), `UNASSIGNED` Enum(미지정) |
| `group_key` | UI 설비군 키 또는 `None`(전체). DB의 `version_family.family_id`가 아님 |
| `QueryState.draft` | 현재 선택 중인 불변 조건. 선택 변경은 자동 조회하지 않음 |
| `QueryState.applied` | 마지막 `AppliedQuery` 또는 적용 전 `None` |
| `AppliedQuery` | 불변 조건·적용 요청 번호·선택지 세대·적용 시점의 표시 문구 |

Site 변경은 Fab·Line 초안을, Fab 변경은 Line 초안을 전체로 되돌립니다. 같은 값을 다시 고르면 하위 선택을 유지합니다. 설비군은 위치 조건과 독립적입니다. 등록된 부모 관계와 맞지 않는 조건은 변경 전에 거부합니다. Fab을 모르는 Line도 `LineOption(..., fab_key=None)`으로 명시적으로 등록할 수 있습니다. 이 경우 Fab ‘미지정’과 해당 Line을 함께 고를 수 있으며 Fab과 Line을 합치지 않습니다.

최초에는 적용 조건이 없습니다. `조회 적용`을 누를 때마다 요청 번호가 증가하고 `query_applied(AppliedQuery)`가 발생합니다. 같은 조건을 재적용해도 새 요청입니다. 초안 변경·초기화만으로 이 신호가 발생하지는 않습니다. 공통 패널은 조건 확정만 처리하며 등록된 업무 페이지가 결과를 계산합니다. 조건 적용을 DB 조회 완료로 표시하지 않습니다.

```python
from setron.ui.main_window import MainWindow
from setron.ui.query_state import QuerySelection

window = MainWindow(query_catalog=validated_catalog)
window.register_page("dashboard", dashboard_page)
window.register_page("heatmap", heatmap_page)
window.register_page("import", import_page)  # 기본적으로 공통 조회 패널을 숨김
window.register_page("items", items_page, uses_query=True)  # 필요할 때 명시적으로 공유
window.query_applied.connect(on_query_requested)  # 워커/서비스 연결은 담당 업무 화면에서 구현
window.query_panel.set_draft(QuerySelection(site_key=registered_site_key))
# 위 코드는 초안만 변경. 명시적으로 적용하려면 window.query_panel.apply_draft()
```

기본 패널 표시 메뉴는 `dashboard`, `heatmap`이며 `uses_query=False`로 끌 수도 있습니다. 패널 API인 `select`, `set_draft`, `reset_draft`, `apply_draft`로 변경해야 위젯과 신호도 일치합니다. `query_state` 속성을 직접 덮어쓰지 않습니다. 비동기 결과를 받는 화면은 전달받은 `AppliedQuery`를 보관하고 `window.query_state.is_current(query)`가 참일 때만 결과를 표시합니다. 초안 편집만으로 기존 적용 요청을 무효화하지는 않습니다.

선택지 교체는 `window.set_query_catalog(validated_catalog)`로 합니다. 없어진 선택지와 하위 초안은 초기화하지만 이전 적용 스냅샷은 보존하고 ‘선택지 변경 · 재적용 필요’를 표시합니다. 이전 세대의 비동기 결과는 `is_current()`에서 거부하며, 조건을 자동으로 넓혀 조회하지 않습니다. 적용 전 재확인이 필요합니다.

기본 `preview_catalog()`는 VER3의 Site·설비군 표시 선택지만 제공합니다. 실제 설비나 가짜 Fab·Line 데이터를 만들지 않습니다. 현재 DB 스키마에는 Fab이 없으므로 DB 연동 시 별도 매핑 계약이 필요합니다. P1·15L 등의 원본 구역을 이름으로 추론하지 말고 검증된 `QueryCatalog`를 주입해야 합니다. 전체는 해당 항목으로 제한하지 않는 의미이며, 미지정은 값이 없는 자료만 고르는 의미입니다. 추후 Repository의 필터 변환은 이 의미를 유지해야 합니다.

이 작업에는 목표 N·기준일·개인 설정 저장·DB/Excel 조회·대시보드 집계가 포함되지 않습니다. `tests/test_ui_query.py`가 순수 상태, 테스트 전용 부모 관계, Qt 조작·신호·상태 보존·반응형 배치를 검증합니다.

### 대시보드 (작업 단위 4)

| 모듈 | 책임 |
| --- | --- |
| `ui/app.py:create_window` | 예시 자료 공급자와 대시보드·히트맵 조립. 한 번 계산한 결과 공유. 다른 업무 메뉴는 활성화하지 않음 |
| `ui/dashboard_data.py` | 위치·모델 매핑과 기존 `summarize()`를 이용한 읽기 스냅샷. DB·Qt에 의존하지 않음 |
| `ui/dashboard_demo.py` | 합성 설비·관측·고정 목표·위험 판정. 실제 회사 자료와 분리 |
| `ui/dashboard.py` | 지표·현황표·보기 전환·수치표·정확한 후속 조회 범위 신호 |
| `ui/dashboard_charts.py` | 설치된 PySide6의 QtCharts로 동적 도넛·그룹 막대·꺾은선 표시 |

`DashboardSource`에는 검증된 `QueryCatalog`, UI 설비군 키와 모델 ID의 `GroupBinding`, `EquipmentRecord` 관측 목록, 설비별 `EquipmentLocation`, 릴리즈/고정 목표 N, 기준일 위험 판정 결과를 주입합니다. UI 설비군 키를 버전 계열 ID로 취급하지 않습니다. 위치의 원본 구역(`raw_area`)과 정규 Fab·Line은 별개입니다. 누락·중복 모델/위치 매핑과 날짜별 중복 설비는 거부합니다. 위험 판정이 누락되면 안전으로 추정하지 않고 미확인으로 표시합니다.

앱 기본 공급자는 `demo_dashboard_source()`입니다. 예시 Fab·Line은 모두 미지정이고 원본 구역에서 추론하지 않습니다. 자료의 마지막 관측일을 기준일로 사용합니다. 기준일 선택과 목표 N의 실제 저장은 아직 구현하지 않으며, 목표 N 임시 편집은 아래 7번 계약을 따릅니다. 합성 예시라는 사실을 앱 안내·결과·출처 문구에 표시합니다. DB/Excel 파일 읽기·저장·회사 AI를 호출하지 않습니다.

`query_applied` → `DashboardPage.apply_query()` → `DashboardSource.snapshot()` → `accept_result()` 순으로 처리합니다. 공통 초안 편집/초기화만으로는 결과를 바꾸지 않습니다. 선택지 교체 시 결과를 지우고 재조회를 요청하며 공급자의 선택지와도 같아야 합니다. 결과를 받는 시점에 `QueryState.is_current()`와 결과의 요청 객체를 검사하여 이전 요청이 화면을 덮어쓰지 못하게 합니다. 현재 작은 합성 자료만 동기로 계산합니다. 실제 DB/대량 자료 연결 시 조회는 워커로 옮기고 `accept_result()`는 UI 스레드에서 호출해야 합니다.

동일한 `DashboardSnapshot`을 지표·현황표·차트·수치표가 공유합니다.

- 물리 대수는 호기 ID 기준이며 HPA를 포함합니다. 분석 대수·업그레이드 필요 수는 기존 집계 서비스의 결과를 사용합니다.
- KPI/현황표의 달성률은 `N 이상 / (N 이상 + N 미만)`이며 단위는 설비·계열 건입니다. 판단 불가는 별도 표시하며 남아 있으면 100%를 전체 완료로 표시하지 않습니다. 계열 자체가 미등록인 설비도 별도 표시합니다.
- 도넛 구성비는 판단 불가를 포함한 전체 설비·계열 건수가 분모입니다. 중앙에는 전체 건수를 표시해 KPI 비율과 혼동하지 않도록 합니다.
- 막대는 원본 구역·설비군별 물리 대수입니다. KCTC 하위 4종을 분리하며 복수 SW 설비가 여러 설비군에 나타날 수 있으므로 막대를 더해 물리 총대수를 만들지 않습니다.
- 과거 관측은 기준일에 선택한 설비 집합·목표를 고정해 계산합니다. 없는 날짜/관측을 이전 값이나 0%로 채우지 않습니다. 미관측 설비가 있거나 판단 가능한 건이 없으면 추이 선을 끊고 수치표에 부족 수를 표시합니다.
- 비교 범례 체크는 표시할 시리즈만 바꿉니다. 적용 조건·지표·원본 스냅샷을 바꾸지 않습니다.

일반 셀/막대 클릭과 위험 후보 버튼은 `drilldown_requested(DashboardDrilldown)`를 보냅니다. 이 객체에는 적용 요청·기준일·표시 범위·정확한 호기 ID와 `risk_only`가 들어 있습니다. 일반 셀은 해당 범위 전체, 후보 버튼은 해당 범위 후보만 전달합니다. 기본 `DashboardPage`는 신호/안내만 제공하고, 앱에서는 `drilldown_connected=True`로 6번 설비 목록에 연결합니다.

Figma의 그래프 SVG는 고정 예시 데이터이므로 실행 이미지로 사용하지 않습니다. QtCharts를 데이터로 갱신하며 원본 KPI/위험 아이콘 6개는 `ui/assets/`에 그대로 포함합니다. 새로운 의존성은 추가하지 않습니다. 히트맵·설비 목록/상세·목표 설정·실데이터 연동은 이 작업에서 제외합니다.

### 버전 히트맵 (작업 단위 5)

`ui/heatmap_data.py`는 공통 `DashboardSnapshot`을 설비별 `EquipmentTile`로 변환하는 순수 표시 어댑터입니다. 스냅샷의 `selected_records`는 대시보드가 집계한 것과 동일한 기준일·정규 위치·설비군으로 걸러진 관측 목록이며, HPA와 선택된 PM도 보존합니다. 별도 조회나 버전 순서·위험 판정을 실행하지 않습니다. PM/계열별 비교는 `summary.equipment`의 기존 `FamilyStatus`·`VersionComparison`을 사용합니다.

`ui/app.py`에서 조회 적용 → 히트맵 이전 결과 제거 → `DashboardPage.apply_query()` 한 번 실행 → 같은 결과 객체를 `HeatmapPage.accept_result()`에 전달합니다. 실패하면 두 화면 모두 이전 값을 지웁니다. 공통 선택지 교체도 두 화면을 무효화하며 이전 요청·선택지 세대의 결과는 거부합니다. 초안 변경만으로는 기존 적용 결과를 바꾸지 않습니다.

`ui/heatmap.py`는 Site별 물리 타일과 요약, 밀집/버전 함께 보기, 위험 후보만 표시를 담당합니다.

- 한 호기는 한 타일입니다. 복수 SW는 타일 안의 계열별 영역으로 표시하며 모든 선택된 PM의 원문·N 비교를 남깁니다. 같은 계열 PM 버전 불일치도 표시합니다. 문자열 비교나 암묵적 릴리즈 대응은 하지 않습니다.
- 한 계열이라도 미달이면 타일은 미달, 미달 없이 미확인 계열이 있거나 SW 미등록이면 정보 부족, 모든 계열이 N 이상일 때만 목표 달성입니다. HPA는 별도 분석 제외 상태입니다.
- 채움색은 목표 상태, 빨간 테두리/`!`는 위험 후보, `?`는 위험 미확인입니다. VER3 타일 형태·색상 토큰을 사용하되 위험이 목표 채움색을 덮지 않게 분리합니다. ‘후보 아님’을 안전 보장으로 표현하지 않습니다.
- 후보 필터와 밀집/버전 전환은 표시만 바꿉니다. 적용 조건·물리 집합·요약 집계는 그대로입니다. 달성률 단위는 설비·계열 건이며 판단 불가는 분모에서 제외합니다.
- 원본 구역은 Fab/Line으로 추론하지 않습니다. 타일 도움말에는 정규 Fab·Line과 원본 구역을 별도 표시합니다. 버전 원문도 정규화하지 않습니다.
- 타일은 native 버튼으로 Tab/Space 조작·접근성 설명·목표/위험/비교 사유 도움말을 제공합니다. 좁은 창에서는 열 수와 요약 위치를 바꾸며 메인 가로 넘침을 만들지 않습니다. 타일은 이미지가 아닌 실제 위젯입니다.

`equipment_selected(EquipmentSelection)`은 적용 요청 객체·관측 기준일·호기 ID를 전달합니다. 기본 `HeatmapPage`는 신호/안내만 제공하고, 앱에서는 `detail_connected=True`로 6번 상세에 연결합니다. 메뉴 왕복 시 보기/결과/선택을 보존하고 재적용/무효화 시 선택은 지웁니다. `create_window(source=...)`로 검증된 테스트 공급자를 주입할 수 있습니다. 로그인·DB/Excel·목표 편집·저장은 포함하지 않습니다.

Figma VER3 `351:4261`과 보기 선택 `972:12515`, 밀집/버전 타일의 디자인을 PySide6 레이아웃으로 구현했습니다. 기존 원본 `7880d.svg`와 KPI 아이콘을 재사용하고 변형하거나 새 의존성을 추가하지 않습니다.

### 설비 목록·상세 (작업 단위 6)

`ui/equipment.py`의 `EquipmentPage` 한 인스턴스에서 목록/상세 보기를 전환합니다. 상세 상태마다 별도 페이지를 복제하지 않습니다. Figma VER3 전체 목록 `419:14163`과 대표 상세 `499:24803`의 카드·표·이동 구조를 native Qt 모델/뷰로 구현하며, KPI 아이콘·폰트·토큰은 기존 자산을 그대로 사용합니다.

`MainWindow.register_auxiliary_page("equipment", ..., parent_key="heatmap", title="설비 목록")`로 사이드바 항목을 늘리지 않고 하위 화면을 등록합니다. 상위 메뉴는 등록되어 있어야 하며 key/위젯 중복을 거부합니다. 이동 시 상위 메뉴를 활성화하고 기존 페이지 보존 규칙을 따릅니다. 앱은 목록에서 공통 조회 패널을 표시하고 상세에서는 숨깁니다.

앱은 `DashboardPage.apply_query()`가 만든 같은 `DashboardSnapshot`을 세 화면에 전달합니다. `ui/equipment_data.py`는 선택된 호기의 같은 관측일 원본 전체 SW만 모아 기존 `summarize()`로 별도 전체 판정을 계산하고 `EquipmentDetail`에 선택/전체 결과를 구분해 보관합니다. `heatmap_data.record_tiles()`로 표시 규칙을 공유합니다. 원래 선택 SW 스냅샷은 바꾸지 않고, 과거 관측을 현재 값으로 채우지 않습니다. 실제 DB에 추가 조회하지 않습니다.

- 헤더 `equipment_list` 액션은 유효한 결과가 있을 때만 활성화되며 적용 범위 전체를 엽니다. 일반 셀/막대의 `DashboardDrilldown`은 전달 ID 전체, 후보 요청은 전달 후보 ID만 목록 범위로 사용합니다. 날짜·요청 객체·ID 부분집합·중복·후보 여부를 검사합니다.
- 검색·목표 상태·후보 필터·정렬·12대 페이지는 전달 범위 안의 표시만 바꿉니다. `적용 범위 전체로` 버튼만 명시적으로 원래 적용 집합으로 돌아갑니다. 요약은 전달 범위 전체(표시 필터 전)이며 단위·HPA·정보 부족 규칙은 대시보드와 같습니다.
- 목록은 물리 호기당 한 행입니다. `ReadTableModel`은 ID를 행 데이터와 보관하고 셀/도움말을 일반 텍스트로 표시합니다. 정렬·페이지 후 행 번호를 호기 ID로 오인하지 않습니다. 긴 원문은 줄바꿈/도움말로 보존하고, 좁은 창에서는 표 내부만 가로 스크롤합니다.
- 상세는 전체 PM 관측 원문·각 모델/계열의 목표 N·기존 비교 결과·사유를 표시합니다. 선택 SW 성공을 설비 전체 성공으로 바꾸지 않습니다. HPA/SW 미등록도 구분하고 모듈 수나 필수 SW 완전성을 추정하지 않습니다.
- 위험은 N 비교와 독립인 설비 단위 결과입니다. 현재 규칙·Item·릴리즈 문서·원본 셀과 PM별 위험 근거는 미연결이므로 연결되지 않았다고 표시합니다. 목록에 위험 여부가 있어도 세부 근거나 PM별 위험을 만들어내지 않습니다.
- 상세에서 목록으로 돌아오면 필터·정렬·페이지를 유지합니다. 타일에서 직접 진입하면 히트맵으로 복귀하며 보기·선택을 유지합니다. 출발 화면 스크롤도 복귀 시 복원합니다. 초안 편집은 이전 결과를 유지하지만 재적용·선택지 교체·오류는 이전 목록/상세와 선택을 제거합니다. 이전 요청·기준일의 선택은 거부합니다.

현재 합성 자료만 사용합니다. 관측 이력 버튼은 미연결 비활성 상태이며 목록/상세의 수정·저장, 실제 DB/Excel 연동, 로그인은 포함하지 않습니다. 목표 N 임시 편집은 아래 7번 계약으로 연결합니다. `tests/test_ui_equipment.py`가 순수 읽기 모델과 실제 Qt 이동/조작을 검증합니다.

### 목표 N 편집 (작업 단위 7)

`ui/target_dialog.py`는 VER3 목표 선택 팝오버 `423:16903`의 원문 버전 버튼·현재 선택 표시와 조회 미적용/적용 흐름을 여러 모델·계열에 재사용하는 native `QDialog`입니다. 기존 카드/버튼/배지/폰트를 사용하며 새 이미지나 의존성은 추가하지 않습니다. 모델·계열 선택, 릴리즈 선택, 변경 요약, 원래 선택 복원, 완료/취소를 제공하며 취소/Esc/닫기는 공통 상태를 수정하지 않습니다.

`ui/target_data.py`는 DB·Qt에 의존하지 않는 선택 어댑터입니다. 모델 매핑과 관측/기본 목표에 실제 존재하는 `(model_id, family_id)`만 나열하고 UI 설비군과 계열 ID를 동일시하거나 모든 조합을 만들어내지 않습니다. `TargetOption`은 순서 미확인/중복·릴리즈 ID 중복·다른 계열을 선택 불가로 구분합니다. 확인된 Hotfix는 일반 릴리즈와 같은 순서를 사용합니다. 기본 목표가 잘못되어도 자동 연결/대체하지 않으며, 기존 값을 유지하거나 사용자가 명시적으로 목표 미지정을 고를 수 있습니다.

`query_state.TargetN`은 불변 모델 ID·계열 ID·릴리즈 ID 계약입니다. 릴리즈 ID `None`은 명시적 목표 미지정이고 `AppliedQuery.targets is None`은 이전 화면/테스트 호환을 위한 공급자 기본 목표 사용입니다. 두 의미를 합치지 않습니다. `QueryState.set_target_draft()`는 중복을 거부하고 정규 순서의 튜플로 보관합니다. `apply()`는 위치/설비군 초안과 목표 튜플을 새 요청 객체에 함께 고정합니다. 목표 초안 편집은 기존 적용 요청을 무효화하지 않고 `has_pending_changes`에 반영합니다. 위치 초안 초기화는 목표를 변경하지 않습니다.

`QueryPanel.set_target_draft()`는 목표 초안/문구를 갱신하고 `targets_changed`만 발생시킵니다. `applied`는 조회 적용에서만 발생합니다. 앱은 `set_target_describer()`로 선택 중 N과 적용 N의 원문을 구분하여 표시합니다. 기본 공통 패널은 여전히 목표 기능 미연결 상태이며 `create_window()`에서 목표 버튼을 연결/활성화합니다. 대화상자는 같은 창에서 하나만 열고, 완료 직전에 선택지 세대·릴리즈 선택 목록과 전체 목표 집합을 검증합니다. 편집 중 자료 변경은 조용히 덮어쓰지 않습니다.

`DashboardSource.snapshot(query)`는 공급자 기본 목표를 수정하지 않습니다. 적용 요청에 N이 있으면 검증 후 요청 전용 읽기 공급자를 만들어 기존 집계/추이를 계산합니다. 결과의 `DashboardSnapshot.target_values`는 실제 계산에 사용한 N을 고정합니다. 목록 범위 요약과 설비 전체 상세도 이 값을 사용하므로 이후의 목표 초안이 과거 결과에 섞이지 않습니다. 이전 요청 결과/선택 거부와 실패 후 이전 값 제거 규칙은 그대로 유지합니다. 원본 관측 버전·위험 결과·기준일은 N 변경으로 바꾸지 않습니다.

현재 합성 관측과 메모리 내 임시 선택만 지원합니다. 별도 파일/DB 저장, 전사·사용자별 기본 정책 변경, 기준일 편집, 릴리즈 등록/수정, 위험 규칙 변경은 하지 않습니다. 새 창/재실행 시 공급자 초기 N을 다시 사용합니다. 실제 저장이나 비동기 공급자 교체를 연결할 때도 불변 요청의 N과 선택지 세대 검사를 보존해야 합니다.

`tests/test_excel_reader.py`는 실제 Excel 대신 가짜 통합문서를 사용해 읽기 모듈의 순수 로직과 오류 처리를 검증합니다.

회사 AI API 정보(주소·모델·인증·호출 제한)를 받기 전까지는 `infrastructure/ai_client.py`의 `ExampleChatClient`가 미리 정한 정상·실패 응답을 돌려줍니다. `features/ai/`는 Excel 추출·질문 해석·분석 작업의 요청과 응답 형식을 정하고, 호출 실패와 형식 오류를 결과 상태로 바꿉니다. 실제 클라이언트는 같은 `ChatClient` 형식으로 추가하며, 결과의 `source`로 예제 응답과 실제 API 결과를 구분합니다. `tests/test_ai_service.py`가 이 흐름을 예제 응답으로 검증합니다.

`features/version/`은 같은 버전 계열 안에서 설비 버전을 목표 N과 비교해 'N 이상', 'N-k', '판단 불가'를 계산합니다. 순서는 릴리즈 자료에서 확인한 `version_order`만 사용하고 버전 문자열이나 접미사로 추정하지 않습니다. DB 없이 동작하는 순수 계산이며, Repository가 `release_version` 행을 `ReleaseVersion`으로 바꿔 전달합니다. 현황표 원문이 릴리즈 원문과 공백·대소문자·앞의 v·(H) 표식·점과 밑줄만 다르면 `FamilyReleases.candidates`가 후보로 보여주고, 자동으로 연결하지 않습니다. 사용자가 고른 릴리즈는 `equipment_version.release_version_id`로 저장해 비교에 씁니다. `tests/test_version_service.py`가 검증합니다.

`features/aggregation/`은 대시보드·히트맵·AI가 함께 쓰는 공통 집계입니다. 한 기준 날짜의 설비 현황을 받아 PM별로 `features/version/`의 비교를 거친 뒤, 설비·계열 상태와 실제 호기 수, 모델별 대수, 업그레이드 필요 수, N 이상 적용률을 계산합니다. 조회 범위 필터는 호출하는 쪽에서 적용하고, Repository는 `equipment_snapshot`·`equipment_pm`·`equipment_version` 행을 `EquipmentRecord`로 묶어 전달합니다. `tests/test_aggregation_service.py`가 검증합니다.

`features/item/`은 Item이 포함된 버전과 적용 설비를 판정합니다. 버전별로 저장된 Item 상태(`release_version_item`)만 사용하고, 행이 없는 버전은 '미확인'으로 둡니다. 공통 스키마 규칙 H에 따라 `version_order`만으로 이전 버전의 상태를 이어받지 않으며, 누적 포함과 제외 조건은 릴리즈를 저장할 때 버전마다 행으로 반영합니다. 모델 적용 범위로 '대상 외'를 구분하고, 범위를 모르면 '미확인'으로 두어 미적용으로 단정하지 않습니다. 릴리즈 순서는 `features/version/`, 설비 현황 형식은 `features/aggregation/`의 것을 씁니다. `tests/test_item_service.py`가 검증합니다.

`features/aging/`은 버전 출시일(`release_version.released_at`)부터 조회 기준 날짜까지의 경과 기간으로 노후도(안전·주의·위험)를 계산합니다. 경계는 달력 기준이며, 출시일 누락·조회 날짜보다 늦은 출시일·잘못된 기간 기준을 서로 다른 상태로 구분합니다. 혼합 설비는 계열별 기간을 모두 남기고 가장 오래된 버전을 대표로 씁니다. 기간 기준은 `app_setting`의 `version_age_thresholds`에 저장하며, Repository가 `features/storage/`의 트랜잭션으로 읽고 저장하고 음수·역전 등 잘못된 기준은 저장하지 않습니다. 노후도는 위험 판정과 따로 표시합니다. `tests/test_aging_service.py`와 MySQL 통합 테스트 `tests/test_aging_mysql.py`가 검증합니다.

`features/risk/`는 등록된 위험 지정(`risk_rule`)으로 설비와 버전의 위험 여부를 판정합니다. 환경·안전 필수 Item 미적용, 결함 Item의 위험 범위([시작, 해제) 버전) 포함, 직접 지정한 위험 버전 중 하나라도 확인되면 위험이고, 자료가 부족하면 '미확인'으로 둡니다. Item 포함 여부는 `features/item/`의 판정을 그대로 쓰며, 위험은 N 대비 격차·노후도와 따로 판단합니다. `tests/test_risk_service.py`가 검증합니다.

`features/priority/`는 업그레이드할 설비의 우선순위를 정합니다(멘토링 3차 기본 우선순위, 데이터 파트 `ai/svhm/core/risk.py`의 `priority_list`와 같은 규칙). `features/risk/`의 위험 판정으로 위험 설비를 먼저 두고, 같은 위험 수준 안에서는 기준 N에서 가장 많이 뒤처진 칸 수가 큰 설비, 계열 안 버전 위치가 낮은 설비, 그 버전의 출시일이 오래된 설비 순으로 둡니다. 칸 수는 계열의 확인된 순서(version_order)로 세고, 혼합 설비는 PM 중 가장 많이 뒤처진 칸 수를 씁니다. N이 지정되지 않은 설비는 0칸으로 보고 버전 위치로 정렬합니다. 위험이 미확인이거나 어느 PM도 버전 위치를 정할 수 없는 설비는 순위 없이 '확인 필요' 목록에 빠진 근거와 함께 둡니다. `features/aging/`의 노후도는 근거로 함께 보여 주지만 순위에는 쓰지 않습니다. 위험을 미리 판정해 둔 곳은 `prioritize_from_risks()`로 같은 판정 결과를 넘깁니다. '현재 조회 현황 XLSX'(`features/management/report.py`)는 이 함수로 같은 출력 범위·위험 판정·목표 N의 우선순위를 우선순위 시트에 담습니다. `tests/test_priority_service.py`가 검증합니다.

`features/query/`는 AI가 질문에서 찾은 조회 조건을 검사하고 해당 자료를 고릅니다. 질문 해석 때 사업장·라인·설비군(화면 조회 칩과 같은 C4·O2 단위)·모델명·계열·저장된 기준일 선택지(`QueryCatalog.choices()`)를 AI에 함께 주어 그 안에서 고르게 하고, 돌아온 값이 정말 선택지에 있는지 검사합니다. 버전은 등록 릴리즈에서, Item은 `search_items`로 찾으며, 여러 개에 해당하거나 찾지 못한 조건은 후보와 함께 사용자 확인으로 넘깁니다. 확인할 조건이 남으면 조회하지 않고 화면도 옮기지 않습니다. 확정한 조건으로 설비(혼합 설비는 맞는 PM만)를 골라 `features/aggregation/`의 공통 집계를 만들고, 같은 조건 객체를 화면 이동에 넘깁니다. 읽기만 하며 DB 값을 바꾸지 않습니다. `tests/test_query_service.py`가 검증합니다.

`features/analysis/`는 AI 원인·현황 설명(FUN-036·038·039)에 보낼 자료를 만들고 답변의 숫자를 검사합니다. 조회 조건과 계열별 목표 N, 전체·사업장별 집계, 계열별 버전 분포, 사업장·사유별 위험 설비, 통일 지연 근거(목표 N에는 포함되고 설치 버전에는 없는 Item)를 모두 공통 계산으로 만들어 화면과 숫자가 같습니다. `explain()`은 이 자료로 `features/ai/`의 `analyze()`를 호출하고, 답변·근거에 나온 숫자 중 보낸 자료에 없는 것을 `unmatched_numbers`로 돌려줘 화면이 경고할 수 있게 합니다. 버전 표기 안의 숫자는 검사하지 않습니다. `tests/test_analysis_service.py`가 검증합니다.

`features/forecast/`는 버전 통일 참고 예측(FUN-040)을 계산합니다. 계열마다 기간 안의 저장 날짜별 N 이상·N 미만 대수를 설비 1대 × 계열 1건으로 세고, (끝 N 이상 대수 − 처음 N 이상 대수) ÷ 기간 일수로 구한 증가 속도에 끝의 N 미만 대수를 단순 비례해 잔여 기간(일 단위 반올림)과 참고 완료일을 계산합니다(멘토링 3차, 데이터 파트 `ai/svhm/core/forecast.py`와 같은 규칙). 신규·전입 설비가 N 이상으로 들어온 것도 증가에 포함합니다. 결과에는 '참고용 · 데이터 n장'을 붙입니다. 목표 N이 없거나, 이 계열의 날짜가 하나뿐이거나, N 이상 대수가 늘지 않았으면 예측하지 않고 사유를 알립니다. 판단 불가가 남으면 완료로 단정하지 않습니다. `explain_forecast()`는 계산 방법·가정·한계와 함께 결과를 AI에 보내 설명을 받고 `features/analysis/`의 숫자 검사를 적용합니다. `tests/test_forecast_service.py`가 검증합니다.

`features/report/`는 AI 분석 보고서를 Word(docx)로 저장합니다. 이미 받은 설명 결과(`features/analysis/`, `features/forecast/`)만 쓰고 AI를 다시 호출하지 않습니다. 자료 출처(예제 응답·회사 AI), 질문과 조회 조건, 답변의 근거·추정·미확인, AI에 보낸 자료를 그대로 옮긴 수치 표, 참고 예측의 계산 방법·가정·한계, 자료에 없는 숫자 경고를 차례로 넣으므로 보고서 숫자가 화면 집계와 같습니다. AI 호출이 실패해도 수치 표는 남깁니다. 저장 실패는 예외 대신 원인을 담은 결과로 돌려줍니다. `tests/test_report_service.py`가 검증합니다.

`features/export/`는 현황 보고서를 Excel(xlsx)로 저장합니다. 화면에서 계산한 집계·위험·노후도·우선순위 결과를 다시 계산하지 않고 표로 옮기므로 같은 조건이면 화면과 숫자가 같습니다. 조회 조건과 모델·계열별 목표 N을 첫 시트에 남기고, 계열별 막대·상태 구성비 원형·날짜별 적용률 꺾은선 차트는 같은 시트의 표 셀을 참조해 Excel에서 편집할 수 있습니다. 적용률을 계산할 수 없으면 0%가 아니라 빈 셀과 '계산 불가'로 적습니다. 저장 실패는 예외 대신 원인을 담은 결과로 돌려줍니다. 트리맵 차트는 아직 포함하지 않습니다. 관리값 요약 내보내기(상단 내보내기 메뉴 '관리값 요약 Excel')는 `features/export/management.py`의 `save_management_summary(storage, path)`가 맡습니다. 13개 관리 테이블과 보관 원본을 그대로 옮기는 '관리 자료 XLSX'(`features/export/management_tables.py`의 `save_management_data`)와 다른 출력입니다. DB의 현재 관리값을 한 트랜잭션에서 읽어 모델·계열, 설비 현황(PM·적용 SW마다 한 줄), 릴리즈, Item, 위험 기준, 관리 설정 시트로 정리하고 ID 대신 이름을 함께 적습니다. 원본 셀 위치·원문 값은 넣지 않으며(원문은 DB 전체 SQL 내보내기), `=` 등으로 시작하는 글자는 수식으로 실행되지 않게 글자로 저장합니다. `tests/test_management_export_mysql.py`가 검증합니다. `tests/test_export_service.py`가 검증합니다.

데이터 관계를 정리한 [ERD](mermaid-diagram.png)는 작성 중인 초안이며, 확정된 DB 스키마가 아닙니다.

## 폴더별 책임

| 경로 | 책임 |
| --- | --- |
| `src/setron/ui/` | PySide6 화면, 사용자 입력, 화면용 테이블 모델, 백그라운드 작업 실행과 결과 표시 |
| `src/setron/features/` | 설비·버전·Item·Excel·통계 업무 로직 |
| `src/setron/features/storage/` | 공통 Repository, 저장값 변환·입력 검증·삭제 전 위험 근거 보존 |
| `src/setron/features/equipment/` | 검토한 설비 현황 묶음 저장, 호기·날짜·PM 고정 위치 연결, 원문·출처와 정확한 날짜 조회 |
| `src/setron/features/release/` | 릴리즈·Item 등록·수정·조회, 버전별 Item 연결, 원본 셀과 현재 관리값 비교 |
| `src/setron/infrastructure/` | DB 엔진·세션과 외부 기술 연동 |
| `compose.yaml`, `docker/mysql5619/` | 개발·테스트용 MySQL 5.6.19 이미지, 초기 DB·계정 생성과 실행 설정 |
| `database/schema_mysql5619.sql` | 개발·테스트·Windows MySQL 최초 설치에 공통으로 사용하는 5.6.19 스키마 |
| `tests/` | 업무 로직 및 외부 연동 검증 |
| `docs/` | 아키텍처, ERD, 협업 규칙과 테스트 전략 |

## 모듈 간 관계

기능을 구현할 때 다음 원칙을 따릅니다.

- UI는 서비스에 작업을 요청하고 결과를 화면에 표시합니다. DB에 직접 접근하지 않습니다.
- 업무 서비스는 Repository를 통해 조회하고 관리 데이터 변경은 공통 `StorageService`를 사용합니다. 공통 Repository는 같은 DB 세션과 SQLAlchemy로 조회한 테이블 정의를 사용합니다.
- `features/`는 `ui/`와 PySide6를 import하지 않습니다. 업무 로직은 화면 없이 검증할 수 있도록 작성합니다.
- DB 접속과 세션 관리 등 기술적인 연동은 `infrastructure/`에 둡니다.
- `infrastructure/`는 `ui/`와 `features/`를 import하지 않습니다. 초기 의존 방향은 `ui → features → infrastructure`로 유지합니다.

예를 들어 Heatmap 화면은 집계 서비스를 호출하고, 서비스는 Repository를 통해 필요한 설비·버전 데이터를 조회합니다.

Excel 대량 처리처럼 오래 걸리는 작업은 `ui/`의 워커에서 서비스를 호출해 실행합니다. 위젯 생성과 화면 갱신은 UI 스레드에서 처리하고, 워커의 결과나 오류는 Qt 시그널로 전달합니다. 스레드 간 객체 호출은 [Qt의 스레드 규칙](https://doc.qt.io/qtforpython-6/overviews/qtdoc-threads-qobject.html)을 따릅니다.

DB 작업을 실행하는 스레드에서 세션을 열고 작업이 끝나면 닫습니다. UI 스레드와 워커가 같은 `Session`을 공유하지 않습니다. 여러 저장을 하나의 업무로 처리해야 한다면 서비스가 트랜잭션 범위를 정하고, Repository가 호출마다 별도로 commit하지 않도록 합니다. 세션과 트랜잭션의 수명은 [SQLAlchemy의 세션 지침](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)을 따릅니다.

개발·테스트 DB는 각각 다른 컨테이너·계정·포트·볼륨을 사용하며, `mysql-test`는 `test` 프로필에서만 실행합니다. 두 서비스는 Oracle 공식 배포 파일의 체크섬과 버전을 확인해 빌드한 5.6.19 이미지를 공유합니다. InnoDB, utf8mb4, `lower_case_table_names=1`을 사용하고 접속 포트는 `127.0.0.1`에만 공개합니다. Compose는 처음 실행할 때 빈 DB와 일반 계정을 만들며 업무 스키마는 별도로 적용합니다. 환경변수 예시는 `.env.example`, 실행·중지 명령은 [README](../README.md#docker로-개발테스트-db-실행)에 정리합니다.

업무 스키마는 프로젝트의 `database/schema_mysql5619.sql`로 관리합니다. 개발·테스트·Windows MySQL에서 같은 파일을 사용하며, 처음 준비한 빈 DB에 적용합니다. 개발 DB는 작업 데이터를 보관하고 테스트 DB는 테이블 생성·삭제와 실패 상황을 검증하므로 데이터를 분리합니다. 적용 방법은 [README의 공통 스키마 설명](../README.md#공통-스키마)을 참고합니다.

### DB 연결과 공통 저장

`infrastructure/database.py`의 `DatabaseConfig`가 접속 환경변수를 검사하고 `Database`가 연결과 업무별 트랜잭션을 관리합니다. 접속마다 `STRICT_TRANS_TABLES`를 활성화합니다. 업무 실행 전 접속 실패, 저장 실패와 최종 commit 중 연결 중단으로 결과를 모르는 경우를 구분합니다. 세션은 작업마다 열고 닫으며 결과를 모르는 저장은 자동 재시도하지 않습니다.

`features/storage/`의 서비스는 공통 스키마를 조회해 만든 Repository를 사용합니다. 서비스 초기화의 테이블 조회도 공통 트랜잭션과 접속 오류 처리를 거칩니다. 입력 검증·JSON과 원문 변환·DB 변경을 같은 업무 트랜잭션으로 묶습니다. 여러 단계의 저장이 필요한 경우 모든 단계에 같은 세션을 전달하며 Repository는 별도로 commit하지 않습니다.

릴리즈 버전 삭제와 릴리즈 교체 내부의 Item 삭제는 대상과 위험 규칙을 잠근 뒤 기존 근거에 삭제 대상의 원문을 추가하고 `UNRESOLVED`로 바꿉니다. 근거 보존과 삭제는 함께 저장되거나 취소됩니다. 일반 Item 직접 삭제는 릴리즈 관리 서비스에서 제공하지 않습니다. 설비·릴리즈 서비스에서 계열 연결·원본 불변·명시한 모델 범위를 검증하고, 위험 범위와 재가져오기 재계산은 해당 업무 서비스에서 처리합니다.

`features/storage/inputs.py`는 설비와 릴리즈에서 함께 쓰는 필수값·ID·원본 위치 검증을 담당합니다. 기존 `equipment.validation`의 함수 이름은 가져오기를 통해 유지하며 기능 사이에 서로의 검증 모듈을 직접 참조하지 않습니다.

MySQL 회귀 테스트는 테스트 파일 위치를 기준으로 프로젝트 내 스키마를 찾습니다. 별도 스키마 경로 설정 없이 전용 테스트 DB에서 생성·검증·정리하며, 동시 삭제와 저장 도중·COMMIT 전송 직전의 실제 연결 중단도 검증합니다. Windows MySQL은 `.env.windows.example`을 기반으로 별도 접속 설정을 준비하고 실제 서버에서 추가 검증합니다.

### 설비·PM·계열 저장 (BE-002)

`features/equipment/schemas.py`는 저장할 `SnapshotInput`·`SoftwareInput`·`PmInput`과 ID가 포함된 조회 결과를 정의합니다. `validation.py`는 필수값·PM 위치 충돌과 출처 형식을 검사하고 정확한 SW/PM 중복만 합칩니다. 한 필드의 여러 원본 위치는 시트별로 보존합니다. 입력값과 원본을 직접 수정하지 않습니다.

`EquipmentService.save_snapshots()`는 공통 `StorageService`의 한 트랜잭션 안에서 기존 호기 잠금·재사용, 날짜별 현황·SW·PM 생성을 수행합니다. 한 현황에 실패해도 앞서 저장한 다른 현황까지 취소됩니다. 기존 호기·날짜의 현황은 보존하고 검토 오류를 반환합니다. 확인한 계열·모델·릴리즈 연결을 받아 설비군과 릴리즈 계열을 검사하며, 이름이나 형상에서 대응을 추정하지 않습니다. 같은 UI의 C3 5각/6각은 같은 계열 ID를 사용할 수 있고, 구UI/통합UI는 별도 등록 계열로 전달합니다.

`EquipmentRepository`는 전달받은 세션으로만 조회합니다. `get_snapshot()`은 정확한 날짜의 소속·PM 구성·SW 원문을 묶고, 기록이 없는 날짜에는 `None`을 반환합니다. 동일 PM의 날짜 간 연결 키는 `equipment_id + pm_name`이며 날짜마다 다른 `pm_id`를 실물 PM의 식별자로 사용하지 않습니다. 같은 계열 PM들의 다른 버전은 `pm_mismatch_families`에 표시하고 다른 계열 사이의 차이는 제외합니다.

`resolve_model()`은 명시한 최상위 분류 안에서 정확한 등록 모델을 조회하고 확정 별칭 M5만 M-O5로 연결합니다. M-O5 Prime은 원형과 다른 모델 ID 및 부모 관계를 유지합니다. 실제 모델·계열·릴리즈 대응의 검토와 카탈로그 등록, 재가져오기 선택·직접 수정, UI/AI 공급자 연결은 각각의 후속 업무로 남깁니다.

### 릴리즈·Item 관리 (BE-004)

`features/release/schemas.py`는 `ReleaseInput`, `ItemInput`, `ItemLinkInput`과 원본 비교 결과를 정의합니다. `validation.py`는 등록 형식과 편집 허용 필드, 실제 변경 항목, Item 포함 상태·모델 범위를 검사합니다. `ReleaseRepository`는 전달받은 세션으로 조회하고 `ReleaseService`는 공통 저장 서비스로 요청 전체의 트랜잭션을 관리합니다.

`register_release()`는 릴리즈·새 Item·연결을 함께 저장합니다. 직접 등록한 자료는 요청별 MANUAL 입력 작업을 공유하고, Excel 입력은 보관 원본의 시트·셀을 확인합니다. 새 Item은 해당 릴리즈에 APPLIED/UNKNOWN 모델 범위로 연결합니다. 기존 Item 재사용과 상태·범위 변경은 `save_item_links()`에 명시한 연결만 반영합니다. 요청에 없는 연결과 다른 릴리즈는 유지하며 순서만으로 포함 상태를 전파하지 않습니다.

릴리즈의 편집 허용 필드는 버전 원문·접미사·출시일·확인된 순서·Item 매핑 확인 상태이며, Item은 원본 번호에 대응하는 현재 관리 번호·내용입니다. 기존 원본 입력 작업·출처·기본키·계열·노트 연결은 이 API의 편집 대상에서 제외합니다. 수정 대상 행을 먼저 잠그고 `edited_fields`를 실제 변경 항목으로 누적해 동시 수정 시 값과 수정 표시가 유실되지 않도록 합니다.

`RecordView.current`는 DB의 현재 관리값, `original_cells`는 보관된 원본 셀의 필드명·시트·주소·값입니다. 원본은 입력 작업의 raw_data에서 매번 읽으며 관리값을 원본으로 복사하지 않습니다. 원본이 없는 자료는 `has_original=False`로 구분합니다. 여러 Item을 포함하는 `ReleaseDetail`은 미포함·미확인 연결도 숨기지 않습니다.

관리 모델·계열 선택지는 실제 릴리즈가 저장된 등록값으로 제공합니다. 이는 모델별 실제 적용 가능 버전 목록과 구분합니다. 모델 범위의 존재·중복·설비군을 검사하되 실제 자료로 확인하지 않은 대응을 생성하지 않습니다. Item 직접 삭제·비활성화·숨김, B-2 UI의 DB 저장 연결, BE-010의 적용 판정·N 유효성·위험 재계산은 이 서비스 범위에 포함하지 않습니다.

## 기능을 추가할 때

업무 기능은 필요해지는 시점에 `features/` 아래에 분리합니다. 예를 들어 설비 기능은 `features/equipment/`에 추가합니다.

- `service.py`: 업무 흐름과 규칙
- `repository.py`: 해당 기능의 데이터 조회. 관리 데이터 변경은 공통 저장 서비스를 사용하고 DB 엔진·세션 관리는 `infrastructure/`에 유지
- `schemas.py`: 화면과 서비스가 주고받는 데이터 구조

파일은 실제로 맡을 책임이 생길 때 만듭니다. 초기부터 모든 기능에 동일한 파일을 만들 필요는 없습니다.

화면은 `ui/`, 업무 처리는 `features/`에 두고 화면과 서비스 사이의 입력·반환 형식은 함께 합의합니다. 공통 코드도 먼저 해당 기능 안에서 관리하고, 여러 곳에서 필요해질 때 별도 패키지로 분리합니다. AI 연동 등 추가 기능을 위한 폴더도 실제 구현이 필요해질 때 만듭니다.

### 클래스·메서드 주석 작성 기준

새로 추가하거나 수정하는 Python 클래스·메서드·함수에는 아래 형식의 한국어 docstring을 작성합니다. 역할 설명 아래에 `param`, `return`, `raises`, `ivar` 항목을 구분하고 각 설명을 `-` 목록으로 작성합니다. 네 항목의 제목은 모두 콜론 없이 소문자로 통일합니다. 항목 사이에는 빈 줄을 두며, 해당 내용이 없는 항목은 생략합니다. 테스트 함수와 보조 함수에도 같은 원칙을 적용합니다.

- 클래스 첫 문장은 `설비 테이블 레포지토리`, `릴리즈·Item 관리 서비스`처럼 짧은 명사형으로 작성합니다. 데이터 클래스는 `릴리즈 등록 요청 데이터`처럼 용도를 밝히고, `ivar` 항목 아래에 `- 필드명: 설명` 목록을 작성합니다.
- 메서드·함수 첫 문장은 `호기 코드에 일치하는 설비를 조회한다`처럼 수행하는 역할을 적습니다. 업무 규칙이나 원본 보존처럼 필요한 설명은 첫 문장 아래에 짧게 덧붙입니다.
- `param` 항목에는 `- 인자명: 설명` 형식으로 값의 의미, 필요한 형식, 특별한 기본값·None의 의미를 적습니다. `self`와 `cls`는 생략하고 `*args`, `**kwargs`도 실제 인자 이름으로 설명합니다.
- 값이 반환되면 `return` 항목 아래에 `- 결과 설명`을 적습니다. 빈 결과·None의 의미도 설명하며, 생성자와 반환값이 없는 검증 함수에는 반환 항목을 쓰지 않습니다.
- 호출자가 처리할 주요 예외는 `raises` 항목 아래에 `- 예외명: 발생 조건`으로 적습니다. 테스트의 일반적인 assert 실패처럼 자명한 예외는 나열하지 않습니다.
- 상속한 데이터 필드 설명은 부모 클래스를 기준으로 하고, 자식에는 추가하거나 의미가 달라진 필드를 설명합니다. 구현을 그대로 읽어주는 주석보다 판단 근거와 업무상 구분을 설명합니다.

2026-11-02에 BE-001의 `infrastructure/database.py`·`features/storage/`, BE-002의 `features/equipment/`, BE-004의 `features/release/`와 관련 테스트의 주석을 검토·보완했습니다. 입력·반환·예외·필드 설명을 같은 형식으로 맞추고, 트랜잭션 실패와 최종 저장 결과 미확인, 원본 보존과 관리값 편집, 명시적인 Item 연결의 구분을 설명했습니다. BE-006의 Excel 읽기 코드 등은 이번 검토 범위에 포함하지 않았으므로 저장소 전체의 소급 적용이 완료된 상태는 아닙니다.

```python
class ReleaseRepository:
    """릴리즈·Item 테이블 레포지토리."""

    def get(self, tx, table, key, *, lock=False):
        """기본키에 일치하는 관리 행을 조회한다.

        param
        - tx: 현재 업무에서 사용하는 공통 저장 트랜잭션.
        - table: 조회할 관리 테이블 이름.
        - key: 조회 대상의 전체 기본키.
        - lock: True이면 조회한 행을 트랜잭션이 끝날 때까지 잠금.

        return
        - JSON과 UTF-8 바이트를 복원한 관리 행.

        raises
        - LookupError: 해당 기본키의 기록이 없는 경우.
        """
        ...
```

## B-1 릴리즈 목록과 상세

`ui/versions.py`의 `VersionsPage`를 `versions` 메뉴에 `uses_query=False`로 등록한다. 목록·상세는 같은 위젯에서 전환하며 다른 메뉴로 이동해도 상태를 보존한다. 릴리즈 목록의 모델·계열 필터는 설비 관측의 공통 조회 및 목표 N과 분리한다. 기존 `Card`, `StatusBadge`, `button`, `label`, 표 모델/뷰, 줄바꿈 필드 배치와 테마·자산을 재사용한다.

| 모듈 | 책임 |
| --- | --- |
| `ui/version_data.py` | `VersionCatalog`, `ModelFamily`, `ReleaseEntry`, `ItemEntry`, `SourceReference` 읽기 계약 및 기존 버전/Item 서비스 호출. Qt·SQL·저장 없음 |
| `ui/version_demo.py` | 기본 앱에만 공급하는 합성 Item·출처. A의 릴리즈 객체/ID/원문/순서를 재사용하며 출시일 등 기존 누락값을 추정하지 않음 |
| `ui/versions.py` | 모델/계열 선택, 검색, 확인된 순서 정렬, 페이지 이동, 상세·출처 표시, 실패/재시도 |
| `ui/app.py` | 페이지 등록, 상세 제목 및 공통 헤더 상태 연결. `version_loader` 주입 지점 |
| `tests/test_ui_versions.py` | 읽기 규칙, 실패·재시도, 상태 보존, A 상태 분리, Qt 키보드 및 화면 크기별 검증 |

`VersionCatalog`는 불변 dataclass와 tuple로 한 번 읽은 자료를 전달한다. 모델·계열 이름/연결, 릴리즈·Item ID, 버전별 Item 행 중복 및 미등록 대상·모델 범위 오류를 거부한다. 순서 중복은 오류로 버리지 않고 `sort_releases` 결과로 선후 미확인 표시한다. 원문 중복 등 계열 검증은 `FamilyReleases`를 사용한다. 읽기 입력 오류가 나면 이전 상세를 지우고 재시도 상태로 전환한다.

릴리즈별 모델 연결은 `ReleaseEntry.model_ids`로 명시하며 UI 설비군·계열 ID에서 추정하지 않는다. `catalog_from_dashboard`는 기존 공급자에서 이미 등록한 목표/관측의 모델·계열 연결만 읽는다. 이 경로는 실제 릴리즈 문서의 모델 범위 계약을 대체하지 않는다. 실제 공급자는 확인된 문서/모델 연결을 `VersionCatalog`로 직접 전달해야 한다.

포함 Item 수는 해당 릴리즈에 저장된 APPLIED 행 수다. `items_verified=False`이면 0건도 전체 Item이 없다는 뜻으로 쓰지 않고 매핑 미완료를 표시한다. 상세는 같은 계열에서 알려진 Item을 보여주며 해당 릴리즈에 행이 없으면 미확인으로 남긴다. `ItemCatalog.version_state`와 `pm_application`으로 릴리즈 포함 상태와 선택 모델 판정을 분리한다. 후자는 실제 설비/PM 관측을 조회한 결과가 아니며 B-4 적용 현황과 구분한다.

출처는 릴리즈와 Item 각각에 필드·문서·리비전·시트·셀·입력 작업 ID를 묶는다. 없는 정보는 미확인으로 표시하고 문서나 셀을 자동으로 열지 않는다. B-1은 읽기 전용을 유지한다. BE-004는 별도의 RecordView로 원본·관리값 조회와 허용 필드 수정을 제공하며 B-2 화면 연결은 후속 작업이다. O5 추가 추출 필드는 현재 계약에 추가하지 않는다.

`create_window(source=..., version_loader=...)`의 공급자는 **메모리에서 즉시 반환하는** `VersionCatalog`를 제공한다. 실제 DB·Excel I/O를 이 콜백 안에서 실행하면 UI가 멈추므로, 실데이터 통합 때는 워커에서 읽기·오류 처리를 완료하고 자료 갱신을 UI 스레드로 전달해야 한다. 현재 앱은 시작/재읽기 때 DB를 호출하지 않는다.

## B 담당 화면 개발 연결 기준

`ae584c5`의 공통 화면을 기준으로 버전·Item 화면을 `src/setron/ui/`에 추가한다. 별도 웹 frontend나 새로운 공통 창을 만들지 않는다. B-1 읽기 전용 화면은 위 계약으로 구현했으며, 아래의 저장·실데이터·B-2~4 연결은 후속 구현 기준이다.

| 작업 | 재사용 지점 | 후속 연결 사항 |
| --- | --- | --- |
| B-1 버전 목록·상세 | `features/version/service.py`의 `sort_releases`, `FamilyReleases` | 모델·계열별 목록, 출시일, 포함 Item과 출처를 전달할 읽기 데이터 계약 |
| B-2 버전 관리·수정 | `features/release/service.py`의 `ReleaseService` | BE-004의 허용 필드·원본 비교 계약을 사용해 UI 편집·검증·저장·취소·오류 및 저장 후 재조회 연결 |
| B-3 Item 매니저 | `features/item/service.py`의 `search_items`, `ItemCatalog` | 검색·상세·포함 릴리즈·모델 범위 표시. 빈 검색어의 `search_items` 반환은 빈 튜플이므로 전체 목록 표시와 구분 |
| B-4 Item 적용 현황 | `ItemCatalog.summarize`, 기존 `EquipmentPage` | Site별 네 상태 표시, 동일 설비 ID·관측일·적용 요청으로 상세 이동 및 복귀 |

`MainWindow.register_page("versions", ...)`, `register_page("items", ...)`로 페이지를 연결하고 공통 컴포넌트·테마·자산을 재사용한다. 공통 조회 패널 사용 여부는 `uses_query`로 명시한다. 목표 N 선택은 기존 `TargetDialog`와 초안/적용 상태를 재사용하며 릴리즈 수정과 분리한다.

`EquipmentPage.open_detail()`은 A의 적용 조회 요청을 검증한다. B-4는 이 경로에 `origin="items"`만 전달하지 않고 아래 `open_item_detail()` 계약으로 같은 상세 위젯을 재사용한다. 현재 적용 집합 밖의 설비를 열기 위해 A 조회 범위를 조용히 넓히지 않는다.

버전 순서는 확인된 `version_order`만 사용한다. 순서 미확인·중복을 구분하고 원문·접미사를 보존한다. Item은 `Application`의 적용/미적용/미확인/대상 외를 사용한다. 더 최신 버전이라는 이유로 Item을 상속하지 않는다. 버전별 저장 행과 모델 적용 범위가 근거이며, 위험 판정과 목표 달성도 별개다.

현재 `ReleaseVersion`과 `Item`의 순수 업무 형식에는 전체 출처 정보가 없다. DB의 `batch_id`, `source_reference`, 릴리즈 문서와 원본을 연결하는 읽기 계약은 C 및 데이터 담당자와 맞춘다. O5의 JIRA·접수라인·비고는 현재 `RELEASE_NOTE_FIELDS`에 없으므로 임의 필드로 저장하지 않는다. Fab·Line 및 UI 설비군/모델/계열 매핑, 목표 N 영속화, 저장 후 선택지·결과 갱신은 A와 함께 확인할 통합 사항이다.

화면 위젯에서 SQL을 실행하지 않는다. BE-004의 저장·조회 서비스와 실제 데이터 공급자를 워커로 연결하기 전까지 합성 자료·미연결 상태를 명시한다. 위험 규칙 편집 정책을 임의로 구현하지 않으며 기존 설비 상세·목표 편집 화면을 복제하지 않는다.

### Item 매니저 (B-3)

`ui/items.py`의 `ItemsPage`는 `uses_query=False`인 `items` 메뉴를 사용한다. 공통 Card·StatusBadge·읽기 표·버튼·폰트와 기존 B-1 출처 표시를 재사용한다. 화면 SQL·파일 저장·편집 정책은 추가하지 않는다. BE-004의 등록·수정 서비스를 사용하는 B-2 편집·저장 화면 연결은 후속 구현이다.

`ui/item_data.py`의 불변 `ItemManagerData`는 B-1 `VersionCatalog`, 한 관측일의 `EquipmentRecord` 튜플, `base_date`, 관측 연결 여부와 출처 문구를 묶는다. 혼합 관측일·중복 설비 ID·기준일 없는 연결 자료를 거부한다. 최신 관측일은 A 공급자의 `dates[-1]`이며 없는 설비를 과거 자료로 채우지 않는다. Fab·Line을 추론하지 않는다.

`create_window(item_loader=...)`로 메모리 자료를 주입한다. 실제 I/O는 외부 워커에서 완료한 뒤 UI 스레드에서 `reload()`를 호출하는 연결 작업이 필요하다. 커스텀 `version_loader`만 주입하면 기본 합성 관측과 임의 결합하지 않고 관측 미연결로 표시한다. 기본 앱만 기존 A 관측·B-1 예시 릴리즈를 함께 사용한다.

내용·번호 검색은 `features/item/service.py`의 `search_items`, 릴리즈·PM·설비 판정은 `ItemCatalog`를 재사용한다. 릴리즈는 계열별 `version_order` 순이며 순서 미확인·중복 표시를 유지한다. 모델 범위와 포함 상태는 구분한다. 전혀 연결되지 않은 Item은 전체 설비 대상 외로 확대하지 않고 적용 현황 미확인으로 안내한다. 관측 미연결과 연결된 빈 결과도 구분한다.

설비 표는 공급된 기준일의 전체 관측 범위이며 상태별 필터와 PM별 근거를 제공한다. 현재 A 공통 조회 조건은 적용되지 않는다고 본문에 명시하고 목표 N을 변경하지 않는다. B-4에서 배포 히트맵·기존 설비 상세 이동을 연결한다. 조회 실패 시 이전 상세를 제거하고 재시도를 제공한다.

### Item 배포 시각화·근거 연결 (B-4)

`ui/item_distribution.py`는 공통 읽기 표·색상 토큰과 `heatmap.TileGrid`를 재사용한다. Site×네 상태의 물리 설비 대수를 표시하고 상태 셀 선택을 Site/상태 필터에 연결한다. 설비 타일과 목록은 동일한 `EquipmentApplication` 결과를 사용한다. 미확인과 대상 외를 합치지 않으며 적용 여부를 위험으로 해석하지 않는다. 표 스타일이 배경 역할을 덮는 경우에도 상태 색상이 보이도록 이 표에만 delegate를 사용한다.

`ItemEquipmentSelection`은 불변 ItemManagerData 객체, 화면 세대, Item ID, 설비 ID, Site·상태 범위를 고정한다. `ItemsPage.is_current_selection()`은 자료 객체 동일성·현재 Item/범위/세대·선택 설비를 검사하고 `selected_application()`은 실제 레코드·Item 연결·HPA 제외·범위를 다시 검사한다. 관측일은 요청의 ItemManagerData에 고정된다. 다시 불러오기·다른 Item/범위·다른 설비 선택 후 오래된 요청은 거부한다. 재생성된 타일의 이전 이벤트도 표시 세대로 거부한다.

`EquipmentPage.open_item_detail(request, is_current=...)`는 기존 상세 카드·PM 표·복귀 버튼을 재사용한다. A 스냅샷을 Item 자료로 대체하지 않고 N 비교 대신 Item 판정과 근거를 표시한다. Item·PM 대응 릴리즈에 실제 존재하는 출처만 `item_evidence_rows()`로 묶는다. 저장 행 누락·출처 누락은 명시하고 위험 근거·원본 파일 열기는 미연결로 둔다. Item 진입 전 A 상태를 보존하고 이 경로를 나갈 때 복구한다. 별도 설비 상세 화면은 만들지 않는다.

앱은 Item 화면에서 발생한 유효한 요청만 수용한다. 상세 왕복 시 검색·페이지·Site·상태·보기·선택·스크롤을 유지한다. 자료/범위가 무효화되면 열린 Item 설비 상세를 닫고 Item 화면으로 복귀한다. Item 근거를 보고 있을 때 A 전체 설비 목록 액션은 비활성화한다. 기존 A 조회 조건·목표 N 변경/확장이나 SQL 실행은 하지 않는다.

## 문서 갱신

폴더를 추가하거나 이동하고, 모듈의 책임 또는 호출 관계를 바꾸면 같은 변경에 이 문서도 갱신합니다. 프로젝트 전체 구조가 달라지면 README의 구조 설명도 함께 수정합니다.

## B-2 편집 공통 기반 (실제 항목·저장 미연결)

`ui/version_edit_data.py`는 명시적으로 주입한 텍스트 항목의 원본·시작 관리값과 초안, 불변 변경 요청을 분리합니다. 실제 릴리즈 필드 목록이나 DB DTO를 새로 정의하지 않습니다. 원본 미연결(None)과 빈 원본값을 구분하며 검토 시 변경 항목만 검증합니다. 날짜·정수·Item 연결 등 업무별 입력과 BE-004 어댑터는 후속 작업입니다.

`VersionsPage`의 릴리즈 수정 버튼은 `VersionEditDialog`를 엽니다. 기본 경로는 항목이 비어 있고 gateway=None이므로 편집 항목 대기와 저장 미연결을 안내하며 저장할 수 없습니다. 기존 공통 Card·StatusBadge·표·버튼·테마를 재사용하고 모달 안에서 목록 선택과 A 상태를 유지합니다.

외부에서 항목과 비동기 EditGateway를 명시적으로 주입하는 테스트 경계에서만 초안 → 검토 → 제출 → 결과 흐름을 실행합니다. gateway.submit은 즉시 반환해야 하며 완료 콜백은 Qt queued signal로 GUI 스레드에 전달합니다. DB나 파일 I/O는 UI 위젯에 추가하지 않았습니다. 성공 응답만 성공으로 표시하며 실패는 입력 유지 후 재검토, 결과 미확인/제출 예외는 자동 재시도 금지로 처리합니다. 요청 번호와 상태로 중복·오래된 응답을 무시합니다. 저장 중 입력·중복 제출·닫기를 차단하고 취소/Escape/창 닫기는 초안 폐기를 확인합니다.

BE-004의 ReleaseService 및 허용 필드는 현재 develop에 존재하지만 이번 B-2 기반에서는 연결하지 않습니다. 저장 후 실데이터 재조회·재판정, 실제 DB 저장, Excel 출력은 이번 범위가 아닙니다. 향후 정정 연결 시 과거 관측 사실은 유지하고 정정된 릴리즈로 조회·새 출력 결과를 다시 판단합니다.

## C 담당 화면 · Excel 가져오기와 AI 분석

`import` 메뉴에 `ui/import_page.py`의 `ImportPage`, `ai` 메뉴에 `ui/ai_page.py`의 `AIPage`를 `uses_query=False`로 등록한다. 두 화면은 SQL을 실행하지 않고 주입받은 Excel 읽기·AI·저장 객체에 요청한다. `ui/app.py`는 공통 셸 규칙에 따라 UI 모듈만 가져오며, 기본 예제 응답·메모리 저장소는 각 화면 모듈이 채운다.

| 모듈 | 책임 |
| --- | --- |
| `features/excel_import/schemas.py` | 시트 원본(`SheetSnapshot`), 검토 행(`ReviewRow`: AI 원본과 수정값 분리), 검토 항목, 저장 요청·결과, `ImportSaver` 형식 |
| `features/excel_import/service.py` | 원본 고정, AI 결과→검토 초안, 수정·제외·되돌리기, 저장 전 검사, 저장 요청 생성, 메모리 저장소, 저장 오류의 화면 상태 변환 |
| `ui/import_page.py` | C-1 통합문서·시트 선택과 원본 미리보기, C-2 추출 행 검토·수정, C-3 확인창·저장 중·성공·실패·결과 미확인 표시와 중복 저장 방지 |
| `ui/import_demo.py` | `--demo-excel` 실행과 테스트용 합성 통합문서, 회사 AI 연결 전 종류별 예제 추출 응답 |
| `ui/ai_data.py` | C-4 질문 조건과 공통 조회 선택지의 정확한 대조, 적용 가능 여부, 분석에 보낼 대시보드 계산값 |
| `ui/ai_page.py` | 질문 해석, 조건 확인·직접 제외, 확인 후 공통 조회 적용·화면 이동, 근거·추정·미확인 분리 표시 |

가져오기 화면은 메뉴 진입만으로 Excel에 연결하지 않는다. 사용자가 불러오기를 누르면 `excel_reader`로 열린 통합문서 목록을 읽고, 시트를 읽은 시점의 셀 값·주소·병합 범위를 `SheetSnapshot`으로 고정한다. AI 추출값(`original`)과 사용자 수정값(`edits`)을 따로 두며, 수정하지 않은 AI 값은 출처 셀의 원본 값과 비교해 다르거나 위치가 없으면 확인 필요로 표시한다.

저장 전 검사에서 필수값 공란·날짜/숫자/Memory·Foundry 형식·길이·같은 PM의 상충 버전·같은 설비/기준일의 소속 불일치·릴리즈 Item 내용 상충은 오류이며, 하나라도 남으면 묶음 전체를 저장하지 않는다(HPA 포함). 정확한 중복은 원본 위치를 모아 한 행으로 합치고, 다른 PM의 버전 차이는 저장을 막지 않는 예외로 안내한다. 설비 미존재·미등록 버전은 `ReferenceData`를 받을 때만 확인 필요로 표시하고, 다운그레이드처럼 이전 현황이 필요한 검사는 기준 자료 연결 전까지 안내로 남긴다.

`SaveRequest.raw_data`는 시트명·셀 주소별 원본값과 병합 범위만 담고 파일명은 넣지 않는다(파일명은 확인창에만 표시). 각 행은 정규화한 저장 값, 필드별 `{sheet, cells}` 출처, `edited_fields`를 가진다. 현재 기본 저장소는 `MemoryImportSaver`이며 조회 화면 자료를 바꾸지 않는다. 저장 후에는 재조회를 안내하고 대시보드로 이동만 한다. `DatabaseCommitUnknownError`는 결과 미확인 상태로 바꿔 재저장을 막는다.

AI 분석 화면은 질문을 해석한 뒤 아무 조건도 바꾸지 않는다. Site·Line·설비군은 공통 조회 선택지의 이름·키와 정확히 같을 때만 연결하고, 원본 구역명을 Fab/Line으로 추정하지 않는다. 복수 선택·기준일·모델·SW 계열·버전·Item·해석 불가 조건은 그대로 표시하며, 사용자가 직접 체크를 해제하기 전에는 적용을 막는다. 확인창 승인 후 공통 조회 초안을 바꾸고 적용하며, 목표 N은 바꾸지 않는다. 목표 N 변경이 적용 대기 중이거나 해석 뒤 선택지가 바뀌면 적용하지 않는다. 분석은 현재 적용 조건의 대시보드 계산값만 보내고, 예제 응답의 근거 문장은 자료로 확인한 근거가 아니라고 표시한다.

DB 저장 서비스(`ImportSaver` 구현)는 아래 "가져오기 DB 저장"에 있다. 후속 통합 사항: `ReferenceData` 공급, 회사 AI 실제 클라이언트, Excel 읽기·AI 호출의 워커 스레드 실행, 저장 후 선택지·결과 갱신 방식은 A·B·데이터 담당자와 확정한다. O5의 JIRA·접수라인·비고는 `RELEASE_NOTE_FIELDS`에 없어 추출·저장하지 않는다.

### 가져오기 DB 저장 (Jira S15P31B106-75)

`features/import_save/service.py`의 `DatabaseImportSaver`가 `ImportSaver`를 구현한다. `save(request)`·`save_many(requests)`는 시트별 `SaveRequest`를 한 트랜잭션으로 저장한다: 묶음 전체 재검증 → 시트마다 `import_batch`(EXCEL, `{시트: {셀: 값}}` 원본) → 설비 현황(`EquipmentService.import_snapshots_in`, 신규 저장 또는 기존 현황 부분 갱신) 또는 릴리즈·Item(`ReleaseService.register_release_in`). 하나라도 실패하면 모든 시트의 입력 작업까지 함께 취소한다. `*_in` 메서드는 호출자가 연 트랜잭션을 받는 공개 진입점이다.

- 설비 행은 `호기 + 기준일`로 묶어 현황 하나로 만들고, 같은 계열·같은 버전 원문 행은 SW 하나로 합친다. PM 이름이 있는 행은 그 SW의 PM이 되고 PM 모델은 행의 모델이다. 모델이 하나면 대표 모델, 여러 개면 대표 모델 없음(혼합형)이다.
- 모델은 설비군(Poly·Oxide·KCTC·HPA, 없으면 확정된 모델 소속) 안에서 이름이 정확히 같은 등록 모델만 연결한다. 별칭은 M5 → M-O5만 쓴다. 계열은 모델(또는 기본 모델)의 계열 중 이름이 같은 것, 없으면 "이름 + 공백"으로 시작하는 것을 후보로 두고 UI 구분(구UI·통합UI)으로 하나를 고른다. 하나로 정해지지 않으면 저장하지 않는다.
- 설비 적용 버전은 같은 계열·같은 버전 원문 릴리즈가 있을 때만 연결한다. 릴리즈는 `계열 + 버전 원문`으로 묶고, Item은 APPLIED·범위 미확인으로 연결한다. 검토 화면에서 고친 항목은 출시일·버전·Item 번호·내용만 `edited_fields`에 남긴다.
- 같은 호기·기준일 현황이 이미 있으면 `load_reference()`로 기존값을 불러와 검토한 요청만, 검토 당시 값과 DB 값이 같을 때 갱신한다. 검토 없이 보냈거나 검토 후 DB 값이 바뀌었으면 묶음 전체를 보류한다. 재가져오기 규칙(PM 위치별 갱신, 사업장 고정, 공통정보 출처 등)은 README의 "날짜별 현황 저장·재가져오기 갱신 (BE-009)"에 있다.
- 같은 계열·버전 릴리즈가 이미 있으면 묶음 전체를 저장하지 않는다. 릴리즈 교체(BE-010), 저장 후 직접 수정(BE-025), 릴리즈 노트 문서 등록, Item 대상 모델 범위 지정은 이 저장소 범위가 아니다.
- 앱(`python -m setron`)은 `SETRON_DB_NAME`이 설정돼 있으면 `ui/import_page.saver_from_env()`로 이 저장소를 쓰고, 없으면 메모리 저장소를 쓴다. DB 접속은 첫 저장 때 한다.
- 같은 설정이면 조회 화면도 `ui/database_source.py`의 `DatabaseDashboardSource`로 DB 자료를 읽는다. '조회 적용'마다 DB를 다시 읽으므로 저장 후 조회 적용을 누르면 반영된다. 조회 선택지는 DB 저장 현황으로 만든다(아래 '조회 선택지 DB 연결'). 설비군은 `GROUP_MODELS`의 등록 모델 이름으로만 연결한다. 연결되지 않거나 PM·HPA 연결을 확인해야 하는 현황은 빼고 건수를 안내한다. 목표 N·위험 기준은 DB에 없어 지정하지 않으므로 적용률은 계산 불가, 위험은 미확인으로 나온다. 목표 초안은 다시 읽은 모델·계열 목록에 맞춘다(고른 값 유지, 새 항목 미지정). 시작할 때 DB를 읽지 못하면 합성 예시로 시작하고 안내한다.
- 위험은 DB의 `risk_rule`과 `release_version_item`으로 만든 `RiskAssessor`로 판정한다. 규칙 변환은 릴리즈 교체 재판정과 같은 `features/release/refresh.py`의 `rule_from_row()`(잘못된 연결은 미확인)·`families_from_rows()`를 쓴다. 사용 중인 위험 지정이 없으면 '위험 없음'으로 단정하지 않고 모두 미확인으로 둔다. 목표 N은 DB에 저장하지 않으므로 화면에서 고른다.
- 상단 '데이터 내보내기'는 `SETRON_DB_*` 설정이 있을 때 관리 데이터 Excel(`data_exporter_from_env`)과 DB 전체 SQL(`sql_exporter_from_env`) 중 하나를 골라 실행한다.
- `SETRON_AI_*` 설정이 있으면 `ui/ai_connection.py`의 `ai_providers_from_env()`가 회사 AI 클라이언트 하나로 가져오기 추출·질문 해석·분석 공급자를 만들어 화면에 넘긴다. 설정이 없으면 예제 응답, 잘못되면 예제 응답으로 시작하고 이유를 안내한다. 클라이언트는 앱을 닫을 때 정리한다. AI 호출은 아직 화면 스레드에서 실행된다.
- `database/reset_demo_db.py`는 시연·확인용 DB의 관리 테이블을 지우고 스키마와 시연용 기준 자료를 다시 넣는다. `--confirm`에 접속 설정의 DB 이름을 다시 적어야 실행되고, 관리 테이블 외의 테이블은 건드리지 않는다.
- `database/seed_demo_catalog.sql`은 멘토 확정 분류표(`docs/mentor-confirmations.md`)의 모델 14개·계열 15개다(v2). 파운드리용 릴리즈노트가 따로 있는 O2·C3·C4는 ' (파운드리)' 계열을 두고, `features/reference_data.py`의 장소표(사이트·라인 → 메모리/파운드리)로 저장 시 계열을 고른다. 번역표(원문 설비군 글자 → 등록 모델)도 같은 파일이 기준이며 `extraction/model_candidates.py`는 그것으로 후보를 만든다.

### AI 화면의 원인 설명·예측·Word 보고서 연결 (BE-018·019·020·022)

`ui/analysis_data.py`가 대시보드 스냅샷과 공급자 자료를 분석 입력으로 바꾼다. 화면과 같은 숫자를 쓰도록 선택 설비·계산에 쓴 목표 N·날짜별 관측을 그대로 옮기고, DB 공급자가 함께 둔 위험 판정기(`source.assessor`)·Item 자료(`source.item_catalog`, 릴리즈 목록의 Item)를 넘긴다. 합성 예시 공급자에는 위험·Item 자료가 없어 '미계산'으로 보낸다.

- 분석: `analysis_hooks()`의 `explainer`가 `features.analysis.explain()`을 불러 사업장별·버전 분포·위험·통일 지연 근거·날짜별 추이를 AI에 보내고, 답변 숫자를 보낸 자료와 대조해 AI 화면 '숫자 확인'에 보여 준다. 분석 공급자(`ai_analyzer`)를 직접 넘기면 기존 방식(`facts_from_snapshot` + `analyze`)으로 분석한다.
- 예측: '통일 예측 (참고)'가 같은 조회 조건의 모든 관측일(`DashboardSource.scoped_records`)로 `forecast()`를 계산하고 `explain_forecast()`로 설명을 받는다. 기간 끝은 스냅샷 기준일이다.
- 보고서: 'Word 보고서 저장'이 마지막 분석·예측 결과로 `save_word_report()`를 부른다. AI를 다시 호출하지 않는다.
- 질문 해석: AI 클라이언트가 있으면 `choice_parser()`가 현재 화면의 Site·Line·설비군 선택지를 `QueryChoices`로 함께 보낸다.
- 앱(`main`)은 `ai_client`를 넘기고, 설정이 없으면 예제 응답 클라이언트로 같은 흐름을 쓴다. AI 호출은 아직 화면 스레드에서 실행된다.

### 조회 선택지 DB 연결

DB 공급자는 `ui/database_source.py`의 `database_catalog()`로 저장 현황의 실제 Site·Line을 조회 선택지로 만든다.

- Site: VER3 선택지(평택·화성·기흥)는 키를 그대로 두고, DB에만 있는 Site를 이름 그대로(키 = 이름) 뒤에 붙인다.
- Line: 관측된 (Site, Line) 조합. 키는 `Site 키:Line`이고 Fab 매핑 자료가 없어 Fab은 지정하지 않는다.
- 설비군: VER3 선택지 그대로.
- 조회 중에는 선택지를 고정한다(`refresh()`는 지금 선택지로 다시 읽는다). 선택지에 없는 Site의 현황은 빼고 건수를 안내한다.
- 가져오기 저장이나 관리 저장이 끝나면 앱이 `reload_catalog()`로 선택지를 다시 만든다. 선택지가 바뀌면 적용 조건이 무효가 된다. 관리 저장 뒤에는 적용 조건·목표 N이 초안과 같고 새 선택지에서도 유효할 때 같은 조건을 자동으로 다시 적용한다. 그렇지 않으면 조회 적용을 안내한다.
- 선택지를 받지 않는 로더(`lambda: load_dashboard_source(storage)`, 테스트용)는 읽을 때마다 로더가 만든 선택지를 쓴다.

### 화면 표시 공통 규칙 (2차 점검 반영)

- 결과 배지: 공급자의 `result_label`을 쓴다. 합성 공급자(`DashboardSource`)는 "예시 결과", DB 공급자(`DatabaseDashboardSource`)는 "DB 자료".
- 계열·모델 이름: `ui/dashboard_data.py`의 `family_name(source, id)`·`model_name(source, id)`로 찾는다. DB 공급자는 `family_names`·`model_names`를 갖고, 이름이 없는 합성 자료는 번호를 그대로 쓴다.
- 요약 카드: `count_value(count, unknown, label)`로 0대인데 판단하지 못한 대상이 있으면 큰 숫자를 '미확인'(위험)·'판단 불가'(업그레이드 필요)로 보인다. 1대 이상이면 확인된 대수를 보이고 미확인 수는 설명 줄에 적는다.
- DB 모드 설비군 선택지는 `GROUP_MODELS`의 모델이 DB에 등록된 것만 둔다.
- HeatMap 타일 격자는 세로로 내용 높이만 쓴다(큰 창에서 타일이 늘어나지 않음).

### 남은 BE 작업 연결 (11-04)

- 위험 기준 새로 등록 (BE-015): `ManagementService.create_risk_rule(values)`가 같은 트랜잭션에서 연결 검사(`validate_risk_in`) → 등록 → 재판정 → MANUAL 입력 작업 기록을 한다. 재연결 근거(`target_reference`)는 릴리즈 교체가 읽는 키(`family`·`item`·`direct_version`·`start_version`·`end_version`)로 만든다. 관리 화면 '위험 기준 새로 등록'(`ui/risk_rule_dialog.py`)이 유형별 연결 칸을 보여 주고, 저장은 묶음 저장과 같은 `saved` 신호로 화면을 갱신한다.
- 버전 변경 이력 (BE-019): `analysis.service.version_changes()`가 같은 조회 범위의 기준일 이하 관측(`DashboardSource.scoped_records`)에서 같은 설비·계열의 이웃한 두 관측일 사이 버전 변경을 '버전 변경 이력'으로 AI에 보낸다. 방향은 확인된 릴리즈 순서로만 정한다(아니면 '순서 미확인').
- 예측 질문 연결 (BE-018 → BE-020): `forecast.service.is_forecast_question()`이 '언제·예측·완료 시점·남은 기간' 등의 표현을 찾으면 AI 분석이 끝난 뒤 같은 조회 범위로 참고 예측도 실행한다.
- 우선순위·노후도 화면 (BE-016·017): 설비 목록 정렬에 '업그레이드 우선순위'를 더했다(`equipment_data.upgrade_priority`, 화면과 같은 위험 판정·목표 N 사용). 설비 상세에 순위(또는 확인 필요 사유)·목표 N보다 낮은 단계·가장 오래된 버전 경과를 보인다. DB 공급자는 저장된 기간 기준(`thresholds_in`)을, 합성 자료는 기본값을 쓴다. 'N보다 몇 단계 낮음'(BE-003)은 이미 비교 표시('N-2')에 있었다.
- 트리맵 (BE-021): 현황 보고서에 '구성 트리맵' 시트(계열 > 상태별 설비·계열 건수)를 두고, 저장 뒤 `export/treemap.py`가 Excel 2016 이상의 chartEx 트리맵을 넣는다. 차트는 숨은 이름(`_xlchart.v1.N`)으로 표 셀을 참조하고, Excel이 요구하는 스타일·색 부품(`chartex/style.xml`·`colors.xml`, Excel 기본값)을 함께 넣는다. 점 색은 Excel 트리맵의 점 번호(상위 칸 → 하위 칸, 표 순서)에 맞춰 앱 상태 색으로 칠한다. Excel 16.0에서 열어 차트 종류 117(트리맵)과 색을 확인했다. 2016 미만 버전은 안내 도형이 보인다.
- 실제 AI 응답 대응 (BE-005): 실제 GMS(gpt-5) 분석 응답이 `unconfirmed` 항목을 `{"문장": null}` 객체로 보내 형식 오류가 났다. 분석 답변의 문장 목록은 이런 객체도 문장으로 받고(`_sentences`), 프롬프트에 '문장 문자열 목록'을 명시했다.

### 데이터 파트 후보 연동 (번역표·버전 순서)

데이터 파트(`ai/svhm`)의 결정적 규칙 두 가지를 **후보로만** 가져왔다(팀 결정 2026-11-03). 사용자가 확인해야 값이 바뀐다.

- 모델명 번역표: `features/extraction/model_candidates.py`가 `ai/svhm/core/refdata.py`의 원문 모델 표기 대응을 시연용 시드 모델 이름(M-C3·M-C4 등, 9계열)으로 옮긴 표다. `features/excel_import/candidates.py`의 `apply_model_candidates(draft)`가 모델명은 등록 모델 이름으로 바꾸고 빈 설비군·계열·UI·구조·개조만 채운다. 가져오기 화면의 '번역표 후보 적용 (n행)' 버튼이 확인창 승인 후 적용하며, 바뀐 값은 사용자 수정으로 기록되고 원문은 그대로 남는다. 릴리즈노트 모델 칸처럼 계열 이름만 적힌 `C4`는 데이터 파트 설비군 이름을 근거로 M-C4 후보를 내고 주의 문구를 붙인다. 시드에 없는 모델(KCTC Inline Ox·Cluster Ox)은 후보를 내지 않고, C3 UI 미표기의 '통합UI'처럼 확정 사항에 없는 값은 확인창에 주의 문구로 보여 준다.
- 버전 순서: `features/release/order_candidates.py`의 `suggest_version_orders()`가 저장된 릴리즈의 버전 셀 행 순서(릴리즈노트 나열 순서)로 10, 20, 30 …을 제안하고 출시일이 거꾸로 가면 경고한다(`ai/svhm/excel/release_parser.py`와 같은 규칙). 순서가 확인된 릴리즈가 있거나, 원본 위치가 없거나, 여러 입력 작업·시트에서 온 계열은 제안하지 않는다. 관리 화면의 '선택 계열의 버전 순서 후보를 수정 목록에 추가'가 후보를 수정 목록에 넣고, 기존 묶음 검토·저장(`ManagementService.save`)을 거쳐야 반영된다.

### DB 전체 SQL 내보내기 (Jira S15P31B106-76)

`features/db_export/service.py`의 `export_database(database, path)`가 관리 테이블(`TABLE_NAMES`) 13개의 구조(`SHOW CREATE TABLE`)와 모든 행을 한 트랜잭션에서 읽어 .sql 파일로 저장한다(멘토링 2차, REQ-054). 받은 사람이 빈 DB에서 실행하면 같은 자료와 AUTO_INCREMENT 값이 들어간다. `DROP TABLE`을 넣지 않으므로 같은 테이블이 있는 DB에서는 첫 `CREATE TABLE`에서 멈추고 기존 자료를 바꾸지 않는다. 다른 PC 자료의 가져오기·병합은 보류(REQ-055)다. 실행 중에만 외래키 검사를 끄고, 임시 파일에 다 쓴 뒤 이름을 바꿔 실패해도 반쯤 쓴 파일이 남지 않는다. 값은 pymysql의 `escape`로 적는다. 실패는 예외 대신 `ExportResult`로 돌려준다.

화면에서는 상단 '데이터 내보내기' 버튼으로 실행한다. `SETRON_DB_*` 설정이 있을 때만 `ui/database_source.sql_exporter_from_env()`가 내보내기 함수를 만들고 버튼을 켠다. 저장 위치를 묻고 결과를 알림창으로 알린다. `tests/test_db_export_mysql.py`가 실제 MySQL에서 내보내기 → 테이블 삭제 → 파일 실행 → 내용·AUTO_INCREMENT 일치를 확인한다.

### Windows 실행 파일 (Jira S15P31B106-77)

`packaging/setron.spec`(PyInstaller)이 `dist/SETRON/` 폴더 형태의 실행 파일을 만든다. 시작 처리는 `setron.__main__.run()` 하나로, 실행 파일일 때만 `infrastructure/env_file.py`의 `load_env_file()`로 실행 파일 옆 `setron.env`를 읽어 아직 없는 환경변수를 채운다. 개발 실행(`uv run --env-file`)은 그대로다. 빌드·설치·설정 방법은 `docs/windows-package.md`에 있다.

## B 읽기 변환 공통 기반

`ui/read_converters.py`는 이미 조회된 `RecordView`, `ReleaseDetail`, `StoredSnapshot`만 변환한다. 서비스·DB·Qt 위젯을 실행하지 않는다. `read_release`는 적용 모델이 없는 `ReleaseRead`를 반환하고 `read_item`, `read_item_links`, `read_pm_versions`는 확정된 현재값·명시적 연결만 옮긴다. 순서·출시일·번호의 명시적 None은 보존하고 필드 누락, 잘못된 ID/상태, 중복 연결은 검증 오류로 구분한다.

`read_equipment_record(s)`는 `StoredSnapshot`을 집계·위험·우선순위·예측의 입력인 `EquipmentRecord`로 바꾼다. PM·SW는 `read_pm_versions`로 옮기고, HPA 여부는 호출하는 쪽이 넘긴 `analysis_enabled` = false 모델 ID로만 정한다. 대표 모델과 PM 모델이 모두 제외 모델이면 HPA이고, 일부만 제외 모델이면 검증 오류다. 여러 기준일을 함께 받을 수 있으며 같은 설비·같은 기준일 관측이 중복되면 오류로 처리한다.

`ReleaseRead`는 완성된 `VersionCatalog`가 아니다. 관리 모델을 실제 적용 모델로 복사하지 않으며 적용 범위·분석 제외 여부·전체 목록·관측 출처 계약은 공통 공급자에서 별도로 해결한다. `SourceReference`에는 셀 위치만 옮기고 원본 값/입력 일시는 원본 `RecordView`에 남긴다. 문서명·개정번호를 생성하지 않는다. PM 없는 SW에 대표 모델이 없거나 PM 위치/같은 계열의 대표 SW 연결이 중복되거나 일부 SW의 PM 연결이 없으면 임의로 합치지 않고 오류로 처리한다. 앱의 기본 로더 및 B-2 편집 필드·저장 연결은 변경하지 않는다.

## B 비동기 읽기 실행 경계

`ui/read_worker.py`의 `BackgroundRead(loader)`로 명시한 공급자만 QThreadPool에서 실행한다. 기존 메모리 공급자 호출 방식은 유지한다. 공급자는 Qt 위젯에 접근하지 않고 독립된 읽기 자료를 반환해야 하며, 같은 공급자가 버전/Item 화면에서 동시에 실행될 수 있다. 실제 DB 세션/연결의 수명은 후속 공급자 구현에서 정할 사항으로 현재 연결하지 않는다.

`ReadRequest`는 중복 요청을 막고 완료 신호를 UI 스레드에서 처리한다. 페이지가 숨겨지면 진행 중 응답을 무효화하고 재조회 버튼을 활성화한다. 재진입 후 사용자가 다시 불러오기를 선택한다. 취소는 실행 중 스레드 강제 종료가 아니며 공급자 자체의 제한 시간/중단 처리는 별도 계약이다. 삭제된 화면에는 Qt 연결 해제로 결과가 전달되지 않는다.

VersionsPage/ItemsPage의 기존 목록·실패·재시도 및 Item 상세 선택 무효화를 재사용한다. 조회 시작 시 이전 자료·상세를 비우며 정상 0건, 로딩, 실패, 이탈 취소를 구분한다. `create_window(version_loader=BackgroundRead(...))`의 기본 Item 공급자도 워커 경계를 이어받으며 합성 설비와 임의 연결하지 않는다. 기본 앱은 기존 합성 메모리 자료를 사용한다.

## B 자료 출처·조회 상태 안내

릴리즈 상단은 자료 종류를 고정하지 않고 본문의 출처·조회 상태를 안내한다. `source_notice.py`는 전달된 설명을 일반 텍스트로 표시하며 빈 설명은 미제공으로 표시한다. 설명 문자열로 DB 연결 성공/실패를 추정하지 않는다. 기본 합성 공급자의 예시·DB 미연결 설명은 유지한다.

VersionCatalog.source_label 및 ItemManagerData.observation_label의 기본값은 출처 설명 미제공이다. Item 관측 상태는 observations_connected와 base_date/records로 따로 표시해 정상 0건과 미연결을 구분한다. 로딩·취소·실패의 건수는 미확인으로 표시하고 이전 출처를 남기지 않는다. 정상 빈 Item 목록만으로 미연결이나 미포함을 확정하지 않는다. 실제 DB/공통 공급자 연결, 모델 매핑, 문서명·개정번호 생성은 추가하지 않는다.
