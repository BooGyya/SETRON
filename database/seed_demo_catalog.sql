-- 시연용 모델·계열 기준 자료 v2 (Jira S15P31B106-75) — 2026-11-03 데이터 파트 기준 자료(멘토 확정 14모델·15계열)로 갱신
--
-- 가져오기 저장은 등록된 모델(equipment_group)과 버전 계열(version_family)에만 연결한다.
-- schema_mysql5619.sql에는 이 기준 자료가 없어서, 교육용 입력 자료(03_데이터)의 모델 표기를 바탕으로
-- 시연용 목록을 만든 것이다. 확정된 기준 자료가 아니며 데이터 담당 확인 후 바꾼다.
--
-- 여러 번 실행해도 같은 이름은 다시 넣지 않는다. 개발 DB에만 적용한다.
--   docker compose exec -T mysql-dev sh -c 'mysql -h127.0.0.1 -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' < database/seed_demo_catalog.sql

SET NAMES utf8mb4;

INSERT INTO equipment_group (group_name, category, parent_group_id, analysis_enabled)
SELECT v.group_name, v.category, NULL, v.analysis_enabled
FROM (
    SELECT 'M-C3' AS group_name, 'POLY' AS category, 1 AS analysis_enabled
    UNION ALL SELECT 'M-C4', 'POLY', 1
    UNION ALL SELECT 'M-O2', 'OXIDE', 1
    UNION ALL SELECT 'M-O3', 'OXIDE', 1
    UNION ALL SELECT 'M-O5', 'OXIDE', 1
    UNION ALL SELECT 'Inline Ox', 'KCTC', 1
    UNION ALL SELECT 'Inline O2', 'KCTC', 1
    UNION ALL SELECT 'Cluster Ox', 'KCTC', 1
    UNION ALL SELECT 'Cluster O2', 'KCTC', 1
    UNION ALL SELECT 'HPA', 'HPA', 0
) v
WHERE NOT EXISTS (SELECT 1 FROM equipment_group g WHERE g.group_name = v.group_name AND g.category = v.category);

-- 파생형: 기본형의 릴리즈노트를 공유한다 (멘토 확인 2026-10-21·10-27). C4 DFB=_DFB, C4+=_DEX, Prime 은 'Prime' 표기.
INSERT INTO equipment_group (group_name, category, parent_group_id, analysis_enabled)
SELECT v.group_name, v.category, p.group_id, 1
FROM (
    SELECT 'M-O5 Prime' AS group_name, 'OXIDE' AS category, 'M-O5' AS parent_name
    UNION ALL SELECT 'M-C4 DFB', 'POLY', 'M-C4'
    UNION ALL SELECT 'M-C4+', 'POLY', 'M-C4'
    UNION ALL SELECT 'XMO3', 'OXIDE', 'M-O3'
) v
JOIN equipment_group p ON p.group_name = v.parent_name AND p.category = v.category
WHERE NOT EXISTS (SELECT 1 FROM equipment_group g WHERE g.group_name = v.group_name AND g.category = v.category);

-- 계열 = 릴리즈노트 1개. C3는 구UI·통합UI 분리, O2·C3·C4는 파운드리용 릴리즈노트가 별도 파일(10/14·10/27)이라
-- ' (파운드리)' 계열을 따로 둔다. O3·O5·KCTC는 메모리/파운드리 공통(10/28). KCTC는 Inline Ox/O2·Cluster Ox/O2 4개(10/29).
INSERT INTO version_family (group_id, family_name, ui_type)
SELECT g.group_id, v.family_name, v.ui_type
FROM (
    SELECT 'M-C3' AS group_name, 'C3 구UI' AS family_name, 'LEGACY' AS ui_type
    UNION ALL SELECT 'M-C3', 'C3 통합UI', 'INTEGRATED'
    UNION ALL SELECT 'M-C3', 'C3 구UI (파운드리)', 'LEGACY'
    UNION ALL SELECT 'M-C3', 'C3 통합UI (파운드리)', 'INTEGRATED'
    UNION ALL SELECT 'M-C4', 'C4', NULL
    UNION ALL SELECT 'M-C4', 'C4 (파운드리)', NULL
    UNION ALL SELECT 'M-O2', 'O2', NULL
    UNION ALL SELECT 'M-O2', 'O2 (파운드리)', NULL
    UNION ALL SELECT 'M-O3', 'O3', NULL
    UNION ALL SELECT 'M-O5', 'O5', NULL
    UNION ALL SELECT 'Inline Ox', 'Inline Ox', NULL
    UNION ALL SELECT 'Inline O2', 'Inline O2', NULL
    UNION ALL SELECT 'Cluster Ox', 'Cluster Ox', NULL
    UNION ALL SELECT 'Cluster O2', 'Cluster O2', NULL
    UNION ALL SELECT 'HPA', 'HPA', NULL
) v
JOIN equipment_group g ON g.group_name = v.group_name
WHERE NOT EXISTS (SELECT 1 FROM version_family f WHERE f.group_id = g.group_id AND f.family_name = v.family_name);
