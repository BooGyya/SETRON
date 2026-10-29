# S15P31B106

setron은 설비 소프트웨어 버전 및 Release Item을 관리하는 Python 기반 Windows 데스크톱 애플리케이션입니다. PySide6로 화면을 구성할 예정이며 SQLAlchemy와 PyMySQL로 각 PC의 MySQL에 연결합니다. 현재 Excel 읽기는 xlwings를 사용하며, pandas와 openpyxl은 데이터 처리와 XLSX 출력에 사용할 의존성입니다.

현재는 열린 Excel 읽기, 버전 비교·집계·Item·위험 판정, 예제 응답을 사용하는 AI 호출 흐름, DB 연결과 공통 저장 서비스가 구현되어 있습니다. 화면, Excel에서 읽은 자료의 저장 흐름과 실제 회사 AI API 연동은 후속 작업입니다.

## Excel 가져오기 현황

`src/setron/infrastructure/excel_reader.py`는 사용자가 실행해 둔 Excel에 연결해 열린 통합문서·시트 목록과 선택한 시트의 사용 영역을 읽습니다. 원본 셀 값, 셀 주소와 병합 범위를 전달하며, Excel 미실행과 연결 실패를 구분해 알립니다.

실제 Excel 연동에는 Windows용 데스크톱 Excel이 필요합니다. 파일 선택창에서 XLSX 파일을 직접 읽는 방식은 아직 구현되어 있지 않습니다. 현재 모듈은 파일을 서버에 업로드하거나 앱으로 복사하지 않고, 실행 중인 Excel에서 값을 읽습니다.

## 개발 환경 준비

