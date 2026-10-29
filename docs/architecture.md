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
│       │   └── __init__.py
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
│       │   ├── item/
│       │   │   ├── __init__.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   ├── risk/
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
│           └── excel_reader.py
├── database/
│   └── schema_mysql5619.sql
├── tests/
│   ├── test_aggregation_service.py
│   ├── test_aging_mysql.py
│   ├── test_aging_service.py
│   ├── test_ai_service.py
│   ├── test_database.py
│   ├── test_database_environment.py
│   ├── test_database_mysql.py
│   ├── test_excel_reader.py
│   ├── test_item_service.py
│   ├── test_risk_service.py
│   ├── test_schema_mysql.py
│   ├── test_storage_service.py
│   ├── test_storage_validation.py
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

각 Python 패키지에는 `__init__.py`를 둡니다. `__main__.py`는 실행 진입점이며, 구현 시 DB 연결 설정, 서비스와 화면의 연결, 애플리케이션 시작을 담당합니다. 현재는 설명문만 있어 실행해도 화면이 뜨지 않고 종료됩니다. 설치와 실행 방법은 [README](../README.md)를 참고합니다.

`tests/test_excel_reader.py`는 실제 Excel 대신 가짜 통합문서를 사용해 읽기 모듈의 순수 로직과 오류 처리를 검증합니다.

회사 AI API 정보(주소·모델·인증·호출 제한)를 받기 전까지는 `infrastructure/ai_client.py`의 `ExampleChatClient`가 미리 정한 정상·실패 응답을 돌려줍니다. `features/ai/`는 Excel 추출·질문 해석·분석 작업의 요청과 응답 형식을 정하고, 호출 실패와 형식 오류를 결과 상태로 바꿉니다. 실제 클라이언트는 같은 `ChatClient` 형식으로 추가하며, 결과의 `source`로 예제 응답과 실제 API 결과를 구분합니다. `tests/test_ai_service.py`가 이 흐름을 예제 응답으로 검증합니다.

`features/version/`은 같은 버전 계열 안에서 설비 버전을 목표 N과 비교해 'N 이상', 'N-k', '판단 불가'를 계산합니다. 순서는 릴리즈 자료에서 확인한 `version_order`만 사용하고 버전 문자열이나 접미사로 추정하지 않습니다. DB 없이 동작하는 순수 계산이며, Repository가 `release_version` 행을 `ReleaseVersion`으로 바꿔 전달합니다. 현황표 원문이 릴리즈 원문과 공백·대소문자·앞의 v·(H) 표식·점과 밑줄만 다르면 `FamilyReleases.candidates`가 후보로 보여주고, 자동으로 연결하지 않습니다. 사용자가 고른 릴리즈는 `equipment_version.release_version_id`로 저장해 비교에 씁니다. `tests/test_version_service.py`가 검증합니다.

`features/aggregation/`은 대시보드·히트맵·AI가 함께 쓰는 공통 집계입니다. 한 기준 날짜의 설비 현황을 받아 PM별로 `features/version/`의 비교를 거친 뒤, 설비·계열 상태와 실제 호기 수, 모델별 대수, 업그레이드 필요 수, N 이상 적용률을 계산합니다. 조회 범위 필터는 호출하는 쪽에서 적용하고, Repository는 `equipment_snapshot`·`equipment_pm`·`equipment_version` 행을 `EquipmentRecord`로 묶어 전달합니다. `tests/test_aggregation_service.py`가 검증합니다.

`features/item/`은 Item이 포함된 버전과 적용 설비를 판정합니다. 버전별로 저장된 Item 상태(`release_version_item`)만 사용하고, 행이 없는 버전은 '미확인'으로 둡니다. 공통 스키마 규칙 H에 따라 `version_order`만으로 이전 버전의 상태를 이어받지 않으며, 누적 포함과 제외 조건은 릴리즈를 저장할 때 버전마다 행으로 반영합니다. 모델 적용 범위로 '대상 외'를 구분하고, 범위를 모르면 '미확인'으로 두어 미적용으로 단정하지 않습니다. 릴리즈 순서는 `features/version/`, 설비 현황 형식은 `features/aggregation/`의 것을 씁니다. `tests/test_item_service.py`가 검증합니다.

