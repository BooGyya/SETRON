# Windows 실행 파일 만들기·설치·실행 안내

Jira S15P31B106-77. 폐쇄망 Windows PC에서 Python·uv 설치 없이 SETRON을 실행하기 위한 안내다.

## 1. 실행 파일 만들기 (개발 PC)

인터넷이 되는 개발 PC에서 저장소 최상위 폴더를 기준으로 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

- 결과는 `dist\SETRON\` 폴더다. `SETRON.exe`, 실행에 필요한 `_internal\` 폴더, 설정 예시 `setron.env.example`이 들어 있다.
- 폴더 전체(약 220MB)를 압축해 옮긴다. `SETRON.exe`만 옮기면 실행되지 않는다.
- 빌드 설정은 `packaging\setron.spec`, 시작 스크립트는 `packaging\run_setron.py`다. 화면 아이콘·폰트(`ui/assets`)와 MySQL 드라이버를 함께 묶는다.

## 2. 사용자 PC 준비

| 항목 | 내용 |
| --- | --- |
| Windows | Windows 10 이상 64비트 |
| Excel | 데스크톱 Excel. 데이터 가져오기는 사용자가 열어 둔 Excel 창에서 읽는다 |
| MySQL | 회사 PC에 설치된 MySQL 5.6.19. SETRON용 빈 DB와 계정을 만들어 둔다 |
| 스키마 | 빈 DB에 `database\schema_mysql5619.sql`을 HeidiSQL 등으로 실행한다 |
| 기준 자료 | 시연·확인 때만 `database\seed_demo_catalog.sql`을 실행한다 (확정 기준 자료 아님) |

다른 사용자에게 받은 DB 내보내기 파일(.sql)이 있으면 스키마 대신 그 파일을 빈 DB에서 실행한다. 내보내기 파일에 구조와 자료가 모두 들어 있다.

## 3. 설정 파일

`dist\SETRON\setron.env.example`을 같은 폴더에 `setron.env`로 복사하고 값을 채운다.

| 이름 | 내용 |
| --- | --- |
| `SETRON_DB_HOST`, `SETRON_DB_PORT` | MySQL 주소와 포트 (보통 `127.0.0.1`, `3306`) |
| `SETRON_DB_NAME`, `SETRON_DB_USER`, `SETRON_DB_PASSWORD` | SETRON용 DB 이름과 계정 |
| `SETRON_DB_CONNECT_TIMEOUT` | 접속 대기 초. 비우면 5초 |
| `SETRON_AI_*` | 회사 AI 접속 정보. 아직 화면에 연결하지 않았다 (예제 응답 사용) |

- 한 줄에 `이름=값` 하나. `#`로 시작하는 줄은 주석이다. 비밀번호의 `#`·공백은 그대로 쓴다.
- 실행 파일은 시작할 때만 `setron.env`를 읽는다. 이미 Windows 환경변수에 같은 이름이 있으면 그 값을 쓴다.
- `setron.env`에는 비밀번호가 들어 있으므로 다른 사람에게 폴더를 넘길 때는 빼고 넘긴다.

## 4. 실행

`SETRON.exe`를 더블클릭한다.

| 상황 | 화면 |
| --- | --- |
| `setron.env`가 없거나 `SETRON_DB_NAME`이 없음 | 합성 예시 자료로 시작. 가져오기 저장은 메모리, 내보내기 버튼 비활성 |
| DB 접속 성공 | 상단 안내 "MySQL DB 저장 자료". 가져오기 저장·조회·DB 전체 SQL 내보내기가 DB를 사용 |
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

남은 확인: 회사에서 쓰는 HeidiSQL 9.2로 내보내기 파일을 실행하는 것은 HeidiSQL이 없어 확인하지 못했다.

설치형 서버를 띄운 방법 (확인용, Windows 서비스로 등록하지 않음):

```powershell
# 압축 해제 폴더에 my.ini를 두고 콘솔로 실행. 끝낼 때는 이 창에서 Ctrl+C 또는 mysqladmin shutdown
bin\mysqld.exe --defaults-file=my.ini --console
```
