# Windows 실행 파일 만들기·설치·실행 안내

Python·uv 설치 없이 Windows에서 SETRON을 실행하는 방법이다.

## 1. 실행 파일 만들기 (개발 PC)

인터넷이 되는 개발 PC에서 저장소 최상위 폴더를 기준으로 실행한다.

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File packaging\build_windows.ps1 -DumpPath dist\SETRON\SETRON_DB.sql
```

- `-DumpPath`는 필수이며, 실제 배포할 완전한 SETRON SQL을 지정한다. 위 명령은 기존 300대 배포 SQL을 그대로 사용한다. 첫 빌드에서는 승인된 배포 SQL의 실제 경로를 지정한다. 생성용 `database\seed.sql`과 별도 53대 원천은 자동 선택하지 않는다.
- Windows 빌드 결과는 기본적으로 `dist\SETRON`에 모인다. `-OutputDir <경로>`를 지정하면 해당 폴더에 격리해 만들 수 있다. 상대 경로는 명령을 실행한 위치를 기준으로 해석한다.
- 앱 `SETRON.exe`, 시연 메뉴 도구 `SETRON_DB_INIT.exe`, 동일 메뉴를 호출하는 `prepare_demo_db.bat`, 외부 `SETRON_DB.sql`, 설정 예시 `setron.env.example`, 실행 라이브러리 `_internal`이 들어 있다.
- 기존 배포 폴더의 `setron.env`, `backups` 및 사용자 파일은 보존한다. 기존 외부 SQL이 지정한 dump와 다르면 교체 전에 중단한다. 빌드와 오프라인 SQL 검증을 마친 뒤 배포 폴더를 교체하며 실패하면 기존 배포본을 유지하거나 복구 경로를 안내한다.
- 앱과 초기화 도구를 전달할 때는 `dist\SETRON` 폴더 전체를 압축한다. 사용자는 ZIP을 풀고 폴더 안의 배치 파일과 앱을 실행할 수 있다.
- `packaging\setron.spec`는 GUI를 빌드한다. 이어서 `packaging\build_demo_db.py`가 명시한 SQL로 시연 메뉴 도구를 만들고 DB 연결 없이 검증한다. 이 빌드 과정은 실제 DB에 접속하지 않는다.

## 2. 사용자 PC 준비

| 항목 | 내용 |
| --- | --- |
| Windows | Windows 10 이상 64비트 |
| Excel | 데스크톱 Excel. 데이터 가져오기는 사용자가 열어 둔 Excel 창에서 읽는다 |
| MySQL | 같은 PC에서 Windows용 MySQL 5.6.19를 실행하고 SETRON용 DB와 계정을 준비한다 |

MySQL을 새로 설치했다면 관리자 계정으로 SETRON용 DB와 계정을 먼저 생성한다. 실행 도구는 계정이나 권한을 자동으로 만들지 않는다.

관리자는 SETRON 계정에 **대상 DB에 한정해** 다음 권한을 부여한다.

| 기능 | 필요한 권한 |
| --- | --- |
| 시연 데이터 넣기 | `SELECT, CREATE, DROP, INSERT, REFERENCES, TRIGGER` |
| 데이터 초기화 | `SELECT, DELETE, TRIGGER` |

도구는 변경 전에 필요한 권한을 검사한다. `TRIGGER`는 사용자 정의 트리거가 있는지 확인하는 데 필요하며, 검사 권한이 없거나 트리거가 있으면 중단한다.

## 3. 설정 파일

`SETRON.exe` 옆의 `setron.env.example`을 같은 폴더에 `setron.env`로 복사하고 값을 채운다. 앱과 초기화 도구 모두 이 파일을 읽는다. 빌드 직후 이 폴더는 `dist\SETRON`이다. 기존 Windows 설정 파일을 사용했다면 그 값을 `dist\SETRON\setron.env`로 옮긴다.

| 이름 | 내용 |
| --- | --- |
| `SETRON_DB_HOST`, `SETRON_DB_PORT` | MySQL 주소와 포트 (보통 `127.0.0.1`, `3306`) |
| `SETRON_DB_NAME`, `SETRON_DB_USER`, `SETRON_DB_PASSWORD` | SETRON용 DB 이름과 계정 |
| `SETRON_DB_CONNECT_TIMEOUT` | 접속 대기 초. 항목을 생략하면 5초 |
| `SETRON_AI_*` | 회사 AI 접속 정보. 있으면 데이터 가져오기 추출과 AI 분석이 회사 AI를 쓰고, 없으면 예제 응답을 쓴다 |

- 한 줄에 `이름=값` 하나. `#`로 시작하는 줄은 주석이다. 비밀번호 중간의 `#`·공백은 그대로 쓴다. 앞뒤 공백이 있는 비밀번호는 `SETRON_DB_PASSWORD='  password  '`처럼 전체를 따옴표로 감싼다.
- 앱과 초기화 도구는 `setron.env`를 읽는다. 같은 이름의 Windows 환경변수가 있으면 환경변수 값이 우선한다. 초기화 도구가 표시하는 대상 DB를 확인한 뒤 진행한다.
- `setron.env`에는 비밀번호가 들어 있으므로 다른 사람에게 폴더를 넘길 때는 빼고 넘긴다.

## 4. 실행

1. ZIP을 풀고 `setron.env`에 미리 준비한 로컬 DB의 접속 정보를 입력한다.
2. SETRON 앱과 다른 DB 프로그램을 닫고, 같은 폴더의 `prepare_demo_db.bat` 또는 `SETRON_DB_INIT.exe`를 실행한다. 두 파일의 메뉴와 안내는 같다. 작업 중 다른 프로그램에서 데이터를 저장하지 않는다.
3. 같은 폴더의 `SETRON.exe`를 실행해 데이터를 조회한다.

| 선택 | 동작 |
| --- | --- |
| 1. 시연 데이터 넣기 | 동봉한 SQL의 설비 300대·관측 1,800건을 포함한 시연 데이터를 넣는다. 기존 관리 테이블은 교체한다 |
| 2. 데이터 초기화 | 관리 테이블 13개의 구조를 유지하고 모든 데이터를 삭제한다 |
| 3. 종료 | DB를 변경하지 않고 종료한다 |

기존 관리 테이블을 교체하거나 초기화할 때는 대상 DB와 작업 내용을 표시한 뒤 `Y`를 입력해야 진행한다. Enter·`N`은 취소다. 기존 데이터는 실행 전에 같은 폴더의 `backups`에 저장한다. 빈 DB에 처음 넣을 때는 바로 진행한다. 다른 DB와 관리 대상 밖의 테이블은 변경하지 않는다.

삽입·삭제에 실패하면 화면에 표시된 MySQL 오류와 계정·권한 안내를 확인한다. 관리자에게 대상 DB의 계정 생성 여부와 필요한 권한을 확인하도록 요청한다. SQL 파일만 검사하려면 `SETRON_DB_INIT.exe --validate-dump`를 실행한다. 이 검사는 DB를 변경하지 않는다.

작업 중 강제 종료되면 다음 실행에서 미완료 작업을 안내하고 새 삽입·삭제를 중단한다. 표시된 백업 SQL과 작업 상태 파일을 보존한 뒤 관리자에게 복구를 요청한다. 상태 파일은 DB 복구·검증이 끝난 뒤 화면 안내에 따라 정리한다. 연결이 끊겨 완료 여부를 확인하지 못한 경우에도 바로 재시도하지 않고 DB 상태부터 확인한다.

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
