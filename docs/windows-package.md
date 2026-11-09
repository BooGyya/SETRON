# Windows 실행 파일 만들기·설치·실행 안내

Jira S15P31B106-77. 폐쇄망 Windows PC에서 Python·uv 설치 없이 SETRON을 실행하기 위한 안내다.

## 1. 실행 파일 만들기 (개발 PC)

인터넷이 되는 개발 PC에서 저장소 최상위 폴더를 기준으로 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

- Windows 빌드 결과는 `dist\SETRON`에 모인다. 앱 `SETRON.exe`, 초기화 도구 `SETRON_DB_INIT.exe`, 루트에서 복사한 `init_local_db.bat`, 설정 예시 `setron.env.example`, 그리고 실행 라이브러리와 초기화 SQL을 담은 `_internal` 폴더가 들어 있다.
- 앱과 초기화 도구를 전달할 때는 `dist\SETRON` 폴더 전체를 압축한다. 사용자는 ZIP을 풀고 폴더 안의 배치 파일과 앱을 실행할 수 있다.
- 빌드 설정은 `packaging\setron.spec`다. 화면 아이콘·폰트(`ui/assets`), MySQL 드라이버, 스키마와 시드 SQL을 함께 묶는다.

## 2. 사용자 PC 준비

| 항목 | 내용 |
| --- | --- |
| Windows | Windows 10 이상 64비트 |
| Excel | 데스크톱 Excel. 데이터 가져오기는 사용자가 열어 둔 Excel 창에서 읽는다 |
| MySQL | 초기화 전에 같은 PC에서 Windows용 MySQL 5.6.19를 실행하고 SETRON용 빈 DB와 계정을 준비한다. 계정에는 대상 DB의 테이블 생성·참조와 데이터 조회·입력 권한이 필요하다 |

개발 PC에서는 Windows 패키지를 빌드한 뒤 저장소 루트의 `init_local_db.bat`을 실행할 수 있다. 루트에서 실행하면 배치 파일이 `dist\SETRON`에 있는 초기화 도구를 찾는다.

시연·확인 전에 DB를 빈 상태로 다시 만들려면 개발 PC에서 아래를 실행한다. 관리 테이블 13개를 지우고 스키마와 시연용 기준 자료를 다시 넣는다. 대상 DB의 SETRON 자료가 모두 지워지므로 `--confirm`에 접속 설정의 DB 이름을 한 번 더 적어야 실행된다.

```powershell
uv run --locked --env-file dist/SETRON/setron.env python database/reset_demo_db.py --confirm <SETRON_DB_NAME 값>
```

다른 사용자에게 받은 DB 내보내기 파일(.sql)이 있으면 스키마 대신 그 파일을 빈 DB에서 실행한다. 내보내기 파일에 구조와 자료가 모두 들어 있다. HeidiSQL에서는 빈 DB를 선택한 뒤 파일을 불러와 실행한다(9.2에서 확인).

## 3. 설정 파일

`SETRON.exe` 옆의 `setron.env.example`을 같은 폴더에 `setron.env`로 복사하고 값을 채운다. 앱과 초기화 도구 모두 이 파일을 읽는다. 빌드 직후 이 폴더는 `dist\SETRON`이다. 기존 Windows 설정 파일을 사용했다면 그 값을 `dist\SETRON\setron.env`로 옮긴다.

| 이름 | 내용 |
| --- | --- |
| `SETRON_DB_HOST`, `SETRON_DB_PORT` | MySQL 주소와 포트 (보통 `127.0.0.1`, `3306`) |
| `SETRON_DB_NAME`, `SETRON_DB_USER`, `SETRON_DB_PASSWORD` | SETRON용 DB 이름과 계정 |
| `SETRON_DB_CONNECT_TIMEOUT` | 접속 대기 초. 비우면 5초 |
| `SETRON_AI_*` | 회사 AI 접속 정보. 있으면 데이터 가져오기 추출과 AI 분석이 회사 AI를 쓰고, 없으면 예제 응답을 쓴다 |

- 한 줄에 `이름=값` 하나. `#`로 시작하는 줄은 주석이다. 비밀번호의 `#`·공백은 그대로 쓴다.
- 앱은 시작할 때 `setron.env`를 읽으며 이미 Windows 환경변수에 같은 이름이 있으면 그 값을 쓴다. 초기화 도구는 설정 파일에 적힌 DB에 접속한다.
- `setron.env`에는 비밀번호가 들어 있으므로 다른 사람에게 폴더를 넘길 때는 빼고 넘긴다.

