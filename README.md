# S15P31B106

setron은 설비 소프트웨어 버전 및 Release Item을 관리하는 Python 기반 Windows 데스크톱 애플리케이션입니다. PySide6로 화면을 구성하며 SQLAlchemy와 PyMySQL로 각 PC의 MySQL에 연결합니다. 현재 Excel 읽기는 xlwings를 사용하며, pandas와 openpyxl은 데이터 처리와 XLSX 출력에 사용할 의존성입니다.

현재는 열린 Excel 읽기, 버전 비교·집계·Item·위험 판정, 예제 응답을 사용하는 AI 호출 흐름, DB 연결과 공통 저장 서비스, 기준일별 설비 저장·조회, 릴리즈·Item 등록·수정·원본 비교가 구현되어 있습니다. 공통 UI·조회 조건과 합성 예시 자료를 사용하는 대시보드·버전 히트맵·설비 목록/상세를 실행할 수 있으며, 업무 화면과 DB의 연결, Excel에서 읽은 자료의 저장 흐름과 실제 회사 AI API 연동은 후속 작업입니다.

## Excel 가져오기 현황

`src/setron/infrastructure/excel_reader.py`는 사용자가 실행해 둔 Excel에 연결해 열린 통합문서·시트 목록과 선택한 시트의 사용 영역을 읽습니다. 원본 셀 값, 셀 주소와 병합 범위를 전달하며, Excel 미실행과 연결 실패를 구분해 알립니다.

실제 Excel 연동에는 Windows용 데스크톱 Excel이 필요합니다. 파일 선택창에서 XLSX 파일을 직접 읽는 방식은 아직 구현되어 있지 않습니다. 현재 모듈은 파일을 서버에 업로드하거나 앱으로 복사하지 않고, 실행 중인 Excel에서 값을 읽습니다.

## 개발 환경 준비

