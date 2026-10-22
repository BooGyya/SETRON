# 폴더 아키텍처

setron은 Python 기반 Windows 데스크톱 애플리케이션입니다. 단일 저장소와 실행 패키지 `src/setron/` 안에서 역할별 모듈을 분리합니다.

현재는 화면, 업무 로직, 외부 기술 연동을 나누는 최소 구조만 갖춘 상태입니다. 세부 기능과 연동 코드는 구현을 시작할 때 추가합니다.

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
│       │   └── __init__.py
│       └── infrastructure/
│           └── __init__.py
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

각 Python 패키지에는 `__init__.py`를 둡니다. `__main__.py`는 실행 진입점이며, 구현 시 DB 연결 설정, 서비스와 화면의 연결, 애플리케이션 시작을 담당합니다. 현재는 설명문만 있어 실행해도 화면이 뜨지 않고 종료됩니다. 설치와 실행 방법은 [README](../README.md)를 참고합니다.

`tests/`는 `.gitkeep`으로 빈 폴더를 유지하며, 첫 테스트를 추가하면 이 파일은 제거할 수 있습니다. 데이터 관계는 [ERD](mermaid-diagram.png)에 정리합니다.

## 폴더별 책임

| 경로 | 책임 |
| --- | --- |
| `src/setron/ui/` | PySide6 화면, 사용자 입력, 화면용 테이블 모델, 백그라운드 작업 실행과 결과 표시 |
| `src/setron/features/` | 설비·버전·Item·Excel·통계 업무 로직 |
| `src/setron/infrastructure/` | DB 엔진·세션·ORM 모델과 외부 기술 연동 |
| `tests/` | 업무 로직 및 외부 연동 검증 |
| `docs/` | 아키텍처, ERD와 협업 규칙 |

## 모듈 간 관계

기능을 구현할 때 다음 원칙을 따릅니다.

- UI는 서비스에 작업을 요청하고 결과를 화면에 표시합니다. DB에 직접 접근하지 않습니다.
- 업무 서비스는 같은 기능의 Repository를 통해 데이터를 조회하거나 저장합니다. Repository는 `infrastructure/`의 DB 세션과 ORM 모델을 사용합니다.
- `features/`는 `ui/`와 PySide6를 import하지 않습니다. 업무 로직은 화면 없이 검증할 수 있도록 작성합니다.
- DB 접속과 세션 관리 등 기술적인 연동은 `infrastructure/`에 둡니다.
- `infrastructure/`는 `ui/`와 `features/`를 import하지 않습니다. 초기 의존 방향은 `ui → features → infrastructure`로 유지합니다.

예를 들어 Heatmap 화면은 집계 서비스를 호출하고, 서비스는 Repository를 통해 필요한 설비·버전 데이터를 조회합니다.

Excel 대량 처리처럼 오래 걸리는 작업은 `ui/`의 워커에서 서비스를 호출해 실행합니다. 위젯 생성과 화면 갱신은 UI 스레드에서 처리하고, 워커의 결과나 오류는 Qt 시그널로 전달합니다. 스레드 간 객체 호출은 [Qt의 스레드 규칙](https://doc.qt.io/qtforpython-6/overviews/qtdoc-threads-qobject.html)을 따릅니다.

DB 작업을 실행하는 스레드에서 세션을 열고 작업이 끝나면 닫습니다. UI 스레드와 워커가 같은 `Session`을 공유하지 않습니다. 여러 저장을 하나의 업무로 처리해야 한다면 서비스가 트랜잭션 범위를 정하고, Repository가 호출마다 별도로 commit하지 않도록 합니다. 세션과 트랜잭션의 수명은 [SQLAlchemy의 세션 지침](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)을 따릅니다.

## 기능을 추가할 때

업무 기능은 필요해지는 시점에 `features/` 아래에 분리합니다. 예를 들어 설비 기능은 `features/equipment/`에 추가합니다.

- `service.py`: 업무 흐름과 규칙
- `repository.py`: 해당 기능의 데이터 조회 및 저장. DB 연결과 ORM 모델 정의는 `infrastructure/`에 유지
- `schemas.py`: 화면과 서비스가 주고받는 데이터 구조

파일은 실제로 맡을 책임이 생길 때 만듭니다. 초기부터 모든 기능에 동일한 파일을 만들 필요는 없습니다.

화면은 `ui/`, 업무 처리는 `features/`에 두고 화면과 서비스 사이의 입력·반환 형식은 함께 합의합니다. 공통 코드도 먼저 해당 기능 안에서 관리하고, 여러 곳에서 필요해질 때 별도 패키지로 분리합니다. AI 연동 등 추가 기능을 위한 폴더도 실제 구현이 필요해질 때 만듭니다.

## 문서 갱신

폴더를 추가하거나 이동하고, 모듈의 책임 또는 호출 관계를 바꾸면 같은 변경에 이 문서도 갱신합니다. 프로젝트 전체 구조가 달라지면 README의 구조 설명도 함께 수정합니다.
