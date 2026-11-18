<div align="center">

<img src="docs/assets/logo.png" alt="SETRON 로고" width="360"/>

### 설비 소프트웨어 버전 관리를 한눈에

**AI 기반 SW Version 분포 Heatmap 솔루션**

SSAFY 15기 기업연계 프로젝트 (SEMES) · 2026.10.06 ~ 2026.11.18 · TEAM B106 (마블룰렛)

![Python](https://img.shields.io/badge/Python_3.13-3776AB?style=flat-square&logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/PySide6_6.12-41CD52?style=flat-square&logo=qt&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL_5.6.19-4479A1?style=flat-square&logo=mysql&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy_2.1-D71F00?style=flat-square&logo=sqlalchemy&logoColor=white)
![OpenAI API](https://img.shields.io/badge/OpenAI_호환_API-412991?style=flat-square&logo=openai&logoColor=white)
![pandas](https://img.shields.io/badge/pandas-150458?style=flat-square&logo=pandas&logoColor=white)
![PyInstaller](https://img.shields.io/badge/PyInstaller-3776AB?style=flat-square&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white)
![pytest](https://img.shields.io/badge/pytest-0A9EDC?style=flat-square&logo=pytest&logoColor=white)

<img src="docs/assets/slides/cover.png" alt="SETRON 메인 비주얼" width="760"/>

</div>

---

## 📑 목차

1. [프로젝트 소개](#-프로젝트-소개)
2. [주요 기능](#-주요-기능)
3. [화면 구성](#️-화면-구성)
4. [핵심 기술 — AI 답변의 신뢰성](#-핵심-기술--ai-답변의-신뢰성)
5. [기술 스택](#️-기술-스택)
6. [시스템 아키텍처](#️-시스템-아키텍처)
7. [명세](#-명세)
8. [프로젝트 폴더 구조](#-프로젝트-폴더-구조)
9. [실행 방법](#-실행-방법)
10. [팀원 소개](#-팀원-소개)
11. [협업 방식](#-협업-방식)

---

## 👋 프로젝트 소개

**SETRON**(SEMES Tracking Monitoring)은 반도체 장비 기업 **SEMES**의 생산 설비에 설치된 소프트웨어 버전을 한눈에 보고, 업데이트 대상을 찾고, 자연어로 분석하는 **Windows 데스크톱 애플리케이션**입니다.

- **기획 배경** — 24시간 가동하는 생산 설비는 노트북처럼 밤사이 자동 업데이트할 수 없습니다. 설비를 멈추고 새 버전이 공정에 영향을 주지 않는지 확인해야 하므로, 같은 기종이라도 어떤 설비는 최신 버전 **N**, 어떤 설비는 **N-1**, **N-3**에 머물게 됩니다.
- **문제 의식** — 평택 · 화성 · 기흥 사업장마다 **Excel 양식과 버전 표기가 다르고**(`4_56_6_42` vs `v4.56.6.42`), 설비 하나의 버전을 확인하려면 **여러 파일을 열어 대조**해야 하며, 현황 **보고서는 수작업**으로 약 4시간이 걸렸습니다.
- **해결** — 흩어진 Excel 현황표와 릴리즈 노트를 **규칙 · AI로 읽어 사람이 검토한 뒤 DB에 통합**하고, 목표 버전 대비 차이를 **히트맵 색으로 보여 주며**, 자연어 질문으로 분석해 **Word · PDF 보고서까지 자동 생성**합니다.

<div align="center">

| 사업장마다 다른 양식 · 표기 | 기업 제약 조건 |
| --- | --- |
| <img src="docs/assets/slides/problem.png" width="440"/> | <img src="docs/assets/slides/constraints.png" width="440"/> |

</div>

| | |
| --- | --- |
| **서비스명** | SETRON — **SE**MES **TR**acking m**ON**itoring |
| **프로젝트 기간** | 2026.10.06 ~ 2026.11.18 (SSAFY 15기 기업연계 프로젝트, 7주) |
| **고객사** | SEMES SW CS그룹 · 주 1회 멘토링 7회 |
| **팀** | B106 마블룰렛 (6명) |
| **플랫폼** | Windows 데스크톱 앱 (PySide6) · 서버 없이 각 PC의 MySQL 사용 |
| **배포** | PyInstaller로 빌드한 `SETRON.exe` + `setron.env` 설정 파일 · 유지보수 가이드 3종(60p) 인계 |

> **기업 제약 조건** — ① 실제 회사 데이터는 제공 불가 → 교육용 자료로 합성 데이터 제작 ② AI는 회사의 OpenAI 호환 API만 사용 가능 ③ 서버 없이 각자 PC의 MySQL 5.6.19 사용

---

## ✨ 주요 기능

기업 요구사항 6개를 모두 구현하고, 멘토님께 제안해 반영된 추가 기능 4개를 더했습니다.

<div align="center">
<img src="docs/assets/slides/requirements.png" alt="기업 요구사항" width="720"/>
</div>

### 📊 ① 대시보드
- 조회 범위(기준날짜 · 분류 · 사업장 · 라인 · 설비군)의 **목표 N 달성률 · 분석 대상 · 목표 미달 · 버전 확인 필요 · 위험 후보** KPI 5종을 보여 줍니다.
- 사업장 × 설비군 **히트맵**, 구성비 **원형 그래프**, 구역별 **막대 그래프**, 기간별 **꺾은선 그래프** 4가지 보기로 전환합니다.

### 🟩 ② 버전 히트맵
- 설비 1대를 칸 1개로 그려 목표 N 대비 **N+1 / N / N-1 / N-2 이하 / 비교 불가**를 색으로 구분하고, 상대 버전 ↔ 실제 버전 표시를 전환합니다.
- 칸을 누르면 **설비 상세 패널**에서 현재 · 목표 버전, 적용된 위험 기준과 판정 근거를 확인합니다.

### 📥 ③ Excel 가져오기 · 내보내기
- 파일을 업로드하지 않고 **사용자가 열어 둔 Excel 창에서 직접 읽습니다**(xlwings COM). 정해진 양식은 규칙으로, 그 외 양식은 **AI 추출**로 읽습니다.
- 추출 값마다 **원본 시트 · 셀 위치 꼬리표**를 붙이고 검토 · 일괄 수정 · 자동 검사를 거친 뒤 저장합니다. 현황 Excel · DB 전체 SQL 백업으로 내보내고, SQL 파일로 다른 PC와 자료를 공유합니다.

### 🧩 ④ 아이템 매니저
- 릴리즈 노트의 Item(기능 변경)을 버전 · 기능명 · Jira 코드로 검색하고, 설비별 **적용 · 미적용 현황**(= 업데이트 대상)을 봅니다.
- 특정 버전 구간의 결함 Item이나 필수 Item을 **위험 기준**으로 등록하면 대시보드와 히트맵의 위험 후보에 반영됩니다.

### 🤖 ⑤ AI 분석
- "화성 C4 설비 중 N-2 이하 설비를 알려줘", "버전 통일률이 가장 낮은 라인을 분석해줘"처럼 **자연어로 질문**하면 표 · 그래프로 답합니다.
- 조회 · 비교 · 기간 변화 · 원인 · 우선순위 · **통일 시점 예측** · 위험도 · 월간 보고서 도구를 제공하고, 답변마다 **판단 근거**(AI가 이해한 조건)를 펼쳐 볼 수 있습니다.

### ⏯️ ⑥ 버전 리플레이
- 저장된 기준일을 순서대로 **재생**(0.5× ~ 4×)하며 달성률 추이와 히트맵 변화를 보고, 재생 막대를 끌어 원하는 시점으로 이동합니다.

### ➕ 추가 구현

<div align="center">
<img src="docs/assets/slides/extras.png" alt="추가 구현 사항" width="720"/>
</div>

| 추가 기능 | 내용 |
| --- | --- |
| **HITL(Human in the Loop) 검증** | AI가 읽은 결과를 사용자가 원본과 대조해 확인 · 수정하고, 반복되는 값은 "모든 행에 적용"으로 한 번에 고친 뒤 저장 |
| **사용자 맞춤형 UI** | 표시 모드(밝게 · 어둡게 · 시스템) · 글자 크기 · 포인트 색상 · 대시보드/히트맵 색상 · 배경색을 사용자가 선택, **다크 모드** 지원 |
| **목적별 맞춤 보고서** | 요약 · 적용률 추이 · 히트맵 · 위험 근거 · 업그레이드 체크 등 **필요한 내용만 골라** Word · PDF로 생성, 히트맵은 항목별 Excel로 저장 |
| **유지보수 가이드 문서** | 개발 문서(10p) · 사용자 가이드(40p) · 인수인계서(10p)로 기업이 자체 유지보수 가능 |

---

## 🖥️ 화면 구성

### 대시보드

| 히트맵 보기 | 원형 그래프 |
| --- | --- |
| <img src="docs/assets/screens/dashboard.png" width="440"/> | <img src="docs/assets/screens/dashboard-pie.png" width="440"/> |

| 막대 그래프 | 꺾은선 그래프 (기간 추이) |
| --- | --- |
| <img src="docs/assets/screens/dashboard-bar.png" width="440"/> | <img src="docs/assets/screens/dashboard-line.png" width="440"/> |

### 버전 히트맵 · 버전 리플레이

| 버전 히트맵 | 설비 상세 패널 |
| --- | --- |
| <img src="docs/assets/screens/heatmap.png" width="440"/> | <img src="docs/assets/screens/heatmap-detail.png" width="440"/> |

| 버전 리플레이 | 아이템 매니저 |
| --- | --- |
| <img src="docs/assets/screens/replay.png" width="440"/> | <img src="docs/assets/screens/items.png" width="440"/> |

### AI 분석

| 시작 화면 (예시 질문) | 조회 질문 답변 |
| --- | --- |
| <img src="docs/assets/screens/ai.png" width="440"/> | <img src="docs/assets/screens/ai-answer-1.png" width="440"/> |

| 분석 질문 답변 (그래프) | 다크 모드 |
| --- | --- |
| <img src="docs/assets/screens/ai-answer-2.png" width="440"/> | <img src="docs/assets/screens/ai-dark.png" width="440"/> |

### 데이터 가져오기 · 내보내기 · 관리

| Excel 가져오기 (시트 선택) | 원본 미리보기 |
| --- | --- |
| <img src="docs/assets/screens/import.png" width="440"/> | <img src="docs/assets/screens/import-preview.png" width="440"/> |

| 추출 결과 검토 항목 (HITL · 원본 셀 위치) | 데이터 내보내기 |
| --- | --- |
| <img src="docs/assets/screens/import-review.png" width="440"/> | <img src="docs/assets/screens/export.png" width="440"/> |

| 데이터 관리 | 보고서 |
| --- | --- |
| <img src="docs/assets/screens/data.png" width="440"/> | <img src="docs/assets/screens/reports.png" width="440"/> |

### 설정 · 다크 모드

| 설정 (테마 · 글자 · 색상) | 대시보드 다크 모드 |
| --- | --- |
| <img src="docs/assets/screens/settings.png" width="440"/> | <img src="docs/assets/screens/dashboard-dark.png" width="440"/> |

| 히트맵 다크 모드 | 맞춤 UI 설정 항목 |
| --- | --- |
| <img src="docs/assets/screens/heatmap-dark.png" width="440"/> | <img src="docs/assets/slides/ui-custom.png" width="440"/> |

---

## 🧠 핵심 기술 — AI 답변의 신뢰성

> "AI가 읽고 답한 숫자, 믿을 수 있을까요?" — SETRON은 **AI가 숫자를 만들지 않는 구조**로 이 질문에 답합니다.

### 1. 원본 대조 — AI 추출 결과에 셀 위치 꼬리표

```
열린 Excel 창 ──▶ 규칙 추출 / AI 추출 ──▶ 원본 셀 대조 ──▶ 사람 검토·수정 ──▶ 저장 직전 재대조 ──▶ MySQL
  (xlwings)      (정해진 양식 / 그 외)     (값·위치 확인)      (HITL)              (변경분 확인)
```

- AI가 돌려준 값마다 **원본 시트 · 셀 주소 · 실제 셀 값**을 `sources`로 붙이고, 원본과 일치하지 않는 값은 검토값에서 제외해 `issues`로 남깁니다.
- 검토 표에서 칸을 누르면 원본 위치가 꼬리표로 보이고, 저장 직전에 원본을 **다시 대조**합니다. 사람이 "검토 완료 · 저장"을 누르기 전에는 DB에 아무것도 쓰지 않습니다.
- 병합 셀의 실제 앵커, 호기 · PM · SW 연결, 사업장별 구조 차이(평택 가로형 / 화성 세로형)를 보존합니다.

<div align="center">
<img src="docs/assets/slides/hitl.png" alt="HITL 검증" width="720"/>
</div>

### 2. AI 분석 — AI가 계획하고, 규칙이 검사하고, DB가 계산한다

| 단계 | 담당 | 하는 일 |
| --- | --- | --- |
| 계획 | **AI** | 질문을 "어떤 도구를, 어떤 조건으로" 쓸지 JSON으로만 계획 |
| 검사 | **규칙** | 도구가 있는지, 사업장 · 라인 · 설비군 이름이 선택지에 있는지, 날짜가 저장된 기준일에 있는지 검증 |
| 계산 | **DB** | 숫자 · 표 · 그래프를 대시보드와 같은 집계로 계산 |
| 대체 | **규칙** | AI 없음 · 실패 · 형식 오류, 바로 분석 버튼, 추천 질문, 인사말 처리 |

- AI에게는 **질문과 선택지 이름 · 기준일 목록만** 보냅니다. 설비 자료는 외부로 나가지 않습니다.
- 없는 기능 · 없는 날짜는 다른 것으로 바꿔 답하지 않고 "없다 + 대신 할 수 있는 것"을 말합니다. 두 가지로 읽히면 **확인 질문**을 던집니다.
- 질문마다 도구를 만들지 않고 **무엇을 셀지(설비 수 · 버전 종류 · 파편화 · 달성률 · 위험도) × 무엇별로(사업장 · 라인 · 모델 · 계열 · 기준일)** 를 조합하는 범용 집계 도구를 둬, 새 질문은 도구 하나만 추가합니다.
- 답변에 "질문을 '…'(으)로 이해했어요"를 남겨 사용자가 **판단 근거**를 확인할 수 있습니다.

### 3. 상대 버전(N 단계) 판정과 집계

- 버전은 **같은 SW 계열 안에서만** 비교하며, 순서는 숫자 크기가 아니라 **릴리즈 노트에 기재된 순서**를 따릅니다. `4_56_6_42` · `4.56.6.42` · `v4.56.6.42`처럼 사업장마다 다른 표기는 번역표와 확인된 표기 대응(`version_aliases`)으로 같은 버전으로 묶습니다.
- 모델 · SW 계열별 **목표 N**을 정하면 각 설비를 N+1 이상 / N / N-1 / N-2 이하 / 비교 불가로 판정하고, 목표가 바뀌면 전체 판정을 다시 계산하며 이력을 남깁니다.
- **목표 달성률 = 목표 달성 ÷ (목표 달성 + 목표 미달)**. 판단할 수 없는 건(릴리즈 노트에 없는 버전, 버전 누락, 목표 N 미지정)은 0%로 깎지 않고 분모에서 빼서 따로 표시합니다.

### 4. 기업 환경 그대로 동작

- **AI 교체 가능성** — 환경변수 4개(`SETRON_AI_BASE_URL` · `API_KEY` · `MODEL` · `TIMEOUT`)만 바꾸면 개발용 GMS에서 사내 OpenAI 호환 서버로 코드 수정 없이 전환됩니다. 서버별 지원이 확인되지 않은 JSON 강제 출력 · 도구 호출 옵션은 보내지 않습니다.
- **오프라인 동작** — DB나 AI 설정이 없어도 합성 예시 자료와 예제 응답으로 모든 화면이 열립니다.
- **PC 간 공유** — 서버 없이 DB 전체 SQL 파일로 자료를 주고받고, 공통 이력 기반 **자동 병합**과 삭제 개별 확인으로 충돌을 처리합니다.
- **멈추지 않는 UI** — DB 읽기 · AI 호출 · Excel 읽기는 모두 워커 스레드에서 실행하고, 백그라운드 읽기 중 GC로 인한 강제 종료까지 방지했습니다.

<div align="center">
<img src="docs/assets/slides/mentor-review.png" alt="멘토 평가" width="720"/>
</div>

---

## 🛠️ 기술 스택

### 🖥️ Desktop App
![Python](https://img.shields.io/badge/python_3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/pyside6_6.12-41CD52?style=for-the-badge&logo=qt&logoColor=white)
![xlwings](https://img.shields.io/badge/xlwings_(excel_com)-217346?style=for-the-badge&logo=microsoftexcel&logoColor=white)
![pandas](https://img.shields.io/badge/pandas-150458?style=for-the-badge&logo=pandas&logoColor=white)
![openpyxl](https://img.shields.io/badge/openpyxl-217346?style=for-the-badge)
![python-docx](https://img.shields.io/badge/python--docx-2B579A?style=for-the-badge)

### 🗄️ Database
![MySQL](https://img.shields.io/badge/mysql_5.6.19-4479A1?style=for-the-badge&logo=mysql&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/sqlalchemy_2.1-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white)
![PyMySQL](https://img.shields.io/badge/pymysql-4479A1?style=for-the-badge)

### 🤖 AI
![OpenAI SDK](https://img.shields.io/badge/openai_python_sdk-412991?style=for-the-badge&logo=openai&logoColor=white)
![GMS](https://img.shields.io/badge/ssafy_gms_(openai_호환)-0052CC?style=for-the-badge)

### 📦 Packaging · Test · Infra
![PyInstaller](https://img.shields.io/badge/pyinstaller_6-3776AB?style=for-the-badge)
![uv](https://img.shields.io/badge/uv-DE5FE9?style=for-the-badge&logo=uv&logoColor=white)
![pytest](https://img.shields.io/badge/pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white)
![Docker](https://img.shields.io/badge/docker_compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)

### 🤝 협업
![GitLab](https://img.shields.io/badge/gitlab-FC6D26?style=for-the-badge&logo=gitlab&logoColor=white)
![Jira](https://img.shields.io/badge/jira-0052CC?style=for-the-badge&logo=jira&logoColor=white)
![Figma](https://img.shields.io/badge/figma-F24E1E?style=for-the-badge&logo=figma&logoColor=white)
![Notion](https://img.shields.io/badge/notion-000000?style=for-the-badge&logo=notion&logoColor=white)
![Mattermost](https://img.shields.io/badge/mattermost-0058CC?style=for-the-badge&logo=mattermost&logoColor=white)

---

## 🏗️ 시스템 아키텍처

<div align="center">
<img src="docs/assets/architecture.png" alt="시스템 아키텍처" width="1000"/>
</div>

| 계층 | 설명 |
| --- | --- |
| **ui/** | PySide6 화면 모듈 약 110개. 공통 창(사이드바 · 조회 조건 패널)이 페이지를 소유하고, 각 화면은 처음 열 때 만듭니다. `features/`나 DB를 직접 호출하지 않습니다. |
| **features/** | 업무 로직 28개 패키지. Qt에 의존하지 않아 단위 테스트가 쉽고, 버전 비교 · 집계 · 위험 · 예측 · 추출 · 보고서가 여기에 있습니다. |
| **infrastructure/** | MySQL(SQLAlchemy · PyMySQL), AI(OpenAI 호환 클라이언트), Excel(xlwings COM), 설정 파일 읽기. 설정이 없으면 예시 공급자로 대체됩니다. |
| **DB** | 각 PC의 MySQL 5.6.19. 개발은 Docker Compose로 같은 버전을 띄우고, 실사용 PC는 Windows MySQL에 접속합니다. 코드는 환경변수만 다릅니다. |

### ERD

테이블 13개. 설비 현황은 **기준일별 스냅샷**으로 쌓여 리플레이와 추이에 쓰이고, 릴리즈 노트 → 버전 → Item → 모델별 적용 조건으로 이어집니다.

<div align="center">
<img src="docs/assets/erd.png" alt="ERD" width="1000"/>
</div>

---

## 📋 명세

기업 명세서를 바탕으로 Google Sheets에 요구사항 · 기능 명세를 정리하고, 멘토링마다 확인된 사항을 반영했습니다.

| 문서 | 규모 | 내용 |
| --- | --- | --- |
| 요구사항 명세서 | 62건 (REQ-001 ~ 063) | 프로젝트 목표 · 관리 범위 · 프로그램 환경 · 대시보드 · 위험 판단 · 버전 히트맵 · 공통 조회 · 버전 계산 · Excel 가져오기/검증 · 원본 데이터 · 아이템 매니저 · AI 분석 · 버전 이력 · 보고서 · 화면 개인화 |
| 기능 명세서 | 70건 (FUN-001 ~ 074) | 연결 요구 ID · 화면/영역 · 처리 순서 · 입력 · 출력 · 오류/예외 처리 · 명세 상태 · 담당 |

### 요구사항 명세서

<div align="center">
<img src="docs/assets/specs/requirements-preview.png" alt="요구사항 명세서 (1쪽)" width="1000"/>
</div>

<details>
<summary><b>🔍 요구사항 명세서 전체 보기 (REQ-001 ~ REQ-063)</b></summary>

<div align="center">
<img src="docs/assets/specs/requirements.png" alt="요구사항 명세서 전체" width="1000"/>
</div>

</details>

### 기능 명세서

<div align="center">
<img src="docs/assets/specs/functions-preview.png" alt="기능 명세서 (1쪽)" width="1000"/>
</div>

<details>
<summary><b>🔍 기능 명세서 전체 보기 (FUN-001 ~ FUN-074)</b></summary>

<div align="center">
<img src="docs/assets/specs/functions.png" alt="기능 명세서 전체" width="1000"/>
</div>

</details>

**핵심 정책**
- 목표 N(기준 버전)은 모델 · SW 계열별로 정하고, 이 버전 이상이면 목표 달성으로 판정합니다. 달성률 단위는 설비 대수가 아닌 **설비 · SW 계열 건**입니다.
- 버전은 같은 계열 안에서만, 릴리즈 노트에 기재된 순서로 비교합니다. 개조형(예: C4 DFB)은 원래 설비군의 릴리즈 노트를 사용합니다.
- 비교 불가 · 정보 부족 · 확인 필요는 0%로 계산하지 않고 따로 표시합니다. HPA 설비는 위험 분석에서 제외합니다.
- AI는 질문 해석과 답변 문장 작성에만 쓰고, 화면의 모든 숫자는 저장된 자료로 계산합니다.
- 가져오기 저장은 사람의 검토 완료 뒤에만 일어나며, 같은 호기 · 기준일의 재가져오기는 기존 항목을 갱신합니다.

---

## 📁 프로젝트 폴더 구조

```
.
├── src/setron/
│   ├── __main__.py            # 실행 진입점 (실행 파일은 옆의 setron.env를 먼저 읽음)
│   ├── ui/                    # PySide6 화면 모듈 약 110개 + assets/ (로고 · 아이콘 · 폰트)
│   ├── features/              # 업무 로직 28개 패키지
│   └── infrastructure/        # database.py · ai_client.py · excel_reader.py · env_file.py
├── database/                  # 스키마(테이블 13개) · 시연 시드 · DB 준비 스크립트
├── packaging/                 # PyInstaller 빌드 (build_windows.ps1 · setron.spec) · 시연 DB 도구
├── tests/                     # pytest 테스트 파일 340여 개 (MySQL 통합 테스트 포함)
├── docs/                      # 화면별 설계 문서 · 아키텍처 · 테스트 결과 · README 자산
├── exec/                      # 포팅 매뉴얼 · 외부 서비스 · DB 덤프 · 시연 시나리오
├── docker/mysql5619/          # 개발 · 테스트용 MySQL 5.6.19 이미지
├── ai/                        # 데이터 · AI 파트 실험 폴더 (파서 · 매칭 · 위험 · 예측 프로토타입)
├── compose.yaml · .env.example · setron.env.example
└── pyproject.toml · uv.lock · .python-version
```

<details>
<summary><b>🖥️ src/setron/ui — 화면</b></summary>

```
ui
├─ app.py                  # 화면 조립 · 공급자 연결 · 시작 창
├─ main_window.py          # 사이드바 · 페이지 등록 · 공통 조회 조건 패널
├─ dashboard*.py           # KPI · 히트맵 · 원형/막대/꺾은선 그래프
├─ heatmap*.py             # 버전 히트맵 · 그룹화 · 목록 보기
├─ equipment_drawer.py     # 설비 상세 사이드 패널
├─ replay*.py              # 버전 리플레이 재생
├─ item_*.py               # 아이템 매니저 · 위험 기준 등록
├─ chat_*.py               # AI 분석 대화 · 그래프 · 보고서 저장
├─ import_*.py             # Excel/SQL 가져오기 · 추출 · 검토 · 일괄 수정
├─ data_export_page.py     # Excel · SQL 내보내기
├─ data_management_page.py # 현황표 · 릴리즈 노트 · 변경 이력 편집
├─ report*.py              # 보고서 조립 · 미리보기 · Word/PDF
├─ appearance*.py          # 테마 · 다크 모드 · 글자 크기 · 색상
└─ components.py · theme.py · query_panel.py · drawer.py
```

</details>

<details>
<summary><b>⚙️ src/setron/features — 업무 로직</b></summary>

```
features
├─ excel_import/ rule_extraction/ extraction/ import_save/   # Excel 읽기 → 규칙/AI 추출 → 원본 대조 → 저장
├─ version/ aggregation/ targets/ history/                    # 버전 비교 · N 단계 판정 · 집계 · 목표 N 이력
├─ risk/ aging/ priority/ forecast/                           # 위험 기준 · 노후도 · 우선순위 · 통일 시점 예측
├─ ai/ chat/ analysis/ report/                                # 질문 계획 · 규칙 검증 · 범용 집계 · 보고서 조립
├─ item/ release/ equipment/                                  # Item · 릴리즈 노트 · 설비
├─ export/ db_export/ db_import/ peer_sync/ deletion/         # Excel/SQL 내보내기 · SQL 가져오기 · PC 간 병합 · 삭제
├─ data_admin/ management/ query/ query_options/ storage/     # 데이터 관리 · 조회 조건 · 공통 저장
└─ reference_data.py · production_area.py · management_format.py
```

</details>

<details>
<summary><b>🗄️ database · packaging · exec</b></summary>

```
database
├─ schema_mysql5619.sql    # 확정 스키마 (테이블 13개)
├─ seed.sql                # 시연 자료: 설비 300대 · 관측 1,800건
├─ SETRON_DB.sql           # 시연 DB 덤프
├─ init_local_db.py · reset_demo_db.py · init_demo_dump.py · init_docker_demo_dump.py
└─ build_seed.py · seed_*.py · demo_release_catalog.json

packaging
├─ build_windows.ps1 · setron.spec · run_setron.py   # dist/SETRON/SETRON.exe
└─ build_demo_db.py · build_docker_demo_db.py        # 시연 DB 도구

exec
├─ 포팅매뉴얼.md · 외부서비스.md · 시연시나리오.md
└─ setron_db_dump.sql
```

</details>

---

## 🚀 실행 방법

Windows, Python 3.13, [uv](https://docs.astral.sh/uv/), Docker Desktop(개발 DB용)이 필요합니다.

```powershell
uv sync --locked                                   # .venv 생성 · 의존성 설치
copy .env.example .env                             # DB 비밀번호 · AI 키 입력
docker compose build mysql-dev                     # MySQL 5.6.19 이미지 빌드 (최초 1회)
docker compose up -d --wait mysql-dev              # 개발 DB (127.0.0.1:13306)
.\prepare_docker_demo_db.bat                       # 스키마 + 시연 자료
uv run --locked --env-file .env python -m setron   # 앱 실행
```

DB 설정 없이 화면만 보려면 `uv run --locked python -m setron`으로 실행합니다. 합성 예시 자료로 시작합니다.

```powershell
uv run --locked pytest                 # 단위 테스트
.\packaging\build_windows.ps1          # dist\SETRON\SETRON.exe 빌드
```

AI 설정은 `.env`(개발) 또는 `setron.env`(실행 파일)에 `SETRON_AI_BASE_URL` · `SETRON_AI_API_KEY` · `SETRON_AI_MODEL` · `SETRON_AI_TIMEOUT`을 넣습니다. 자세한 빌드 · 배포 절차는 [exec/포팅매뉴얼.md](exec/포팅매뉴얼.md), 시연 흐름은 [exec/시연시나리오.md](exec/시연시나리오.md)를 참고합니다.

---

## 💛 팀원 소개

| <img src="https://github.com/BooGyya.png" width="120"/> | <img src="docs/assets/character.png" width="120"/> | <img src="https://github.com/alswjd131313.png" width="120"/> | <img src="https://github.com/1lights1.png" width="120"/> | <img src="https://github.com/2hnK.png" width="120"/> | <img src="https://github.com/Kim-jin-gwang.png" width="120"/> |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **김보경**<br/>([@BooGyya](https://github.com/BooGyya)) | 박호진 | 황민정<br/>([@alswjd131313](https://github.com/alswjd131313)) | 임세희<br/>([@1lights1](https://github.com/1lights1)) | 김지훈<br/>([@2hnK](https://github.com/2hnK)) | 김진광<br/>([@Kim-jin-gwang](https://github.com/Kim-jin-gwang)) |
| **PM · Frontend** | 팀장 · Data / AI | Frontend | Frontend | Backend | Backend |

---

## 🤝 협업 방식

- **브랜치 전략** — `master`(릴리스) / `develop`(통합) / `feature/*` · `fix/*` · `docs/*` · `chore/*`. 작업 브랜치 → `develop` MR → 검토 후 병합, 릴리스는 `develop → master` MR(merge commit). 7주간 커밋 540여 개.
- **커밋 컨벤션** — `type(scope): 한글 설명` 형식의 Conventional Commits (`feat(report): 보고서 내용 및 히트맵 묶음 선택 추가`).
- **이슈 관리** — Jira 스토리 단위로 스프린트를 운영하고, 1P = 1시간 기준으로 주당 목표 포인트를 관리했습니다.
- **멘토링** — SEMES 멘토님과 주 1회, 7회(10/07 · 10/14 · 10/21 · 10/28 · 11/04 · 11/11 · 11/16) 진행하며 확인된 사항은 [docs/mentor-confirmations.md](docs/mentor-confirmations.md)에 기록해 설계 기준으로 삼았습니다.
- **문서 주도 개발** — 화면마다 설계 문서(`docs/*.md`)를 쓰고 Figma 디자인(VER5)과 코드를 함께 갱신했습니다. 회의록 · 명세는 Notion과 Google Sheets에, 일상 소통은 Mattermost에서 진행했습니다. 테스트 파일 340여 개(MySQL 통합 테스트 39개)로 회귀를 막았습니다.
- **인계** — 포팅 매뉴얼 · 외부 서비스 · 시연 시나리오(`exec/`)와 개발 문서 · 사용자 가이드 · 인수인계서 3종을 고객사에 전달했습니다.

---

<div align="center">

<img src="docs/assets/character.png" alt="SETRON 캐릭터" width="160"/>

**SETRON** — SSAFY 15기 기업연계 프로젝트 B106 마블룰렛 × SEMES

</div>
