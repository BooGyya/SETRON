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
├── .python-version
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
