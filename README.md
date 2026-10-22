# S15P31B106

setron은 설비 소프트웨어 버전 및 Release Item을 관리하는 Python 기반 Windows 데스크톱 애플리케이션입니다. PySide6로 화면을 구성하고, SQLAlchemy와 PyMySQL로 DB에 연결하며, pandas와 openpyxl로 Excel 데이터를 처리할 예정입니다.

현재는 초기 폴더와 패키지 구조만 갖춘 상태입니다. 화면과 업무 기능은 아직 구현되어 있지 않습니다.

## 개발 환경 준비

Python과 pip가 설치된 Windows 환경에서 시작합니다. 지원 Python 버전은 아직 `pyproject.toml`에 지정되어 있지 않습니다.

저장소 루트에서 PowerShell로 다음 명령을 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

가상환경을 만든 뒤 프로젝트와 의존성을 설치합니다. 편집 가능 모드(`-e`)로 설치하므로 `src/`의 코드 수정이 바로 반영됩니다.

## 실행

```powershell
.\.venv\Scripts\python.exe -m setron
```

실행 진입점은 `src/setron/__main__.py`입니다. 현재는 설명문만 있어 위 명령을 실행해도 화면이 뜨지 않고 종료됩니다. 화면 초기화 코드는 UI 구현 시 추가합니다.

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
├── tests/
│   └── .gitkeep
├── docs/
│   ├── architecture.md
│   ├── git-conventions.md
│   └── mermaid-diagram.png
├── .gitignore
├── pyproject.toml
└── README.md
```

`ui/`, `features/`, `infrastructure/`에는 각각 `__init__.py`를 둡니다. 기능별 폴더와 파일은 실제 구현이 필요할 때 추가합니다. `tests/`에는 아직 테스트 코드가 없으며, 빈 폴더를 Git에 포함하기 위한 `.gitkeep`만 있습니다.

## 문서

개발 작업은 작업별 브랜치에서 진행한 뒤 `develop`에 MR로 통합합니다. 릴리스할 변경이 준비되면 `develop → master` MR로 반영합니다. 자세한 절차는 Git 협업 규칙을 따릅니다.

- [폴더 아키텍처](docs/architecture.md): 모듈별 책임과 기능 추가 원칙
- [Git 협업 규칙](docs/git-conventions.md): 브랜치, 커밋, 리뷰 및 병합 규칙
- [ERD](docs/mermaid-diagram.png): 설비·버전·Release Item의 데이터 관계
