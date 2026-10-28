# 폴더 아키텍처

setron은 Python 기반 Windows 데스크톱 애플리케이션입니다. 단일 저장소와 실행 패키지 `src/setron/` 안에서 역할별 모듈을 분리합니다.

화면, 업무 로직, 외부 기술 연동을 나누는 최소 구조를 사용합니다. 현재는 열린 Excel에서 값을 읽는 모듈, 예제 응답으로 동작하는 AI 호출 흐름과 해당 테스트가 구현되어 있으며, 화면과 DB 연동 코드는 기능 구현 시 추가합니다.

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
│       │   ├── ai/
│       │   │   ├── __init__.py
│       │   │   ├── examples.py
│       │   │   ├── schemas.py
│       │   │   └── service.py
│       │   └── version/
│       │       ├── __init__.py
│       │       ├── schemas.py
│       │       └── service.py
│       └── infrastructure/
│           ├── __init__.py
│           ├── ai_client.py
│           └── excel_reader.py
├── tests/
│   ├── test_ai_service.py
│   ├── test_excel_reader.py
│   └── test_version_service.py
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

각 Python 패키지에는 `__init__.py`를 둡니다. `__main__.py`는 실행 진입점이며, 구현 시 DB 연결 설정, 서비스와 화면의 연결, 애플리케이션 시작을 담당합니다. 현재는 설명문만 있어 실행해도 화면이 뜨지 않고 종료됩니다. 설치와 실행 방법은 [README](../README.md)를 참고합니다.

`tests/test_excel_reader.py`는 실제 Excel 대신 가짜 통합문서를 사용해 읽기 모듈의 순수 로직과 오류 처리를 검증합니다.

회사 AI API 정보(주소·모델·인증·호출 제한)를 받기 전까지는 `infrastructure/ai_client.py`의 `ExampleChatClient`가 미리 정한 정상·실패 응답을 돌려줍니다. `features/ai/`는 Excel 추출·질문 해석·분석 작업의 요청과 응답 형식을 정하고, 호출 실패와 형식 오류를 결과 상태로 바꿉니다. 실제 클라이언트는 같은 `ChatClient` 형식으로 추가하며, 결과의 `source`로 예제 응답과 실제 API 결과를 구분합니다. `tests/test_ai_service.py`가 이 흐름을 예제 응답으로 검증합니다.

`features/version/`은 같은 버전 계열 안에서 설비 버전을 목표 N과 비교해 'N 이상', 'N-k', '판단 불가'를 계산합니다. 순서는 릴리즈 자료에서 확인한 `version_order`만 사용하고 버전 문자열이나 접미사로 추정하지 않습니다. DB 없이 동작하는 순수 계산이며, Repository가 `release_version` 행을 `ReleaseVersion`으로 바꿔 전달합니다. `tests/test_version_service.py`가 검증합니다.

데이터 관계를 정리한 [ERD](mermaid-diagram.png)는 작성 중인 초안이며, 확정된 DB 스키마가 아닙니다.

## 폴더별 책임

| 경로 | 책임 |
| --- | --- |
| `src/setron/ui/` | PySide6 화면, 사용자 입력, 화면용 테이블 모델, 백그라운드 작업 실행과 결과 표시 |
| `src/setron/features/` | 설비·버전·Item·Excel·통계 업무 로직 |
| `src/setron/infrastructure/` | DB 엔진·세션·ORM 모델과 외부 기술 연동 |
| `tests/` | 업무 로직 및 외부 연동 검증 |
| `docs/` | 아키텍처, ERD, 협업 규칙과 테스트 전략 |

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