## 4. 실행

1. ZIP을 풀고 `setron.env`에 미리 준비한 로컬 DB의 접속 정보를 입력한다.
2. 압축을 푼 폴더에서 `init_local_db.bat`을 실행한다. 관리 테이블 13개와 시연 설비 300대·관측 데이터 1,800건, 시연 목표 N(모델 적용 근거가 있는 모델·계열 11쌍, 최신 바로 아래 버전)을 적용한다. 위험 규칙은 넣지 않는다(시연에서 기준·설정 화면으로 등록). 시연 데이터가 적용된 상태에서 다시 실행하면 중복 없이 기존 적용 상태를 확인한다.
3. 같은 폴더의 `SETRON.exe`를 실행해 데이터를 조회한다.

스키마만 준비하려면 명령 프롬프트에서 `init_local_db.bat --no-seed`를 실행한다.

| 상황 | 화면 |
| --- | --- |
| 파일과 기존 환경변수에서 `SETRON_DB_NAME`을 설정하지 않음 | 합성 예시 자료로 시작. 가져오기 저장은 메모리, 내보내기 버튼 비활성 |
| DB 접속 성공 | 상단 안내 "MySQL DB 저장 자료". 가져오기 저장·조회·위험 판정·내보내기(관리 데이터 Excel, DB 전체 SQL)가 DB를 사용 |
| DB 접속 실패 | 합성 예시 자료로 시작하고 상단에 "DB 자료를 읽지 못해…" 안내 |

## 5. 설치형 MySQL 실사용 확인

S15P31B106-31, -63의 남은 확인 항목이다. 2026-11-03 개발 PC에서 공식 ZIP 버전(`mysql-5.6.19-winx64.zip`)을 콘솔로 실행해 확인했다.

| 확인 | 결과 |
| --- | --- |
| 서버 설정 | `my.ini`에 utf8mb4·utf8mb4_unicode_ci 지정 (Docker와 같음). Windows 기본 `lower_case_table_names=1`이며 테이블 이름이 모두 소문자라 영향 없음 |
| 스키마·시드 적용 | `mysql.exe`로 `schema_mysql5619.sql`, `seed_demo_catalog.sql` 실행, 테이블 13개 |
| `setron.env`로 접속 | 실행 파일이 DB 모드("MySQL DB 저장 자료")로 시작 |
| 대시보드·HeatMap 조회 | 저장 후 조회 적용 시 기준 관측일·현황 건수 반영 |
| 가져오기 저장, 같은 날 재저장 | 설비 현황·릴리즈 저장 완료, 같은 호기·기준일 재저장은 거부 |
| 저장 실패 시 전체 취소 | 실패한 저장 2번의 입력 작업이 DB에 남지 않음 |
| MySQL 통합 테스트 | `tests/*_mysql.py` 9개 파일 94개를 설치형 서버의 빈 테스트 DB에서 실행, 모두 통과 |
| SQL 내보내기 → 빈 DB 복원 | 테이블 13개 · 32행 내보내기 → 새 빈 DB에서 `mysql.exe`로 실행 → 테이블별 행 수·`CHECKSUM TABLE` 일치. 같은 DB에 다시 실행하면 첫 `CREATE TABLE`에서 멈춤 |
| HeidiSQL 9.2로 복원 | 공식 포터블 `HeidiSQL_9.2_Portable.zip`. 빈 DB를 선택하고 SQL 파일을 불러와 실행(인코딩 기본값) → 27개 문장 · 36행 · 경고 0. 한글·이모지·작은따옴표·역슬래시가 든 값까지 테이블별 행 수·`CHECKSUM TABLE`과 AUTO_INCREMENT가 원본과 일치 |
| HeidiSQL로 같은 DB에 다시 실행 | "SQL 오류 (1050): Table 'import_batch' already exists" 창(확인 버튼만 있음)을 띄우고 멈춤. 자료 변화 없음 |

받은 파일은 HeidiSQL에서 **빈 DB를 먼저 선택한 뒤** 실행하도록 안내한다. 다른 DB가 선택된 상태면 그 DB에 테이블이 만들어진다.

설치형 서버를 띄운 방법 (확인용, Windows 서비스로 등록하지 않음):

```powershell
# 압축 해제 폴더에 my.ini를 두고 콘솔로 실행. 끝낼 때는 이 창에서 Ctrl+C 또는 mysqladmin shutdown
bin\mysqld.exe --defaults-file=my.ini --console
```