Windows와 Python 3.13.x를 기준으로 개발합니다. Python 버전은 `.python-version`과 `pyproject.toml`에 지정하고, 패키지는 [uv](https://docs.astral.sh/uv/getting-started/installation/)로 관리합니다.

uv를 설치한 뒤 저장소 루트에서 PowerShell로 다음 명령을 실행합니다.

```powershell
uv sync --locked
```

`uv.lock`에 기록된 버전으로 `.venv`를 만들고 프로젝트·실행 의존성·개발용 pytest를 설치합니다. 프로젝트는 편집 가능 모드로 설치되므로 `src/`의 코드 수정이 바로 반영됩니다. 명령을 실행할 때 가상환경을 별도로 활성화할 필요는 없습니다.

의존성을 추가할 때는 `uv add <패키지>`, 개발용 의존성은 `uv add --dev <패키지>`를 사용하고 `pyproject.toml`과 `uv.lock`을 함께 커밋합니다.

## Docker로 개발·테스트 DB 실행

개발 PC의 MySQL 5.6.19는 Docker Compose로 실행합니다. Windows에서 Docker Desktop의 Linux 엔진을 시작한 뒤 저장소 루트의 PowerShell에서 아래 명령을 사용합니다. 실제 사용 PC의 앱은 Windows에 설치된 MySQL에 접속하며, 이 Compose는 개발·테스트용입니다.

`mysql:5.6.19` 공식 이미지 태그가 없어 Oracle 보관소의 5.6.19 배포 파일을 받아 전용 이미지를 빌드합니다. Dockerfile은 배포 파일의 체크섬과 서버 버전을 확인합니다. 최초 빌드에는 인터넷 연결과 약 306MB의 다운로드가 필요합니다.

| 용도 | 서비스 | Windows 접속 주소 | DB / 일반 계정 | 데이터 볼륨 |
| --- | --- | --- | --- | --- |
| 개발 | `mysql-dev` | `127.0.0.1:13306` | `setron_dev` / `setron_app` | `mysql5619_dev_data` |
| 테스트 | `mysql-test` | `127.0.0.1:13307` | `setron_test` / `setron_test` | `mysql5619_test_data` |

컨테이너·계정·포트·볼륨을 용도별로 분리합니다. 포트는 이 PC의 `127.0.0.1`에만 공개합니다. 서버는 InnoDB, utf8mb4와 Windows의 기본 테이블명 처리에 맞춘 `lower_case_table_names=1`을 사용합니다.

개발 DB는 앱을 개발하면서 입력한 데이터를 보관합니다. 테스트 DB는 자동 검증에 사용하며 테스트가 테이블을 만들고 지우거나 오류를 일으키고 연결을 끊을 수 있습니다. 두 DB를 분리해 이런 검증이 개발 데이터와 작업 중인 연결에 영향을 주지 않도록 합니다. 테스트 DB는 검증할 때만 실행하면 됩니다.

### DB 연동 개발 기준

개발과 자동화 테스트에서는 Docker의 MySQL 5.6.19를 사용하고, 실제 사용 PC에서는 Windows에 설치된 MySQL 5.6.19에 접속하도록 개발합니다. Python 앱과 Excel은 두 환경 모두 Windows에서 실행합니다.

앱의 DB 접속 코드는 공통으로 사용하고, 호스트·포트·DB 이름·계정·비밀번호는 `SETRON_DB_*` 환경변수에서 읽습니다. 개발 DB는 `127.0.0.1:13306`, Windows DB는 해당 서버 주소와 설정된 포트(기본 `3306`)를 지정합니다. Windows DB가 앱과 같은 PC에 있으면 주소는 `127.0.0.1`입니다. 접속 설정을 바꾸면 같은 코드로 대상 DB를 전환할 수 있으며, 앱의 DB 연동은 Docker 명령이나 컨테이너 내부 경로에 의존하지 않습니다.

SQL과 스키마는 MySQL 5.6.19에서 지원하는 기능을 기준으로 작성합니다. Windows 서버에도 같은 업무 스키마를 적용하고 InnoDB, utf8mb4와 `lower_case_table_names=1`, 앱 계정의 권한을 확인해야 합니다. 접속 설정을 바꾸는 것만으로 스키마나 데이터가 이전되지는 않습니다.

실사용 접속 설정은 개발·테스트용 `.env`와 구분해 보관하고 실행 시 앱의 환경변수로 불러옵니다. 실사용 전에는 Windows에 설치된 MySQL에서도 조회·저장, 저장 실패 시 전체 취소와 삭제 전 위험 근거 보존을 확인합니다.

### 최초 준비와 실행

```powershell
# 기존 .env가 없을 때만 예시를 복사합니다.
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

`.env`를 열어 개발·테스트 일반 계정과 root 계정의 비밀번호 네 개를 서로 다른 값으로 바꿉니다. `$` 등이 문자 그대로 전달되도록 작은따옴표를 유지합니다. 접속 포트·DB 이름·계정은 예시 파일에서 변경할 수 있습니다. MySQL 5.6의 계정명은 16자 이내로 지정하며, 초기화 스크립트는 DB·계정 이름에 영문·숫자·밑줄을 허용합니다.

```powershell
# 설정 구문 확인. 비밀번호가 출력되지 않습니다.
docker compose --profile test config --quiet

# 개발·테스트가 공유하는 5.6.19 이미지를 한 번 빌드합니다.
docker compose build mysql-dev

# 개발 DB 실행
docker compose up -d --wait mysql-dev

# 테스트 DB 실행. 기본 실행에서는 제외되는 test 프로필입니다.
docker compose --profile test up -d --wait mysql-test
```

`--wait`는 DB가 쿼리를 받을 수 있을 때까지 기다립니다. 처음 실행하면 빈 DB와 일반 계정을 만들며, 업무 테이블은 아래 공통 스키마로 생성합니다. 초기화된 볼륨이 있으면 `.env`의 DB 이름·계정·비밀번호를 바꿔도 기존 DB와 계정에 자동 반영되지 않으므로 DB에서 별도로 변경해야 합니다.

### 공통 스키마

개발·테스트·Windows MySQL의 최초 설치에는 프로젝트의 [database/schema_mysql5619.sql](database/schema_mysql5619.sql)을 공통으로 사용합니다. 스키마 변경은 이 파일에 반영합니다. 저장소에 포함돼 있으므로 개인 PC의 외부 문서 폴더나 별도 스키마 경로 설정이 필요하지 않습니다.

HeidiSQL에서 **빈 대상 DB**를 선택하고 UTF-8로 SQL 파일 전체를 실행합니다. 13개 테이블과 기본 설정을 만들며, 기존 테이블에 덧씌우는 업그레이드 스크립트는 아닙니다. 스키마 생성에 필요한 권한과 앱 저장 서비스에서 검사해야 할 업무 규칙은 SQL 파일의 주석에 정리합니다.

### 상태 확인과 접속

```powershell
# 컨테이너 상태와 공개 포트 확인
docker compose --profile test ps

# 이미지에 포함된 정확한 서버 버전 확인
docker compose exec mysql-dev mysqld --version

# 최근 서버 로그 확인
docker compose logs --tail=100 mysql-dev

# 개발 DB에 접속. .env에 적은 일반 계정 비밀번호를 입력합니다.
docker compose exec mysql-dev mysql --default-character-set=utf8mb4 --user=setron_app --password setron_dev

# 테스트 DB의 서버 버전과 DB 이름 확인
docker compose --profile test exec mysql-test mysql --default-character-set=utf8mb4 --user=setron_test --password setron_test --execute="SELECT VERSION(), DATABASE();"
```

접속 명령의 DB·계정은 예시 값입니다. `.env`에서 바꿨다면 같은 값으로 맞춥니다. HeidiSQL로 확인할 때도 위 표의 Windows 주소와 일반 계정을 사용합니다. root 계정은 컨테이너 내부 관리와 상태 확인에 사용합니다.

### 중지·재시작·제거

```powershell
# 두 DB 중지. 데이터 볼륨은 유지합니다.
docker compose --profile test stop

# 중지된 개발 DB 다시 시작
docker compose up -d --wait mysql-dev

# 컨테이너·네트워크 제거. 데이터 볼륨은 유지합니다.
docker compose --profile test down
```

Dockerfile이나 초기화 스크립트를 수정했다면 `docker compose build mysql-dev`로 이미지를 다시 빌드하고 필요한 서비스를 `up -d --wait`로 재생성합니다.

## DB 연결과 저장 처리 (BE-001)

`src/setron/infrastructure/database.py`는 환경변수로 MySQL 접속 설정을 읽고 설정 오류, 접속 실패와 연결 후 저장 실패를 구분합니다. 접속마다 `STRICT_TRANS_TABLES`를 추가해 MySQL 5.6.19에서 길이를 넘는 값이 조용히 잘려 저장되지 않도록 합니다.

### 접속 설정과 Windows MySQL

`SETRON_DB_HOST`, `SETRON_DB_PORT`, `SETRON_DB_NAME`, `SETRON_DB_USER`, `SETRON_DB_PASSWORD`가 필수입니다. `SETRON_DB_CONNECT_TIMEOUT`은 연결 제한 시간(초)이며 생략하면 5초입니다. 테스트 DB에는 같은 항목을 `SETRON_TEST_DB_*`로 지정합니다. Compose의 `ROOT_PASSWORD` 설정은 DB 초기화와 상태 확인용이며 앱에서는 사용하지 않습니다.

실제 환경 파일은 Git에서 제외됩니다. 코드가 파일을 자동으로 읽지는 않으므로 실행할 때 uv의 `--env-file` 옵션으로 불러오거나 환경변수를 직접 설정합니다. 개발 DB에 접속하는지 다음 명령으로 확인할 수 있습니다.

```powershell
uv run --locked --env-file .env python -m setron.infrastructure.database
```

실제 사용 PC에서는 `.env.windows.example`을 `.env.windows`로 복사하고 Windows MySQL 5.6.19의 DB 이름·일반 계정·비밀번호를 입력합니다. 기본 주소는 `127.0.0.1:3306`이며 해당 PC의 설정에 맞춥니다. DB와 계정은 미리 준비하고 빈 DB에 공통 스키마를 적용합니다.

```powershell
uv run --locked --env-file .env.windows python -m setron.infrastructure.database
```

성공하면 확인 메시지와 종료 코드 0, 설정·접속 오류가 있으면 안내 메시지와 종료 코드 1을 반환합니다. Windows 설정에는 Compose용 root 비밀번호와 테스트 DB 설정이 필요하지 않습니다. Windows에 직접 설치된 MySQL의 실제 접속·저장 검증은 해당 환경이 준비된 뒤 진행합니다.

### 업무 단위 저장

업무 서비스는 `with database.transaction() as session:`으로 하나의 저장 요청 전체를 감싸고 모든 Repository에 같은 세션을 전달합니다. 정상 종료하면 한 번 commit하고, 작업 중 오류가 밖으로 전달되면 전체 변경을 rollback합니다. SQLAlchemy 저장 오류는 `DatabaseSaveError`, 업무 실행 전 접속 실패는 `DatabaseConnectionError`로 알리며 업무 검증 오류는 원래 예외를 유지합니다. 각 작업은 새 세션을 사용하고, 앱 종료 시 `database.dispose()`로 연결 풀을 정리합니다.

Repository와 트랜잭션 블록 안에서는 `commit()`, `rollback()`, `close()`를 직접 호출하지 않습니다. 전체 취소를 보장하려면 업무 테이블은 InnoDB를 사용해야 합니다. 암묵적으로 commit하는 테이블 생성·변경 등의 DDL은 저장 업무 밖에서 수행합니다.

최종 commit 중 연결이 끊겨 결과를 확인할 수 없으면 `DatabaseCommitUnknownError`로 구분합니다. 이때는 전체 취소로 단정하거나 자동 재시도하지 않고, 연결 복구 후 저장된 내용을 먼저 확인합니다.

### 공통 저장 서비스

`features/storage/`는 공통 스키마의 조회·저장, 입력 검증과 삭제 전 위험 근거 보존을 처리합니다. MySQL 5.6.19에 맞춰 JSON을 LONGTEXT로 저장하고 앱에서 JSON 구조·상태값·필수값을 검사합니다. 중첩 JSON 객체의 키도 문자열이어야 합니다. 호기 코드·버전 원문·PM명·설정 키는 UTF-8 VARBINARY로 저장해 대소문자와 뒤쪽 공백을 보존하고 조회 시 문자열로 복원합니다.

```python
from setron.features.storage.service import StorageService
from setron.infrastructure.database import Database

database = Database.from_env()
try:
    storage = StorageService(database)  # 공통 스키마를 적용한 뒤 생성합니다.
    with storage.transaction() as tx:
        equipment = tx.insert("equipment", {"equipment_code": "EQ-001"})
        tx.update("equipment", {"equipment_id": equipment["equipment_id"]}, {"equipment_code": "EQ-002"})
finally:
    database.dispose()
```

릴리즈 버전 삭제와 릴리즈 교체 내부의 Item 삭제는 대상과 위험 규칙을 잠근 뒤 기존 위험 근거에 원문 정보를 추가하고 `UNRESOLVED`로 바꿉니다. 근거 보존과 삭제는 다른 변경과 같은 트랜잭션에서 수행하므로 중간 실패 시 함께 취소됩니다. 정수 ID와 숫자로만 된 ID 문자열을 지원하며, DB에서 읽은 기본키로 참조를 비교합니다.

관리 데이터의 수정·삭제는 앱의 저장 서비스를 통해 수행합니다. HeidiSQL에서 직접 바꾸면 앱의 검증과 삭제 전 근거 보존을 거치지 않습니다. 스키마 주석에 정리한 계열 일치·원본 불변·모델 적용 범위 등 기능별 규칙은 후속 업무 서비스에서 구현해야 합니다.

## 실행

```powershell
uv run --locked python -m setron
```

실행 진입점은 `src/setron/__main__.py`입니다. 현재는 설명문만 있어 위 명령을 실행해도 화면이 뜨지 않고 종료됩니다. 화면 초기화 코드는 UI 구현 시 추가합니다.

## 테스트

```powershell
uv run --locked python -m pytest
```

DB 접속 설정을 불러오지 않으면 실제 MySQL 테스트는 건너뜁니다. Excel 읽기, 예제 AI 응답, 버전·집계·Item 판정과 DB 입력 검증 등의 테스트는 외부 서버 없이 실행합니다. DB 트랜잭션의 기본 동작은 SQLite에서도 확인하며, 이 결과는 실제 MySQL 검증과 구분합니다.

MySQL 통합 테스트는 `SETRON_TEST_DB_*` 설정이 있는 전용 DB에서 실행합니다. 스키마는 테스트 파일 위치를 기준으로 `database/schema_mysql5619.sql`에서 읽으므로 실행 위치에 영향을 받지 않고 별도 스키마 경로 환경변수가 필요하지 않습니다. 테스트가 만든 테이블은 종료 시 삭제합니다. 기존 관리 테이블이 있는 DB에서는 스키마 검증을 거부하므로 테스트 DB에 개발 데이터를 저장하지 않습니다.

```powershell
docker compose --profile test up -d --wait mysql-test
uv run --locked --env-file .env python -m pytest -q
```

`tests/test_database_mysql.py`와 `tests/test_schema_mysql.py`는 실제 MySQL 5.6.19에서 스키마 적용, 저장 성공·전체 취소, JSON·유니코드·버전 원문 보존과 삭제 전 위험 근거 보존을 확인합니다. 문자열 ID 삭제, 잘못된 ID·JSON 입력, TEXT 초과와 삭제 단계 FK 오류도 검증합니다. 같은 위험 규칙을 참조하는 버전의 동시 삭제는 두 작업을 겹쳐 실행해 근거 병합과 각 작업의 저장·취소 결과를 확인합니다.

실제 연결 중단 테스트는 같은 테스트 계정의 별도 연결에서 테스트가 확보한 연결만 종료합니다. 저장 도중에는 앞선 변경·삭제·위험 근거가 모두 취소되고 다음 저장이 가능한지 확인합니다. 최종 commit 단계에서는 COMMIT 전송 직전에 연결을 종료해 결과 불명 오류로 구분하고 자동 재시도하지 않는지 확인합니다. 성공한 commit의 응답만 유실되는 상황을 재현하는 테스트는 아닙니다.

접속 설정을 지정했는데 연결·권한·설정에 문제가 있으면 테스트 실패로 보고합니다. 테스트 종류별 범위, 기능별 체크리스트와 실제 Excel·회사 AI의 확인 방법은 [테스트 전략](docs/testing.md)을 따릅니다.

## 폴더 구조

```text
S15P31B106/
├── src/
│   └── setron/
│       ├── __init__.py
│       ├── __main__.py
│       ├── ui/
│       ├── features/
│       └── infrastructure/
│           ├── database.py
│           └── excel_reader.py
├── database/
│   └── schema_mysql5619.sql
├── tests/
│   ├── test_aggregation_service.py
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

`ui/`, `features/`, `infrastructure/`에는 각각 `__init__.py`를 둡니다. 기능별 폴더와 파일은 실제 구현이 필요할 때 추가합니다. Excel 읽기 테스트는 실제 Excel 대신 가짜 통합문서를 사용합니다.

## 문서

개발 작업은 작업별 브랜치에서 진행한 뒤 `develop`에 MR로 통합합니다. 릴리스할 변경이 준비되면 `develop → master` MR로 반영합니다. 자세한 절차는 Git 협업 규칙을 따릅니다.

- [폴더 아키텍처](docs/architecture.md): 모듈별 책임과 기능 추가 원칙
- [Git 협업 규칙](docs/git-conventions.md): 브랜치, 커밋, 리뷰 및 병합 규칙
- [테스트 전략](docs/testing.md): 테스트 종류별 범위, 기능별 체크리스트, 실행·결과 기록 방법
- [ERD 초안](docs/mermaid-diagram.png): 작성 중인 데이터 관계도. DB 스키마는 아직 확정되지 않았습니다.