`features/aging/`은 버전 출시일(`release_version.released_at`)부터 조회 기준 날짜까지의 경과 기간으로 노후도(안전·주의·위험)를 계산합니다. 경계는 달력 기준이며, 출시일 누락·조회 날짜보다 늦은 출시일·잘못된 기간 기준을 서로 다른 상태로 구분합니다. 혼합 설비는 계열별 기간을 모두 남기고 가장 오래된 버전을 대표로 씁니다. 기간 기준은 `app_setting`의 `version_age_thresholds`에 저장하며, Repository가 `features/storage/`의 트랜잭션으로 읽고 저장하고 음수·역전 등 잘못된 기준은 저장하지 않습니다. 노후도는 위험 판정과 따로 표시합니다. `tests/test_aging_service.py`와 MySQL 통합 테스트 `tests/test_aging_mysql.py`가 검증합니다.

`features/risk/`는 등록된 위험 지정(`risk_rule`)으로 설비와 버전의 위험 여부를 판정합니다. 환경·안전 필수 Item 미적용, 결함 Item의 위험 범위([시작, 해제) 버전) 포함, 직접 지정한 위험 버전 중 하나라도 확인되면 위험이고, 자료가 부족하면 '미확인'으로 둡니다. Item 포함 여부는 `features/item/`의 판정을 그대로 쓰며, 위험은 N 대비 격차·노후도와 따로 판단합니다. `tests/test_risk_service.py`가 검증합니다.

데이터 관계를 정리한 [ERD](mermaid-diagram.png)는 작성 중인 초안이며, 확정된 DB 스키마가 아닙니다.

## 폴더별 책임

| 경로 | 책임 |
| --- | --- |
| `src/setron/ui/` | PySide6 화면, 사용자 입력, 화면용 테이블 모델, 백그라운드 작업 실행과 결과 표시 |
| `src/setron/features/` | 설비·버전·Item·Excel·통계 업무 로직 |
| `src/setron/features/storage/` | 공통 Repository, 저장값 변환·입력 검증·삭제 전 위험 근거 보존 |
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

릴리즈 버전 삭제와 릴리즈 교체 내부의 Item 삭제는 대상과 위험 규칙을 잠근 뒤 기존 근거에 삭제 대상의 원문을 추가하고 `UNRESOLVED`로 바꿉니다. 근거 보존과 삭제는 함께 저장되거나 취소됩니다. 앱에서 수정·삭제하는 경로를 지원하며, 계열 일치·원본 불변·모델 적용 범위 등 기능별 추가 규칙은 후속 업무 서비스에서 구현합니다.

MySQL 회귀 테스트는 테스트 파일 위치를 기준으로 프로젝트 내 스키마를 찾습니다. 별도 스키마 경로 설정 없이 전용 테스트 DB에서 생성·검증·정리하며, 동시 삭제와 저장 도중·COMMIT 전송 직전의 실제 연결 중단도 검증합니다. Windows MySQL은 `.env.windows.example`을 기반으로 별도 접속 설정을 준비하고 실제 서버에서 추가 검증합니다.

## 기능을 추가할 때

업무 기능은 필요해지는 시점에 `features/` 아래에 분리합니다. 예를 들어 설비 기능은 `features/equipment/`에 추가합니다.

- `service.py`: 업무 흐름과 규칙
- `repository.py`: 해당 기능의 데이터 조회. 관리 데이터 변경은 공통 저장 서비스를 사용하고 DB 엔진·세션 관리는 `infrastructure/`에 유지
- `schemas.py`: 화면과 서비스가 주고받는 데이터 구조

파일은 실제로 맡을 책임이 생길 때 만듭니다. 초기부터 모든 기능에 동일한 파일을 만들 필요는 없습니다.

화면은 `ui/`, 업무 처리는 `features/`에 두고 화면과 서비스 사이의 입력·반환 형식은 함께 합의합니다. 공통 코드도 먼저 해당 기능 안에서 관리하고, 여러 곳에서 필요해질 때 별도 패키지로 분리합니다. AI 연동 등 추가 기능을 위한 폴더도 실제 구현이 필요해질 때 만듭니다.

## 문서 갱신

폴더를 추가하거나 이동하고, 모듈의 책임 또는 호출 관계를 바꾸면 같은 변경에 이 문서도 갱신합니다. 프로젝트 전체 구조가 달라지면 README의 구조 설명도 함께 수정합니다.