Windows와 Python 3.13.x를 기준으로 개발합니다. Python 버전은 `.python-version`과 `pyproject.toml`에 지정하고, 패키지는 [uv](https://docs.astral.sh/uv/getting-started/installation/)로 관리합니다.

처음 저장소를 받은 팀원은 Git, Python 3.13, uv와 Docker Desktop을 설치한 뒤 아래 [팀원 최초 설정 순서](#팀원-최초-설정-순서)를 따릅니다.

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

### 팀원 최초 설정 순서

Windows PowerShell에서 저장소 최상위 폴더를 기준으로 실행합니다. Docker Desktop은 Linux 엔진이 실행 중이어야 합니다. 아래 접속 명령은 위 표의 기본 DB·계정을 기준으로 하므로 `.env`에서 바꿨다면 명령도 같은 값으로 맞춥니다.

1. **최신 develop과 Python 의존성 받기**

   작업 중인 변경이 있다면 먼저 커밋하거나 보관한 뒤 진행합니다.

   ```powershell
   git fetch origin
   git switch develop
   git pull --ff-only origin develop
   uv sync --locked
   ```

2. **.env 만들기**

   기존 파일이 없을 때만 예시를 복사합니다.

   ```powershell
   if (-not (Test-Path .env)) { Copy-Item .env.example .env }
   notepad .env
   ```

   복사한 `.env`에서 비밀번호 네 개를 수정합니다. 다음은 로컬 개발용 예시이며, 나머지 접속 항목은 예시 파일의 기본값을 사용합니다.

   ```dotenv
   SETRON_DB_PASSWORD='dev_demo1234'
   SETRON_DB_ROOT_PASSWORD='devroot_demo1234'
   SETRON_TEST_DB_PASSWORD='test_demo1234'
   SETRON_TEST_DB_ROOT_PASSWORD='testroot_demo1234'
   ```

   앱과 HeidiSQL은 일반 계정으로 접속하고 root 계정은 컨테이너 내부 관리에 사용합니다. 실제 `.env`는 Git에서 제외되므로 팀원마다 직접 만들고, 공유할 설정 항목은 `.env.example`에 반영합니다. 비밀번호의 `$` 등이 문자 그대로 전달되도록 작은따옴표를 유지합니다. MySQL 5.6의 계정명은 16자 이내이며 초기화 스크립트는 DB·계정 이름에 영문·숫자·밑줄을 허용합니다.

   초기화된 볼륨이 있으면 `.env`의 비밀번호·계정·DB 이름을 바꿔도 기존 DB에 자동 반영되지 않습니다. 이 경우 DB의 계정·DB 설정도 별도로 변경해야 합니다.

3. **Docker 이미지 빌드와 DB 실행**

   ```powershell
   # 설정 구문 확인. 비밀번호는 출력되지 않습니다.
   docker compose --profile test config --quiet

   # 개발·테스트가 공유하는 이미지를 한 번 빌드합니다.
   docker compose build mysql-dev

   docker compose up -d --wait mysql-dev
   docker compose --profile test up -d --wait mysql-test
   docker compose --profile test ps
   ```

   `--wait`는 DB가 쿼리를 받을 수 있을 때까지 기다립니다. 두 서비스가 `healthy`인지 확인합니다. 최초 실행에서는 빈 DB와 계정만 생성하며, 업무 테이블은 다음 단계에서 만듭니다.

4. **빈 개발 DB에 스키마 최초 적용**

   **개발 DB에 한 번만 적용합니다. 테스트 DB에는 직접 적용하지 않습니다.** 먼저 스키마를 개발 컨테이너로 복사하고 MySQL에 접속합니다.

   ```powershell
   docker compose cp ./database/schema_mysql5619.sql mysql-dev:/tmp/schema_mysql5619.sql
   docker compose exec mysql-dev mysql --default-character-set=utf8mb4 --user=setron_app --password setron_dev
   ```

   비밀번호 입력 요청이 나오면 `.env`의 `SETRON_DB_PASSWORD` 값을 입력합니다. 접속 후 `mysql>` 프롬프트에서 다음을 실행합니다.

   ```sql
   SOURCE /tmp/schema_mysql5619.sql;
   SHOW TABLES;
   SELECT VERSION(), DATABASE();
   exit;
   ```

   테이블 13개, 서버 버전 `5.6.19`, DB 이름 `setron_dev`를 확인합니다. 이미 스키마를 적용한 개발 DB에서는 이 단계를 생략합니다.

5. **앱 연결 확인과 전체 테스트**

   PowerShell로 돌아와 다음 명령을 실행합니다.

   ```powershell
   uv run --locked --env-file .env python -m setron.infrastructure.database
   uv run --locked --env-file .env python -m pytest -q
   ```

   첫 명령에서 DB 연결 확인 메시지가 나오고, 전체 테스트가 통과하는지 확인합니다. 테스트는 `setron_test`에 스키마를 자동 적용하고 검증 후 생성한 테이블을 삭제합니다. 테스트 DB는 관리 테이블이 없는 상태로 두며, 개발 데이터를 저장하지 않습니다. 스키마는 프로젝트의 `database/schema_mysql5619.sql`을 자동으로 찾으므로 별도 스키마 경로 환경변수가 필요하지 않습니다.

6. **작업 종료와 다음 실행**

   ```powershell
   # 두 DB 중지. 데이터는 유지합니다.
   docker compose --profile test stop

   # 다음 개발 시작
   docker compose up -d --wait mysql-dev

   # 테스트할 때 추가 실행
   docker compose --profile test up -d --wait mysql-test
   ```

   다음부터는 이미지 빌드와 스키마 최초 적용을 생략하고 필요한 DB를 실행하면 됩니다. Dockerfile이나 초기화 스크립트를 수정했다면 아래 재빌드 안내를 따릅니다.

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

관리 데이터의 수정·삭제는 앱의 저장 서비스를 통해 수행합니다. HeidiSQL에서 직접 바꾸면 앱의 검증과 삭제 전 근거 보존을 거치지 않습니다. 설비 현황과 릴리즈·Item은 아래 업무 서비스의 검증을 사용합니다. 위험 범위·재가져오기 후 재계산 등 나머지 규칙은 각 후속 업무 서비스에서 구현합니다.

## 설비·PM·버전 계열 저장 (BE-002)

`features/equipment/`의 `EquipmentService`는 검토가 끝난 현황을 기존 공통 스키마에 저장하고 정확한 기준일로 다시 조회합니다. UI·AI 추출과는 독립적으로 사용할 수 있습니다.

- `save_snapshots(tuple[SnapshotInput, ...])`: 같은 저장 요청 전체를 하나의 트랜잭션으로 처리합니다. 호기 코드는 전체 사업장에서 재사용하고, 기준일별 소속·원본 모델명·형상·개조 정보와 PM 구성을 따로 보존합니다.
- `get_snapshot(equipment_code, base_date)`: 해당 날짜의 `StoredSnapshot`을 반환합니다. 기록이 없으면 `None`이며 이전 날짜나 현재값으로 채우지 않습니다. 날짜 간 동일 PM의 식별은 `equipment_id + pm_name`입니다.
- `resolve_model(model_name, category)`: 등록 모델 중 확인된 이름을 찾습니다. 확정 별칭 `M5 → M-O5`만 허용하며 `M-O5 Prime`과 KCTC/Oxide의 O2를 임의로 합치지 않습니다. 대응이 없거나 여러 개면 확인을 요청하는 검증 오류를 반환합니다.

`SnapshotInput`의 `software`는 `SoftwareInput` 목록이고, 각 SW의 `pms`에는 고정 위치와 확인된 모델 ID를 담은 `PmInput`을 지정합니다. 일반 설비는 PM 목록을 비우고 현황의 `group_id`를 지정합니다. 같은 현황·계열·버전 원문의 정확한 중복은 SW 한 행을 공유하며 PM 연결과 모든 원본 위치를 유지합니다. 같은 PM의 상충 값은 저장을 막고, 같은 계열의 다른 PM에 서로 다른 버전이 있는 경우에는 모두 저장한 뒤 `pm_mismatch_families`로 알려줍니다.

모델·계열·릴리즈 ID는 원본 검토로 확인한 기존 등록값을 전달합니다. 릴리즈의 계열 일치와 모델/계열의 최상위 설비군을 검사하지만, 같은 설비군이라는 이유로 모델별 릴리즈 적용 가능성을 확정하지 않습니다. 미등록 SW는 원문과 계열을 보존하고 `release_version_id=None`으로 저장합니다. C3 형상은 원문 속성이며 계열을 자동 생성하거나 나누지 않습니다. 구UI/통합UI에는 각각 확인된 다른 계열 ID를 전달합니다. Memory/Foundry는 `None`을 허용하며 표시값은 `미확인`입니다.

입력 작업은 먼저 공통 저장 서비스에 등록합니다. `EXCEL`이면 현황·SW·PM의 출처에 지정한 시트·셀이 `import_batch.raw_data`에 실제로 있어야 합니다. 출처는 필드별 `{sheet, cells}`이며 여러 시트에서 중복 추출한 필드는 이 객체들의 목록으로 보존합니다. 저장 중 입력 자료와 보관 원본은 바꾸지 않습니다. `MANUAL` 입력은 출처를 생략할 수 있습니다.

이미 저장된 같은 호기·날짜의 현황은 덮어쓰지 않고 재가져오기 검토가 필요하다는 오류를 반환합니다. 재가져오기 충돌 선택·수정과 UI 연결은 후속 BE-009/025에서 구현합니다.

```powershell
uv run --locked python -m pytest tests/test_equipment_validation.py -q
# 기존 관리 테이블이 없는 전용 MySQL 5.6.19 테스트 DB 사용
uv run --locked --env-file .env python -m pytest tests/test_equipment_mysql.py -q
```

## 릴리즈·Item 등록·수정·조회 (BE-004)

`features/release/ReleaseService`는 기존 `release_version`, `item`, `release_version_item`, `import_batch`를 사용합니다. 새 테이블이나 의존성을 추가하지 않습니다.

| 메서드 | 처리 내용 |
| --- | --- |
| `register_release(value, items=())` | 릴리즈와 여러 새 Item을 한 번에 등록. 새 Item은 해당 버전에 `APPLIED`, 모델 범위는 `UNKNOWN`으로 연결 |
| `register_item(release_id, value)` | 기존 릴리즈에 새 Item 추가 |
| `update_release(release_id, changes)` | 허용된 릴리즈 관리값 수정 및 실제 수정 항목 기록 |
| `update_item(item_id, changes)` | Item 번호·내용 수정. 여러 릴리즈가 같은 Item을 공유하면 관리값도 함께 반영 |
| `save_item_links(release_id, links)` | 기존 Item의 포함 상태와 확인된 모델 범위 등록·수정. 요청에 없는 연결은 유지 |
| `get_release(release_id)`, `get_item(item_id)` | 현재 관리값과 보관 원본 셀 값·위치를 분리해 조회 |
| `list_groups()`, `list_families(group_id)`, `list_releases(family_id)` | 릴리즈가 저장된 관리 모델·계열 선택지와 계열별 릴리즈 목록 조회 |

등록 입력은 `ReleaseInput`, `ItemInput`, 연결 입력은 `ItemLinkInput`을 사용합니다. `batch_id=None`으로 직접 등록하면 이번 요청의 자료들이 하나의 새 `MANUAL` 입력 작업을 공유합니다. Excel 자료는 기존 입력 작업 ID와 필드별 `{sheet, cells}` 출처를 전달하며, 참조한 셀이 보관 원본에 있는지 검사합니다. 여러 시트의 출처는 객체 목록으로 보존합니다.

릴리즈는 `version_raw`, `version_suffix`, `released_at`, `version_order`, `item_mapping_status`를 수정할 수 있고, Item은 `item_no`, `content`를 수정할 수 있습니다. 기존 `batch_id`, `source_reference`, 계열·노트·기본키는 이 수정 API에서 변경하지 않습니다. `edited_fields`는 실제로 바뀐 관리 항목을 누적하며 수정 요청에서 직접 덮어쓸 수 없습니다. 원래 값으로 되돌려도 과거에 직접 수정한 사실은 유지합니다.

조회 결과의 `RecordView.current`는 현재 DB 관리값이고, `original_cells`는 필드명·시트·셀 주소·보관 원본값입니다. 원본을 확인할 자료가 없으면 빈 튜플과 `has_original=False`를 반환합니다. 수정 후에도 원본 입력 작업의 셀 값·위치·등록 시각을 유지합니다.

모델 선택지는 계열의 **관리 모델** 기준이며 릴리즈의 실제 적용 모델 전체를 뜻하지 않습니다. `LISTED` 범위는 검토로 확인한 모델 ID만 명시하고 DB 존재 여부·중복·설비군 일치를 검사합니다. 같은 설비군이라는 이유로 모델 적용 범위를 확정하거나 다른 버전으로 Item을 상속하지 않습니다. 일반 Item 삭제·숨김·비활성화는 제공하지 않습니다.

현재 범위는 백엔드 서비스입니다. B-2 화면의 편집·저장 연결은 후속 작업이며, 등록·수정 후 적용 여부·N 유효성·위험 결과의 재계산은 BE-010에서 연결합니다.

```powershell
uv run --locked python -m pytest tests/test_release_validation.py -q
uv run --locked --env-file .env python -m pytest tests/test_release_mysql.py -q
```

클래스·메서드 주석은 [주석 작성 기준](docs/architecture.md#클래스메서드-주석-작성-기준)을 따릅니다.

## 실행

```powershell
uv run --locked python -m setron
```

실행 진입점은 `src/setron/__main__.py`입니다. 현재는 **A 작업 단위 1~7과 B-1 버전 목록·상세**를 실행합니다. VER3 Light 기반 공통 조회 패널, 대시보드·히트맵·설비 목록/상세, 모델·계열별 목표 선택과 읽기 전용 릴리즈 조회를 확인할 수 있습니다. 조회 적용 시 같은 합성 관측과 적용한 목표 N으로 계산하며 실제 DB·Excel에는 연결하지 않습니다.

이미 설치된 가상환경으로 직접 실행할 때는 터미널에 맞는 명령을 사용합니다.

```bash
# Git Bash · 저장소 최상위 폴더에서 실행
./.venv/Scripts/python.exe -m setron
```

```powershell
# PowerShell
.\.venv\Scripts\python.exe -m setron
```

### 작업 단위 2·3·4·5·6·7 확인 범위

- 224px 사이드바, 원본 SEMES 로고·메뉴 아이콘·폰트, 공통 헤더·카드·버튼·배지를 제공합니다.
- 시작 시에는 아직 적용된 조건이 없어 지표를 ‘—’로 표시합니다. **조회 적용**을 누르면 합성 예시 대시보드·히트맵 결과를 함께 갱신합니다.
- 조회 적용 후 헤더의 **선택 범위 설비 목록**이 활성화됩니다. 나머지 미연결 메뉴와 ‘데이터 내보내기’·‘화면 설정’은 비활성화합니다. 도움말과 조회 패널의 ‘조회 적용’은 사용할 수 있습니다.
- 창이 좁아지면 헤더 액션을 다음 줄에 배치하고, 창 높이가 낮으면 사이드바도 스크롤합니다.
- 페이지 등록·이동 연결과 위젯 상태 보존을 제공합니다. 담당자는 `MainWindow.register_page(key, widget)`와 `navigate(key)`로 연결합니다. [공통 UI 구조](docs/architecture.md#공통-ui-작업-단위-2)를 참고합니다.

조회 조건은 다음 순서로 확인합니다.

1. Site·Fab·Line·설비군을 선택합니다. Site 변경 시 Fab·Line, Fab 변경 시 Line 선택을 전체로 초기화합니다.
2. **조회 적용**을 누르면 적용 조건과 요청 번호가 갱신됩니다. 선택만 바꿔서는 기존 적용 조건이 바뀌지 않습니다.
3. **전체 조건으로**는 선택 중인 조건만 초기화합니다. 전체 범위를 적용하려면 다시 조회 적용을 누릅니다.

Site·설비군은 VER3 선택지 미리보기입니다. Fab·Line의 실제 매핑은 미등록이므로 전체·미지정만 표시하며 P1·15L 같은 원본 구역을 임의로 변환하지 않습니다. KCTC의 Inline Ox·Inline O2·Cluster Ox·Cluster O2는 서로 다른 선택지입니다. 앱에서는 목표 N을 임시 편집할 수 있으며 개인 기본 선택지 설정은 아직 비활성화 상태입니다.

‘조건 적용됨’은 **조건을 확정했다는 뜻이지 DB 조회가 완료되었다는 뜻이 아닙니다.** 현재 대시보드 결과도 합성 예시이며 실제 회사 현황이 아닙니다. 적용 상태는 앱 실행 중에만 유지됩니다. [조회 조건 계약과 페이지 연결 방법](docs/architecture.md#공통-조회-조건-작업-단위-3)을 참고합니다.

### 대시보드 확인 순서 (작업 단위 4)

1. 조회 적용 후 물리 설비·목표 달성률·안전/결함 후보·업그레이드 필요 지표를 확인합니다.
2. **현황표 / 구성비 / 막대 / 적용 추이** 탭으로 같은 적용 결과를 확인합니다. 차트의 **수치표 보기**로 원본 숫자를 확인할 수 있습니다.
3. Site·설비군을 바꿔도 이전 결과는 유지됩니다. 다시 조회 적용을 눌러야 지표·표·그래프를 함께 갱신합니다.
4. 일반 셀/막대는 해당 범위의 전체 설비, 위험 후보 버튼은 후보 설비만 전달해 설비 목록으로 이동합니다. 전달 범위를 자동으로 넓히지 않습니다.
5. 차트 범례로 표시할 설비군을 선택할 수 있습니다. 범례는 그래프 표시만 바꾸며 조회 조건과 지표는 바꾸지 않습니다.

물리 설비 수에는 HPA를 포함하고 분석에서는 제외합니다. 적용률은 설비·계열당 1건으로 계산하며 판단 불가는 분모에서 제외하고 따로 표시합니다. 반면 구성비는 판단 불가를 포함한 전체 설비·계열 건수가 분모입니다. 두 비율의 분모가 달라 숫자가 다를 수 있으며 화면에 단위를 명시합니다.

추이는 기준 관측일에 선택한 설비 집합과 목표 N을 고정합니다. 과거 관측이 없는 설비를 이전 값이나 0%로 채우지 않으며 관측 부족 지점은 선을 끊습니다. 기준 관측일은 예시 자료의 마지막 날짜로 고정되어 있고 변경 UI는 아직 없습니다. 빈 결과·계산 불가·오류는 서로 구분합니다.

### 버전 히트맵 확인 순서 (작업 단위 5)

1. **조회 적용 → 버전 히트맵** 메뉴를 선택합니다. 반대 순서로 진입해도 조회 적용 후 결과가 표시됩니다. 대시보드와 같은 적용 조건·기준 관측일·설비 집합·목표를 사용합니다.
2. **밀집 보기 / 버전 함께 보기**를 전환합니다. 물리 설비 한 대는 타일 하나이며, 버전 함께 보기에서는 선택 범위의 모든 계열·PM 원문과 `N 이상 / N-k / 판단 불가`를 표시합니다. 복수 SW·PM 버전 불일치를 별도 표시하고 타일의 도움말에서 목표 N·판단 불가 사유·Fab/Line/원본 구역을 확인할 수 있습니다.
3. 채움색은 초록(목표 달성), 노랑(미달), 회색(정보 부족 또는 분석 제외)입니다. 위험 후보는 별도의 `!`와 빨간 테두리, 위험 미확인은 `?`로 구분합니다. 목표 달성과 위험 후보는 동시에 성립할 수 있습니다. 회색 HPA는 물리 대수에만 포함합니다.
4. **위험 후보 n대만 보기**를 누르면 타일 표시만 제한하며 다시 누르면 전체로 복귀합니다. 적용 조건과 오른쪽 요약은 바뀌지 않습니다. 빈 후보/빈 조회/실패를 구분하며 재적용으로 다시 확인할 수 있습니다.
5. 타일은 클릭 또는 Tab·Space로 선택해 설비 상세를 엽니다. ‘이전 조회로 돌아가기’로 히트맵에 복귀하면 조회 결과와 보기 방식·선택이 유지됩니다.

현재 히트맵은 합성 예시 63대(분석 60대·HPA 3대)입니다. 실제 회사 현황이 아닙니다. 창이 좁아지면 타일 열 수를 줄이고 요약을 아래에 배치합니다. 실제 데이터 연동은 아직 포함하지 않습니다. [히트맵 연결 계약](docs/architecture.md#버전-히트맵-작업-단위-5)과 [대시보드 연결 계약](docs/architecture.md#대시보드-작업-단위-4)을 참고합니다.

### 설비 목록·상세 확인 순서 (작업 단위 6)

1. 조회 적용 후 **선택 범위 설비 목록**을 누르거나 대시보드의 일반 셀/막대·후보 버튼을 선택합니다. 일반 셀은 해당 범위 전체, 후보 버튼은 전달된 후보만 표시합니다. 헤더 버튼도 전체 DB가 아닌 현재 적용 조건의 목록입니다.
2. 설비명·위치·모델·버전 원문 검색, 목표 상태 필터, 설비명/Site 정렬, 후보 필터와 **이전/다음**으로 12대씩 확인합니다. 복수 SW/PM이어도 물리 설비당 한 행입니다. ‘범위 전체’는 전달받은 ID 안에서 후보 제한만 해제하며, **적용 범위 전체로**를 명시적으로 눌러야 원래 공통 조회 범위로 돌아갑니다.
3. 요약은 현재 목록에 전달된 범위 전체 기준입니다. 검색·표시 필터·페이지 이동은 적용 조건이나 요약을 바꾸지 않습니다. Fab·Line과 원본 구역은 별개로 표시합니다. 좁은 창에서는 표 내부를 좌우로 스크롤합니다.
4. 행을 클릭하거나 선택 후 Enter로 상세를 엽니다. 한 상세 화면을 재사용하며 목표 달성·미달·위험 후보·정보 부족·복수 SW·HPA를 데이터에 따라 표시합니다. 설치 원문·목표 N·N 비교·비교 사유를 PM별로 확인합니다.
5. **선택 SW 판정**과 **설비 전체 등록 SW 판정**은 다릅니다. 상세 표는 같은 호기·같은 관측일의 전체 SW를 보여주지만 원래 적용 범위의 집계는 바꾸지 않습니다. 특정 SW가 달성해도 다른 SW가 미달이면 설비 전체는 미달일 수 있습니다.
6. 상세의 **이전 조회로 돌아가기**는 목록의 필터·정렬·페이지 상태 또는 출발한 히트맵으로 복귀합니다. 조건 초안만 바꾸면 기존 결과를 보존하고, 조회 재적용·선택지 변경·실패 시에는 이전 상세와 선택을 지웁니다.

현재 위험 정보는 설비 단위 판정 결과만 연결되어 있습니다. 규칙·필수 Item·릴리즈 문서·원본 셀은 **미연결**로 표시하며 근거를 만들어내지 않습니다. 모듈 수·필수 SW 목록도 미확정입니다. 관측 이력 버튼은 비활성화되어 있고, 로그인·데이터 수정/저장·DB/Excel 연동은 포함하지 않습니다. [목록·상세 연결 계약](docs/architecture.md#설비-목록상세-작업-단위-6)을 참고합니다.

### 목표 N 편집 확인 순서 (작업 단위 7)

1. 대시보드·히트맵·목록의 공통 패널에서 **목표 기준 편집**을 누릅니다. 처음 조회하기 전에도 편집할 수 있습니다. VER3의 릴리즈 선택·미적용/적용 구분을 모델·계열별 대화상자로 확장했습니다.
2. 설비군·모델·계열을 고른 뒤 목표 릴리즈를 선택합니다. 실제 공급자에 등록된 모델·계열 조합만 표시하며, 같은 설비군/계열도 모델이 다르면 독립적으로 선택합니다. Site·Fab·Line별 목표를 만들거나 현재 설비군 하나에만 몰래 제한하지 않습니다.
3. 같은 계열의 `version_order`가 확인되고 중복되지 않는 릴리즈만 새 목표로 선택할 수 있습니다. 순서가 확인된 Hotfix도 허용하며 문자열 크기·접미사로 순서를 추정하지 않습니다. 순서 미확인/중복·릴리즈 ID 중복은 선택 불가로 표시합니다.
4. 여러 모델·계열을 바꾸고 변경 요약을 확인한 뒤 **선택 완료**를 누릅니다. 이는 **목표 초안 변경**일 뿐 DB 저장이나 조회 적용이 아닙니다. 선택 중 N과 기존 적용 N을 별도로 표시하며 이전 숫자는 유지합니다.
5. **조회 적용**을 누르면 위치·설비군·목표 N을 함께 확정합니다. 대시보드·추이·히트맵·목록 요약·설비 전체 상세가 같은 적용 N으로 다시 계산됩니다. 위험 판정은 N 변경과 독립적입니다.
6. **취소 / Esc / 창 닫기**는 대화상자 안의 편집을 버립니다. **선택 원래대로**는 대화상자를 열었을 때의 초안으로 돌아가며 공급자 기본값으로 바꾸지 않습니다. 공통 패널의 **전체 조건으로**는 위치·설비군만 초기화하고 N은 유지합니다.

‘목표 미지정’은 해당 모델·계열을 판단 불가로 두는 명시적 선택입니다. 기존 잘못된 목표도 자동으로 정상 릴리즈에 연결하지 않으며 미확인/확인 필요로 알립니다. 편집 도중 조회 선택지나 릴리즈 자료가 바뀌면 완료를 거부하고 다시 열도록 안내합니다. 빈 선택지에서는 가짜 릴리즈를 만들지 않습니다.

현재 목표 선택은 **앱 실행 중에만 유지**됩니다. 원본 버전·공급자 기본 목표·위험 규칙·DB·파일을 수정하지 않으며 앱을 새로 열면 초기 목표로 시작합니다. 실제 저장·사용자별 정책·기준일 변경은 후속 작업입니다. [목표 N 연결 계약](docs/architecture.md#목표-n-편집-작업-단위-7)을 참고합니다. 설치 확인 도구는 아래 명령으로 계속 별도 실행할 수 있습니다.

### UI 개발 전 설치와 실행 확인

저장소 최상위 폴더에서 `uv sync --locked`를 실행해 Python 3.13 전용 `.venv`와 잠금 파일에 지정된 패키지를 준비합니다. 기존 Python 환경은 그대로 유지합니다.

다음 명령은 DB·Excel·AI 연결 없이 설치 확인 창을 엽니다. Python·PySide6·Qt 버전과 화면 플랫폼을 확인하고 `닫기` 버튼으로 종료합니다.

```powershell
uv run --locked python -m setron.ui.check_environment
```

이미 설치된 가상환경의 Python으로 직접 실행할 수도 있습니다.

```powershell
.\.venv\Scripts\python.exe -m setron.ui.check_environment
```

설치 확인 창은 업무 화면과 별개입니다. 이후 화면 개발 결과는 위의 앱 실행 명령(`python -m setron`)으로 확인합니다.

### 버전 목록·상세 확인 순서 (B-1)

**버전 관리** 메뉴에서 읽기 전용 릴리즈 목록을 확인할 수 있습니다. 기존 `ui/` 공통 창·카드·표·버튼·폰트를 재사용합니다. 버전 수정·저장과 목표 N 편집은 서로 다른 기능이며, 이 화면에서는 둘 다 변경하지 않습니다.

1. **모델 / SW 계열**을 선택하고 버전 원문 또는 출처 문서명으로 검색합니다. 모델을 바꾸면 연결된 계열만 표시합니다. 이 선택은 대시보드의 Site·Fab·Line·설비군 조건과 별개입니다.
2. 확인된 `version_order` 기준 오름차순/내림차순으로 보고 12건씩 이동합니다. 순서 미확인은 마지막에 두고 중복 순서는 선후 미확인으로 표시합니다. 버전 문자열·출시일로 순서를 추정하지 않습니다.
3. 행 클릭 또는 Enter로 상세를 엽니다. 버전 원문·출시일·확인된 순서·연결 모델·Item 매핑 상태와 포함 Item·모델 범위·출처 문서/리비전/시트/셀/입력 작업을 확인합니다. 누락된 값은 미확인으로 표시합니다.
4. Item의 릴리즈 포함 상태와 선택 모델 판정(적용/미적용/미확인/대상 외)을 구분합니다. 선택 모델 판정은 실제 설비 배포 현황이 아닙니다. 같은 계열의 Item이라도 해당 버전의 저장 행이 없으면 미확인이고, 최신 버전이라는 이유로 상속하지 않습니다.
5. **릴리즈 목록으로 돌아가기**는 검색·정렬·페이지·선택 행을 보존합니다. 메뉴를 이동해도 같은 페이지 인스턴스를 사용합니다. **다시 불러오기**는 이전 상세를 지우고 목록을 갱신하며 실패 시 재시도 안내를 표시합니다.

기본 실행은 A의 합성 릴리즈 ID·원문·순서를 재사용하고, B-1 확인용 합성 Item과 문서·셀 위치를 덧붙입니다. 실제 문서나 회사 현황이 아닙니다. 기존 릴리즈에 출시일이 없으므로 예시에서도 출시일을 만들어내지 않습니다. 실제 DB·Excel·원본 파일 열기는 미연결이며 수정·저장은 B-2, 설비별 Item 적용 현황 연결은 B-4 범위입니다.

검증 자료는 `create_window(version_loader=...)`로 메모리의 `VersionCatalog` 공급자를 주입할 수 있습니다. A 공급자만 주입하면 그 공급자의 릴리즈를 읽고 Item/출처는 미연결로 표시하며 합성 정보를 추가하지 않습니다. 실제 DB 공급자·워커 연결은 별도 통합 작업입니다. [B-1 연결 계약](docs/architecture.md#b-1-릴리즈-목록과-상세)과 [검증 범위](docs/testing.md#버전-목록상세-검증-b-1)를 참고합니다.

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
│       │   ├── theme.py
│       │   ├── assets/
│       │   └── check_environment.py
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
│   ├── test_ui_common.py
│   ├── test_ui_query.py
│   ├── test_ui_dashboard.py
│   ├── test_ui_heatmap.py
│   ├── test_ui_equipment.py
│   ├── test_ui_targets.py
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

### Item 매니저 (B-3)

**아이템 매니저** 메뉴에서 변경 내용·원본 Item 번호로 검색하고 행 선택 또는 Enter로 상세를 엽니다. 같은 번호도 Item ID로 구분하며, 목록은 12건씩 표시합니다. 상세에서 돌아오면 검색어·페이지·선택 행을 유지합니다.

상세는 Item 원문·출처, 계열별 포함 릴리즈와 확인된 순서, 모델 적용 범위, 한 관측 기준일의 설비별 적용 현황과 PM별 근거를 표시합니다. 적용·미적용·미확인·대상 외를 구분하며 저장 행이 없는 버전에 이전 Item을 자동 상속하지 않습니다. HPA는 기존 업무 규칙에 따라 제외하고 제외 대수를 표시합니다.

현재 기본 자료는 **합성 예시이며 DB 미연결**입니다. 설비 현황은 본문에 표시된 기준일의 전체 관측 범위이며 A의 공통 조회 조건·목표 N과 독립적입니다. 자료가 미연결인 경우 0대 또는 대상 외로 확정하지 않습니다. Item 편집·저장은 포함하지 않습니다. 실제 공급자 연결 계약은 [아키텍처](docs/architecture.md#item-매니저-b-3)를 참고합니다.

### Item 배포 시각화·근거 연결 (B-4)

Item 상세의 **Site · 설비 히트맵** 보기를 선택하면 Site별 네 상태의 대수와 설비 타일을 확인할 수 있습니다. Site의 상태 셀을 선택하면 같은 범위로 타일과 목록을 좁힙니다. 상태 필터의 **적용**을 선택하면 적용 설비 목록을 볼 수 있습니다. HPA는 집계에서 제외되며, 색상과 상태 문구를 함께 표시합니다.

설비 행의 Enter 또는 타일의 클릭/Space로 **기존 설비 상세**를 엽니다. 같은 관측일·설비 ID의 PM별 적용 판정, 대응 릴리즈, 저장 행 유무, Item·릴리즈의 문서/리비전/시트/셀 출처를 표시합니다. 돌아오면 Item 검색·페이지·Site·상태·보기·선택 설비와 화면 스크롤이 유지됩니다. 자료 재조회·Item/범위 변경 후의 오래된 이동 요청은 거부합니다.

이 경로는 A의 공통 조회 범위나 목표 N을 바꾸지 않으며 Item 판정을 N 비교로 대신하지 않습니다. 실제 DB·원본 문서/셀 열기·위험 규칙 근거는 미연결입니다. Item 적용만으로 안전 여부를 추정하지 않습니다.

## 문서

개발 작업은 작업별 브랜치에서 진행한 뒤 `develop`에 MR로 통합합니다. 릴리스할 변경이 준비되면 `develop → master` MR로 반영합니다. 자세한 절차는 Git 협업 규칙을 따릅니다.

- [폴더 아키텍처](docs/architecture.md): 모듈별 책임과 기능 추가 원칙
- [Git 협업 규칙](docs/git-conventions.md): 브랜치, 커밋, 리뷰 및 병합 규칙
- [테스트 전략](docs/testing.md): 테스트 종류별 범위, 기능별 체크리스트, 실행·결과 기록 방법
- [ERD 초안](docs/mermaid-diagram.png): 작성 중인 데이터 관계도. DB 스키마는 아직 확정되지 않았습니다.
