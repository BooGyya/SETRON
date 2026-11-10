-- SEMES: 13개 테이블 논리 ERD의 MySQL 5.6.19용 신규 스키마
-- 기준: 2026-10-27 현재 ERDCloud Snapshot / SEMES_수정안.mmd / 요구사항·기능·백엔드 작업표
-- 멘토 답변과 팀 확정: 호기 코드 전역 고유, C3는 같은 UI 안에서 형상 무관 공유,
-- C4 Plus=C4 노트·O5 Prime=O5 노트 포함, 같은 계열 PM 버전 차이 보존·예외 집계.
-- 문자셋: utf8mb4 / 저장 엔진: InnoDB
-- 실행 전 대상의 빈 데이터베이스를 선택한다. CREATE DATABASE / USE는 포함하지 않는다.
-- 기존 테이블을 삭제하거나 IF NOT EXISTS로 기존 정의의 차이를 숨기지 않는다.
-- DDL은 암묵적으로 COMMIT되므로, 스키마 생성 전체가 하나의 트랜잭션인 것은 아니다.
-- 운영 데이터의 가져오기·교체는 애플리케이션에서 별도 트랜잭션으로 처리한다.
-- 스키마 초기화 권한: CREATE, REFERENCES, INSERT. 앱 계정에는 별도로 SELECT/INSERT/UPDATE/DELETE를 부여한다.
-- HeidiSQL에서 대상 DB를 선택한 뒤 UTF-8로 파일 전체를 실행한다. 트리거와 DELIMITER는 사용하지 않는다.

-- [2026-10-27 확인 대기 해소: 아래 업무 규칙은 확정됨]
-- 1. C3는 같은 UI 안에서 5각·6각과 관계없이 릴리즈 노트·버전 계열을 공유한다.
--    구UI·통합UI는 서로 다른 계열·목표 N을 사용하며 형상만으로 계열을 나누지 않는다.
-- 2. HPA를 포함해 호기·SW 버전 등 필수값 공란은 저장 오류로 처리한다.
--    가져오기 검토 화면에서 파일·시트·행·열과 사유를 안내하고 보완·재검증한다.
--    오류가 남으면 이번 저장 묶음 전체를 보류한다. 임의 값·공란 저장 예외는 제공하지 않는다.
--    Memory/Foundry 등 선택 항목의 공란 허용은 유지한다.
-- 3. 목표 N 후보는 해당 모델·계열에 적용 가능한 것으로 확인된 버전만 제공한다.
--    후보 목록의 저장·조회는 기존 기능 원칙에 따른 구현 작업이며 추가 업무 결정 대기가 아니다.
-- [정보 수신·자료·구현 검증]
-- 회사 AI API 주소·모델·인증·호출 제한은 수신 대기다. 예제 응답 검증과 실제 연결은 구분한다.
-- 공유 노트의 모델별 적용 버전·Item·파생 계열·분기 순서는 실제 릴리즈로 검증한다.
-- 미확인 모델·버전 대응을 임의 연결하지 않으며, 확인된 대응으로 N 후보를 선별한다.
-- 편집 가능한 Excel 트리맵 생성·호환성은 구현 검증 대상이며 DB 제약으로 다루지 않는다.
-- 다운그레이드 경고와 관리용 Item 숨김은 보류한다.

