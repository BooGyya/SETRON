# S15P31B106

setron은 설비 소프트웨어 버전 및 Release Item을 관리하는 Python 기반 Windows 데스크톱 애플리케이션입니다. PySide6로 화면을 구성하고 SQLAlchemy와 PyMySQL로 각 PC의 MySQL에 연결할 예정입니다. 현재 Excel 읽기는 xlwings를 사용하며, pandas와 openpyxl은 데이터 처리와 XLSX 출력에 사용할 의존성입니다.

현재는 최소 패키지 구조와 열린 Excel 읽기 모듈이 구현되어 있습니다. 화면, 업무 서비스, DB·AI 연동은 아직 구현되어 있지 않습니다.

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

### DB 연동 개발 기준

개발과 자동화 테스트에서는 Docker의 MySQL 5.6.19를 사용하고, 실제 사용 PC에서는 Windows에 설치된 MySQL 5.6.19에 접속하도록 개발합니다. Python 앱과 Excel은 두 환경 모두 Windows에서 실행합니다.

앱의 DB 접속 코드는 공통으로 사용하고, 호스트·포트·DB 이름·계정·비밀번호는 `SETRON_DB_*` 환경변수에서 읽도록 구현합니다. 개발 DB는 `127.0.0.1:13306`, Windows DB는 해당 서버 주소와 설정된 포트(기본 `3306`)를 지정합니다. Windows DB가 앱과 같은 PC에 있으면 주소는 `127.0.0.1`입니다. 접속 설정을 바꾸면 같은 코드로 대상 DB를 전환할 수 있도록 하고, 앱의 DB 연동이 Docker 명령이나 컨테이너 내부 경로에 의존하지 않도록 합니다.

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

`--wait`는 DB가 쿼리를 받을 수 있을 때까지 기다립니다. 처음 실행하면 빈 DB와 일반 계정을 만들며, 업무 테이블은 별도 스키마 작업에서 적용합니다. 초기화된 볼륨이 있으면 `.env`의 DB 이름·계정·비밀번호를 바꿔도 기존 DB와 계정에 자동 반영되지 않으므로 DB에서 별도로 변경해야 합니다.

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

## 실행

```powershell
uv run --locked python -m setron
```

실행 진입점은 `src/setron/__main__.py`입니다. 현재는 설명문만 있어 위 명령을 실행해도 화면이 뜨지 않고 종료됩니다. 화면 초기화 코드는 UI 구현 시 추가합니다.

## 테스트

```powershell
uv run --locked pytest
```

`tests/test_excel_reader.py`는 셀 주소 변환, 병합 셀 보정, 열린 통합문서 목록과 Excel 미실행·연결 실패 처리를 검증합니다. 실제 Excel을 실행하지 않고 테스트할 수 있으며, 실제 Excel·DB 연동 검증은 별도로 진행합니다. 테스트 종류별 범위, 기능별 체크리스트와 결과 기록 방식은 [테스트 전략](docs/testing.md)을 따릅니다.

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
│           └── excel_reader.py
├── tests/
│   └── test_excel_reader.py
├── docs/
│   ├── architecture.md
│   ├── git-conventions.md
│   ├── testing.md
│   └── mermaid-diagram.png
├── .gitignore
├── .env.example
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