-- [업무 서비스에서 검증해야 할 확정 규칙]
-- A. 전역 고유 호기 코드로 같은 설비를 찾고 날짜별 소속·모델을 저장한다.
--    필수 정보·원본 위치를 검사하고 가져오기·직접 수정 묶음 전체를 저장/취소한다.
-- B. equipment_version.family_id와 연결 release_version.family_id는 같아야 한다.
--    risk_rule의 직접/시작/해제 버전도 지정한 계열·확인된 흐름에 속해야 한다.
--    다른 테이블을 조회하는 검증은 CHECK로 표현할 수 없어 서비스 트랜잭션에서 검사한다.
-- C. LISTED의 target_group_ids는 실제 equipment_group ID여야 하며 적용 계열도 검사한다.
--    JSON 안의 ID에는 일반 외래키를 걸 수 없다. 모델 삭제 전 JSON 참조도 검사한다.
-- D. source_reference는 필드명별 {sheet, cells} 객체다. 각 행의 batch_id로 원본을 찾는다.
--    설비 현황·SW·PM의 같은 필드가 여러 시트에 있으면 {sheet, cells} 목록으로 모두 보존한다.
--    원본 raw_data와 가져오기 일시는 저장 후 덮어쓰지 않는다.
-- E. edited_fields는 사용자가 직접 수정한 필드명을 기록하며 원본과 분리한다.
--    기존 Excel 행을 직접 수정할 때는 원래 batch_id·source_reference를 유지한다.
--    새 자료를 직접 등록할 때만 MANUAL 입력 작업을 만든다.
-- F. 완전한 계열별 노트를 검토한 뒤 교체한다. 다른 계열과 공유 Item을 함께 삭제하지 않는다.
--    노트 삭제는 RESTRICT이므로 필요한 Item·릴리즈만 명시적으로 정리한다.
-- G. 형식이 올바른 미등록 버전은 원문·계열을 보존하고 release_version_id=NULL로 저장한다.
--    등록·교체 후 확인된 대상만 다시 연결하고 N·Item·위험 판정을 다시 계산한다.
-- H. Item은 실제 후속 관계와 제외 조건을 반영해 연결한다. version_order만으로 일괄 상속하지 않는다.
-- I. 같은 버전으로 확인된 별칭만 정규 릴리즈 ID에 연결한다. 철자·구분자를 일괄 치환하지 않는다.
-- J. 위험 지정 시 target_reference에 계열·원본 Item 번호·버전 원문 등 재연결 근거를 저장한다.
--    VERIFIED는 대상·범위를 확인한 뒤에만 설정하며 UNRESOLVED를 비위험으로 처리하지 않는다.
-- K. 주의·위험 기간의 음수·역전 등은 설정 저장 시 검사한다.
-- L. 같은 현황·계열·버전 원문의 정확한 중복은 한 행으로 합치고 PM 연결·원본 위치를 보존한다.
--    서로 다른 PM의 버전 차이는 허용한다. UNIQUE(snapshot_id, family_id)는 사용하지 않는다.
--    같은 설비·계열에서 하나라도 N 미만이면 미달, 모두 확인·N 이상이면 부합, 그 외 판단 불가다.
--    집계는 설비·계열당 1건이다. 같은 계열 PM 불일치는 예외 표시하되 집계에 포함한다.
--    O2·O3처럼 다른 계열의 정상 차이는 PM 불일치로 판정하지 않는다.
-- M. 원형·개조형의 N은 모델+계열별 조회·AI 분석 조건이다. 영구 기준 N 컬럼을 두지 않는다.
--    확인된 적용 버전만 N 후보로 제시하고 등록·수정·교체 후 선택한 N의 유효성을 재검증한다.
-- N. C3는 구UI·통합UI 각각의 UI 안에서 형상과 관계없이 같은 릴리즈·계열을 사용한다.
--    구UI와 통합UI는 서로 다른 계열로 관리한다.
--    C4 Plus·O5 Prime의 원형 노트 공유만으로 모든 버전·Item·순서를 공통 적용하지 않는다.
-- O. 과거 조회는 정확히 같은 기준일의 현황·PM 구성을 사용한다. 없는 날짜는 채우지 않는다.
--    날짜 간 같은 PM은 equipment_id+pm_name으로 연결하며 실물 모듈 교체를 추적하지 않는다.

-- MySQL 5.6.19 호환 처리:
-- JSON은 LONGTEXT에 저장하고 앱에서 구조와 값을 검사한다. edited_fields의 기본값 []도 앱에서 채운다.
-- 5.6에서 적용되지 않는 CHECK는 제거하고 기존 검증 규칙을 앱의 StorageService로 옮겼다.
-- 원문을 정확히 구분해야 하는 equipment_code/version_raw/pm_name/setting_key는 UTF-8 VARBINARY로 저장한다.
-- 5.6의 일반 문자열 비교는 뒤쪽 공백을 무시하므로, 서로 다른 원문이 같은 값으로 처리되는 것을 막기 위함이다.
-- 앱 저장 계층에서 문자열을 UTF-8 바이트로 변환하고 조회할 때 다시 문자열로 복원한다.
-- HeidiSQL에서 직접 수정·삭제하면 앱의 입력 검증과 삭제 전 위험 근거 보존을 거치지 않는다.
-- Windows 앱 접속은 환경변수로 설정하며 Docker는 개발·테스트에만 사용한다.

SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 01. 데이터 입력 작업 이력: 파일명·검토 대기/반려 상태는 저장하지 않는다.
CREATE TABLE import_batch (
    batch_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '입력 작업 ID',
    source_type VARCHAR(20) COLLATE utf8mb4_bin NOT NULL COMMENT '입력 방식: EXCEL 또는 MANUAL',
    imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'DB 반영 입력 일시',
    raw_data LONGTEXT NULL COMMENT '시트명·셀 주소별 원본값; 직접 입력은 NULL 허용',
    PRIMARY KEY (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='데이터 입력 작업 이력';

-- 02. 설비군·모델: 원형과 파생형의 관계를 유지한다.
CREATE TABLE equipment_group (
    group_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '설비군·모델 ID',
    group_name VARCHAR(50) NOT NULL COMMENT '표기를 통일한 설비군·모델명',
    category VARCHAR(10) COLLATE utf8mb4_bin NOT NULL COMMENT '최상위 분류: POLY/OXIDE/KCTC/HPA',
    parent_group_id INT UNSIGNED NULL COMMENT '기본 모델 ID; 기본형은 NULL',
    analysis_enabled BOOLEAN NOT NULL DEFAULT TRUE COMMENT '공통 분석 포함 여부; HPA는 FALSE',
    PRIMARY KEY (group_id),
    KEY ix_equipment_group_parent (parent_group_id),
    KEY ix_equipment_group_category (category),
    CONSTRAINT fk_equipment_group_parent FOREIGN KEY (parent_group_id)
        REFERENCES equipment_group (group_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='설비군·모델 및 원형·파생형 관계';

-- 자기 자신을 부모로 지정하거나 부모 관계가 순환하지 않는지도 저장 서비스에서 검사한다.

-- 03. 실제 설비: 공통부 기준으로 한 호기를 식별한다.
CREATE TABLE equipment (
    equipment_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '실제 호기 ID; 개조 전후에도 유지',
    equipment_code VARBINARY(200) NOT NULL COMMENT '설비호기 코드; 모든 사업장에서 고유, 사업장별 중복 생성 금지',
    PRIMARY KEY (equipment_id),
    UNIQUE KEY uq_equipment_code (equipment_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='실제 설비; 호기 코드는 전체 사업장에서 고유';

-- 04. 버전 계열: C3 구UI·통합UI는 서로 다른 행으로 관리한다.
CREATE TABLE version_family (
    family_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '버전 계열 ID',
    group_id INT UNSIGNED NOT NULL COMMENT '계열 관리 모델 ID; 실제 적용 모델 전체 목록을 뜻하지 않음',
    family_name VARCHAR(50) NOT NULL COMMENT '버전 계열명; C3 구UI·통합UI 분리, 같은 UI 안에서는 형상 무관 공유',
    ui_type VARCHAR(20) COLLATE utf8mb4_bin NULL COMMENT 'UI 유형: LEGACY/INTEGRATED/SMART; 미확인은 NULL',
    PRIMARY KEY (family_id),
    KEY ix_version_family_group (group_id),
    CONSTRAINT fk_version_family_group FOREIGN KEY (group_id)
        REFERENCES equipment_group (group_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='버전 계열; 기준 N은 조회 조건으로 전달';

-- 05. 릴리즈 노트: 하나의 문서를 여러 버전·파생형이 공유할 수 있다.
CREATE TABLE release_note (
    release_note_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '릴리즈 노트 ID',
    group_id INT UNSIGNED NOT NULL COMMENT '문서 관리 모델 ID; C4 Plus는 C4, O5 Prime은 O5 노트에 포함',
    release_date DATE NULL COMMENT '문서 발행일; 미확인은 NULL',
    batch_id INT UNSIGNED NOT NULL COMMENT '문서 정보의 입력 작업 ID',
    PRIMARY KEY (release_note_id),
    KEY ix_release_note_group (group_id),
    KEY ix_release_note_batch (batch_id),
    CONSTRAINT fk_release_note_group FOREIGN KEY (group_id)
        REFERENCES equipment_group (group_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_release_note_batch FOREIGN KEY (batch_id)
        REFERENCES import_batch (batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='릴리즈 노트 문서';

-- 06. 릴리즈 버전
-- 계열+정확히 같은 버전 원문의 중복만 막는다. 미확인 별칭·대소문자를 임의 통합하지 않는다.
CREATE TABLE release_version (
    release_version_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '릴리즈 버전 ID',
    release_note_id INT UNSIGNED NULL COMMENT '릴리즈 노트 ID; 문서 없이 직접 등록하면 NULL',
    family_id INT UNSIGNED NOT NULL COMMENT '버전 계열 ID',
    version_raw VARBINARY(400) NOT NULL COMMENT '릴리즈 SW 버전 원문; 핫픽스 표기 포함',
    version_suffix VARCHAR(50) COLLATE utf8mb4_bin NULL COMMENT '파생 접미사: _DFB=DFB, _DEX=Plus 등',
    released_at DATE NULL COMMENT 'SW 버전 출시일; 미확인은 NULL',
    version_order INT NULL COMMENT '같은 계열의 확인된 비교 순서; 미확인은 NULL, 원본 No 차이와 구분',
    item_mapping_status VARCHAR(20) COLLATE utf8mb4_bin NOT NULL DEFAULT 'UNKNOWN' COMMENT '항목 매핑 확인 상태: UNKNOWN/VERIFIED',
    batch_id INT UNSIGNED NOT NULL COMMENT '이 릴리즈 버전 정보의 입력 작업 ID',
    source_reference LONGTEXT NULL COMMENT '필드별 원본 시트명·셀 주소; No 등 정렬 근거 포함',
    edited_fields LONGTEXT NOT NULL COMMENT '사용자가 직접 수정한 필드명 목록',
    PRIMARY KEY (release_version_id),
    UNIQUE KEY uq_release_version_family_raw (family_id, version_raw),
    KEY ix_release_version_note (release_note_id),
    KEY ix_release_version_order (family_id, version_order),
    KEY ix_release_version_batch (batch_id),
    CONSTRAINT fk_release_version_note FOREIGN KEY (release_note_id)
        REFERENCES release_note (release_note_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_release_version_family FOREIGN KEY (family_id)
        REFERENCES version_family (family_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_release_version_batch FOREIGN KEY (batch_id)
        REFERENCES import_batch (batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='릴리즈 SW 버전 및 계열별 확인된 순서';

-- version_order에는 UNIQUE를 두지 않는다. 별도 흐름·순서가 확인되지 않은 데이터에 순서를 강제하지 않는다.

-- 07. 변경 항목
-- 원본 Item 번호는 버전·계열에 따라 반복될 수 있으므로 문서+번호 UNIQUE를 두지 않는다.
CREATE TABLE item (
    item_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '변경 항목 ID',
    release_note_id INT UNSIGNED NULL COMMENT '릴리즈 노트 ID; 원본에 없는 수동 Item은 NULL 허용',
    item_no INT NULL COMMENT '원본 Item 번호; 누락·재배치는 매칭 확인 대상',
    content TEXT NOT NULL COMMENT '변경 내용',
    batch_id INT UNSIGNED NOT NULL COMMENT '이 Item 정보의 입력 작업 ID',
    source_reference LONGTEXT NULL COMMENT '필드별 원본 시트명·셀 주소; 직접 입력은 NULL 허용',
    edited_fields LONGTEXT NOT NULL COMMENT '사용자가 직접 수정한 필드명 목록',
    PRIMARY KEY (item_id),
    KEY ix_item_note_number (release_note_id, item_no),
    KEY ix_item_batch (batch_id),
    CONSTRAINT fk_item_note FOREIGN KEY (release_note_id)
        REFERENCES release_note (release_note_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_item_batch FOREIGN KEY (batch_id)
        REFERENCES import_batch (batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='릴리즈 노트의 변경 항목';

-- 08. 기준일별 설비 현황
CREATE TABLE equipment_snapshot (
    snapshot_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '기준일별 설비 현황 ID',
    equipment_id INT UNSIGNED NOT NULL COMMENT '실제 호기 ID',
    base_date DATE NOT NULL COMMENT 'Excel 현황 기준일; 입력 일시와 구분',
    site VARCHAR(50) NOT NULL COMMENT '해당 기준일의 사업장',
    line VARCHAR(50) NULL COMMENT '해당 기준일의 라인; 필수 여부는 자료 검증 규칙에 따름',
    model_name VARCHAR(255) NOT NULL COMMENT '원본 설비 모델명; 혼합형 표기 보존',
    group_id INT UNSIGNED NULL COMMENT '확인된 대표 모델 ID; 혼합형은 NULL 허용',
    production_area VARCHAR(20) COLLATE utf8mb4_bin NULL COMMENT '선택 속성 MEMORY/FOUNDRY; NULL은 미확인, 모델 소속 제한 금지',
    structure_type VARCHAR(50) NULL COMMENT 'INLINE/CLUSTER 등 장비 구조',
    geometry_type VARCHAR(50) NULL COMMENT '5각/6각 등 형상 원문; C3는 같은 UI 안에서 형상과 무관하게 릴리즈·계열 공유',
    modification_type VARCHAR(100) NULL COMMENT 'CIP 등 개조 유형; 없으면 NULL',
    batch_id INT UNSIGNED NOT NULL COMMENT '이 현황 정보의 입력 작업 ID',
    source_reference LONGTEXT NULL COMMENT '필드별 원본 시트명·셀 주소',
    PRIMARY KEY (snapshot_id),
    UNIQUE KEY uq_equipment_snapshot_date (equipment_id, base_date),
    KEY ix_equipment_snapshot_date_site_line (base_date, site, line),
    KEY ix_equipment_snapshot_group_date (group_id, base_date),
    KEY ix_equipment_snapshot_batch (batch_id),
    CONSTRAINT fk_equipment_snapshot_equipment FOREIGN KEY (equipment_id)
        REFERENCES equipment (equipment_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_equipment_snapshot_group FOREIGN KEY (group_id)
        REFERENCES equipment_group (group_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_equipment_snapshot_batch FOREIGN KEY (batch_id)
        REFERENCES import_batch (batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='기준일별 설비 소속·모델·개조 상태';

-- 09. 설비 적용 버전 이력
CREATE TABLE equipment_version (
    equipment_version_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '설비 적용 버전 이력 ID',
    snapshot_id INT UNSIGNED NOT NULL COMMENT '기준일별 설비 현황 ID',
    family_id INT UNSIGNED NOT NULL COMMENT '적용 버전 계열 ID; 미등록 버전도 보존',
    version_raw VARBINARY(400) NOT NULL COMMENT '실제 적용 SW 버전 원문; 같은 계열 PM의 드문 차이도 보존',
    release_version_id INT UNSIGNED NULL COMMENT '확인된 릴리즈 버전 ID; 미등록·삭제 시 NULL',
    batch_id INT UNSIGNED NOT NULL COMMENT '이 적용 버전 정보의 입력 작업 ID',
    source_reference LONGTEXT NULL COMMENT '필드별 원본 시트명·셀 주소',
    PRIMARY KEY (equipment_version_id),
    -- 아래 UNIQUE는 PM의 동일 현황 복합 FK용이다. 설비·계열당 버전 수를 제한하지 않는다.
    UNIQUE KEY uq_equipment_version_snapshot_id (snapshot_id, equipment_version_id),
    UNIQUE KEY uq_equipment_version_snapshot_family_raw (snapshot_id, family_id, version_raw),
    KEY ix_equipment_version_family_raw (family_id, version_raw),
    KEY ix_equipment_version_release (release_version_id),
    KEY ix_equipment_version_batch (batch_id),
    CONSTRAINT fk_equipment_version_snapshot FOREIGN KEY (snapshot_id)
        REFERENCES equipment_snapshot (snapshot_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_equipment_version_family FOREIGN KEY (family_id)
        REFERENCES version_family (family_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_equipment_version_release FOREIGN KEY (release_version_id)
        REFERENCES release_version (release_version_id) ON DELETE SET NULL ON UPDATE RESTRICT,
    CONSTRAINT fk_equipment_version_batch FOREIGN KEY (batch_id)
        REFERENCES import_batch (batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='설비 적용 SW 버전 이력; 여러 PM이 공유 가능';

-- 10. 기준일별 PM 구성
CREATE TABLE equipment_pm (
    pm_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '기준일별 PM 구성 ID',
    snapshot_id INT UNSIGNED NOT NULL COMMENT '소속 설비 현황 ID',
    equipment_version_id INT UNSIGNED NULL COMMENT 'PM 적용 버전 ID; 같은 값은 공유, 드문 버전 차이는 별도 연결, 미확인은 NULL',
    pm_name VARBINARY(200) NOT NULL COMMENT 'PM1/PM2 등 설비 내 고정 위치 번호; 날짜 간 위치 연결에 사용',
    group_id INT UNSIGNED NOT NULL COMMENT 'PM 모델 ID; O2/O3 등 실제 입력에 따라 연결',
    model_name_raw VARCHAR(255) NULL COMMENT '원본 PM 모델명',
    batch_id INT UNSIGNED NOT NULL COMMENT '이 PM 정보의 입력 작업 ID',
    source_reference LONGTEXT NULL COMMENT '필드별 원본 시트명·셀 주소',
    PRIMARY KEY (pm_id),
    UNIQUE KEY uq_equipment_pm_name (snapshot_id, pm_name),
    KEY ix_equipment_pm_snapshot_version (snapshot_id, equipment_version_id),
    KEY ix_equipment_pm_group (group_id),
    KEY ix_equipment_pm_batch (batch_id),
    CONSTRAINT fk_equipment_pm_snapshot FOREIGN KEY (snapshot_id)
        REFERENCES equipment_snapshot (snapshot_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    -- 복합 FK로 다른 호기·다른 기준일의 적용 버전에 연결되는 것을 차단한다.
    -- SET NULL은 snapshot_id까지 NULL로 만들 수 있으므로 RESTRICT를 사용한다.
    CONSTRAINT fk_equipment_pm_snapshot_version FOREIGN KEY (snapshot_id, equipment_version_id)
        REFERENCES equipment_version (snapshot_id, equipment_version_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_equipment_pm_group FOREIGN KEY (group_id)
        REFERENCES equipment_group (group_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_equipment_pm_batch FOREIGN KEY (batch_id)
        REFERENCES import_batch (batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='기준일별 PM 구성과 적용 버전 연결';

-- 11. 버전별 항목 상태와 적용 모델
CREATE TABLE release_version_item (
    release_version_id INT UNSIGNED NOT NULL COMMENT '릴리즈 버전 ID',
    item_id INT UNSIGNED NOT NULL COMMENT '변경 항목 ID',
    item_state VARCHAR(20) COLLATE utf8mb4_bin NOT NULL COMMENT 'Item 적용 상태: APPLIED/NOT_APPLIED/UNKNOWN; 대상 외·위험 해제는 별도 판단',
    target_scope VARCHAR(20) COLLATE utf8mb4_bin NOT NULL DEFAULT 'UNKNOWN' COMMENT '모델 적용 범위: ALL/LISTED/UNKNOWN',
    target_group_ids LONGTEXT NULL COMMENT 'LISTED일 때 적용 모델 ID 배열; 그 외는 NULL',
    PRIMARY KEY (release_version_id, item_id),
    KEY ix_release_version_item_item (item_id),
    CONSTRAINT fk_release_version_item_version FOREIGN KEY (release_version_id)
        REFERENCES release_version (release_version_id) ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT fk_release_version_item_item FOREIGN KEY (item_id)
        REFERENCES item (item_id) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='버전별 Item 적용 상태와 전체·지정 모델 범위';

-- 12. 수동 위험 지정: 릴리즈 교체 대상 원본과 분리해 보존한다.
CREATE TABLE risk_rule (
    risk_rule_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '위험 지정 ID',
    rule_type VARCHAR(30) COLLATE utf8mb4_bin NOT NULL COMMENT '위험 유형: SAFETY_REQUIRED/DEFECT_ITEM/DIRECT_VERSION',
    family_id INT UNSIGNED NOT NULL COMMENT '대상 버전 계열 ID',
    group_id INT UNSIGNED NULL COMMENT '특정 적용 모델 ID; 계열 전체 대상이면 NULL',
    item_id INT UNSIGNED NULL COMMENT '필수·결함 Item ID; 연결 미확인·삭제 시 NULL',
    release_version_id INT UNSIGNED NULL COMMENT '직접 위험 지정한 버전 ID; 해당 없음·삭제 시 NULL',
    start_release_version_id INT UNSIGNED NULL COMMENT '위험 시작 버전 ID; 범위에 포함; 미확인은 NULL',
    end_release_version_id INT UNSIGNED NULL COMMENT '위험 해제 버전 ID; 범위에서 제외; 미해결·미확인은 NULL',
    risk_reason TEXT NOT NULL COMMENT '환경안전 필수 또는 결함 위험 사유',
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE COMMENT '이 위험 지정의 사용 여부; 위험 판정 결과 자체는 아님',
    target_reference LONGTEXT NOT NULL COMMENT '계열·Item 번호·버전 원문 등 삭제 후 재연결에 사용할 근거',
    mapping_status VARCHAR(20) COLLATE utf8mb4_bin NOT NULL DEFAULT 'UNRESOLVED' COMMENT '연결 확인 상태: VERIFIED/UNRESOLVED',
    PRIMARY KEY (risk_rule_id),
    KEY ix_risk_rule_family_status (family_id, mapping_status),
    KEY ix_risk_rule_group (group_id),
    KEY ix_risk_rule_item (item_id),
    KEY ix_risk_rule_direct_version (release_version_id),
    KEY ix_risk_rule_start_version (start_release_version_id),
    KEY ix_risk_rule_end_version (end_release_version_id),
    CONSTRAINT fk_risk_rule_family FOREIGN KEY (family_id)
        REFERENCES version_family (family_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    -- 모델 삭제로 NULL=계열 전체로 범위가 넓어지지 않도록 RESTRICT를 사용한다.
    CONSTRAINT fk_risk_rule_group FOREIGN KEY (group_id)
        REFERENCES equipment_group (group_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_risk_rule_item FOREIGN KEY (item_id)
        REFERENCES item (item_id) ON DELETE SET NULL ON UPDATE RESTRICT,
    CONSTRAINT fk_risk_rule_direct_version FOREIGN KEY (release_version_id)
        REFERENCES release_version (release_version_id) ON DELETE SET NULL ON UPDATE RESTRICT,
    CONSTRAINT fk_risk_rule_start_version FOREIGN KEY (start_release_version_id)
        REFERENCES release_version (release_version_id) ON DELETE SET NULL ON UPDATE RESTRICT,
    CONSTRAINT fk_risk_rule_end_version FOREIGN KEY (end_release_version_id)
        REFERENCES release_version (release_version_id) ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='환경안전·결함 위험의 수동 지정과 재연결 근거';

-- SET NULL로 바뀌기 전의 위험 근거는 앱 저장 서비스가 보존한다.
-- 유형별 필수 대상과 계열 일치, 시작·해제 순서 검증은 후속 업무 구현에서 추가한다.

-- 13. 관리 설정
CREATE TABLE app_setting (
    setting_key VARBINARY(400) NOT NULL COMMENT '설정 이름',
    setting_value LONGTEXT NOT NULL COMMENT '설정값; 노후도 주의·위험 기간과 단위 등',
    PRIMARY KEY (setting_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='관리 설정; 사용자 인증·권한 테이블과 구분';

-- 기본 노후도 기준: 1년 미만 안전, 1년 이상 2년 미만 주의, 2년 이상 위험.
INSERT INTO app_setting (setting_key, setting_value)
VALUES ('version_age_thresholds', '{"warning":1,"danger":2,"unit":"YEAR"}');

-- 삭제 전 위험 근거 보존은 앱의 저장 서비스에서 같은 트랜잭션으로 처리한다.

-- 최종 구성: 테이블 13개. 위험 근거 보존과 JSON·값 검증은 앱 저장 서비스를 사용한다.
-- 설비 코드 중복: equipment(equipment_code), 전체 사업장에서 고유.
-- 정확한 버전 원문 중복: release_version(family_id, version_raw).
-- 기준일별 설비 중복: equipment_snapshot(equipment_id, base_date).
-- 적용 SW 정확한 중복: equipment_version(snapshot_id, family_id, version_raw).
-- 이 UNIQUE는 같은 계열의 다른 PM 버전을 허용하고 정확한 중복만 막는다.
-- PM 중복: equipment_pm(snapshot_id, pm_name).
-- 버전·Item 연결 중복: release_version_item의 복합 기본키.
-- 설정 중복: app_setting의 기본키.
-- C3 형상별 계열 분리·호기 누락 HPA 저장 예외는 추가하지 않는다.
-- 필수값 공란의 위치 안내·보완·전체 묶음 저장 보류는 저장 서비스에서 처리한다.
-- 설비·계열당 버전 하나를 강제하는 제약은 없으며 PM 예외 표시는 저장 자료로 계산한다.
-- 직접 SQL로 값을 수정해도 DB 제약을 만족하는지와 업무 판정이 올바른지는 별개다.
-- 서비스 검증 A~O를 같은 저장 트랜잭션에서 실행하고, 변경 후 조회·집계·위험 결과를 갱신한다.
