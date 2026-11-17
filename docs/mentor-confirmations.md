# 멘토 확정 사항의 코드 반영 위치

> **기준 문서는 기획 폴더의 `02_멘토링/멘토링 확정 사항.md`이다.** 멘토 결정의 원문·출처·바뀐 결정은 그 문서를 따른다. 이 문서는 그 결정이 코드 어디에 반영됐는지를 적은 보조 자료다. 두 문서가 다르면 기준 문서가 맞다.
>
> 문서 기준: 2026-11-16, develop `f0cfc8d`. 처음 판은 데이터 파트가 2026-11-03에 정리했고(1~4차, 채팅 회신 10/20·10/27·10/28·10/29), 이번에 6차 멘토링(2026-11-11)과 11-11 멘토 VOC를 반영했다. 근거: 기획 폴더 `02_멘토링/멘토링 확정 사항.md`, `02_멘토링/6차 멘토링 정리.md`.

## 1. 1~4차와 채팅 회신 (2026-11-03 정리, 11-16 갱신)

멘토링은 차수가 높을수록 최신이다. 6차에서 바뀐 항목은 "바뀜" 표시와 함께 2절 번호를 적었다.

| # | 확정 내용 | 날짜·근거 | 코드 반영 |
|---|---|---|---|
| 1 | 최상위 설비군은 Poly / Oxide / KCTC 3개. HPA는 읽기만, 집계 제외. Memory/Foundry는 분류가 아니라 속성(6차도 같음, 2절 #1) | 10/14 2차, 10/21 3차, 11/11 6차 | `equipment_group.category`, `analysis_enabled` |
| 2 | 개조형(C4 DFB·C4+·XMO3·O5 Prime·CIP)은 원 설비군의 릴리즈노트를 쓴다. **바뀜(일부):** C4 DFB는 릴리즈노트는 C4 것을 쓰되 화면·집계·출력에서는 독립 설비군으로 본다(2절 #5) | 10/21 3차, 10/27 회신, 11-11 VOC | seed v2 `parent_group_id`, `reference_data.MODEL_PARENTS` |
| 3 | C4 DFB 는 C4 릴리즈노트의 `_DFB` 버전 줄기, C4+ 는 `_DEX` 줄기 | 10/21, 10/29 회신 | `reference_data.resolve_model_text`(`_DEX`), 번역표 후보 주의 문구 |
| 4 | KCTC 는 Inline Ox / Inline O2 / Cluster Ox / Cluster O2 **4개 분리** | 10/29 회신 | seed v2 모델·계열 4개, `GROUP_MODELS` |
| 5 | **UI 표기가 없는 M-C3 는 통합UI** (구UI가 특별한 설비). 6차에서 UI 포함 여부는 선택값이 됨(2절 #7) | 10/28 회신 (4차 일지), 11/11 6차 | `extraction/normalization.py` C3 기본값, `import_save._family`의 통합UI 기본값, 번역표 후보 |
| 6 | `M-C4/C4+` 혼합 표기는 SW 버전에 `_DEX` 가 있으면 C4+, 없으면 C4 | 10/29 회신 | `reference_data.RESOLVE_BY_VERSION`, 번역표 후보 |
| 7 | 메모리/파운드리는 **라인이 정한다**(파운드리 라인 = 평택 S5·V2·P3-S5, 화성 Foundry 시트). 설비별 예외('메모리버전임')는 수동 지정. 6차에서 평택 메모리 세부 라인과 사용자 라인 추가가 더해짐(2절 #2·#3) | 10/14 메모, 10/27·10/28 회신, 11/11 6차 | `reference_data.LINE_AREAS`, `default_area`, `import_save._family` |
| 8 | 파운드리용 릴리즈노트가 따로 있는 모델은 O2·C3·C4. O3·O5·KCTC 는 메모리/파운드리 공통 | 10/14, 10/27, 10/28 회신 | seed v2 ' (파운드리)' 계열, `reference_data.FOUNDRY_FAMILY_MODELS` |
| 9 | 버전은 이름표. 순서는 릴리즈노트 나열 순서로만. 숫자 크기 비교 안 함 | 10/14 2차 | `features/version` (`version_order`) |
| 10 | 점·밑줄은 같은 버전 (`v999.3.7` = `v999.3_7`), `(H)` 는 화성 표시로 제거 후 비교. 기준 문서에는 `v999.3.7`·`v999.3_7`이 "아직 확정되지 않은 것"으로 남아 있다 | 10/20 회신, 10/14 | `features/version` `FamilyReleases.candidates` (후보 제시까지, 자동 연결 안 함) |
| 11 | `+ Hotfix` 는 릴리즈노트에 같은 이름으로 수록 → 그대로 일치. `_a`·`_c` 꼬리표 버전은 릴리즈노트에 없을 수 있어 사용자가 추가 등록 | 10/29 회신 | 릴리즈 등록(BE-004), 버전 순서 후보 |
| 12 | 적용 = 기준 N 과 같거나 높음 | 10/14 2차 | `features/version.compare_version` |
| 13 | 노후도 색 = 출시일 기준 1년 미만/1년 이상/2년 이상 (설정값) | 10/21 3차 | `features/aging` |
| 14 | 위험 = 환경안전 아이템 미적용 OR 결함 버전. 사용자가 직접 등록. 6차에서 위험 기준 등록이 필요하다고 다시 확인(2절 #11) | 10/21 3차, 11/11 6차 | `features/risk`, `risk_rule` |
| 15 | 우선순위 = 위험 → 버전 낮은 순 (기준 N 까지 칸 수) | 10/21 3차 | `features/priority` |
| 16 | 예측 = 단순 비례, 참고용 | 10/21 3차 | `features/forecast` |
| 17 | 미등록 버전은 전체 대수에 포함, 업그레이드 필요에서는 제외, '미등록'으로 별도 표시. 기준 문서에는 멘토 답변 기록이 없어 "아직 확정되지 않은 것"으로 남아 있다 | 10/28 회신 (데이터 파트 기록: 이의 없음) | 집계: 팀 규칙(11-05)대로 비교 불가는 적용률 분모에서 빼고 따로 표시 (`aggregation` `RateCount.rate`) |
| 18 | 설비호기는 전 사업장에서 유일 | 10/27 회신 | `equipment.equipment_code` UNIQUE |
| 19 | PM 혼합 설비(O2/O3)는 양쪽에 각 1대로 집계 | 10/14, 10/21 | 혼합형 PM 저장 |
| 20 | 과거 현황표는 기존 파일 복사 + 날짜·버전 조정으로 직접 제작 | 10/28 회신 | `ai/tests/data/*_2026-08-03/09-01.xlsx` |
| 21 | 화성 현황표의 `S1 (기흥)`·`1L (기흥)` 등 라인의 사업장은 **화성** (`(기흥)`은 위치 표시) | 11/05 회신 | 번역표 후보 `excel_import/candidates.py` (`GIHEUNG_NOTE`), 장소표 `reference_data.LINE_AREAS` |
| 22 | 같은 버전이 다른 출시일로 여러 번 나오면 **재배포**, 같은 버전으로 합친다. 출시일은 첫 날짜 | 11/05 회신 | 검증 `excel_import._release_issues` (`RERELEASE` 안내), 저장 `import_save._releases` |
| 23 | 정해진 양식은 **규칙 추출 우선**, 처음 보는 양식만 AI | 11/05 (팀 결정) | `features/rule_extraction`, `ui/import_extraction.extract_for_review(rules=…)` |

## 2. 6차 멘토링(2026-11-11)과 11-11 멘토 VOC

"코드 반영"은 2026-11-16 코드에서 확인한 위치다. 확인하지 못한 것은 "확인 안 함"으로 적었다.

| # | 결정 | 근거 | 코드 반영 |
|---|---|---|---|
| 1 | **메모리/파운드리 구분이 가장 큰 요구사항.** 부서(고객사)가 달라 보고서에서 합쳐 보여주지 않는다. 구분 기준은 사이트·라인. 상단 필터는 메모리/파운드리 구분으로 유지하고, 고르면 라인 목록도 그 구분만 보인다. 설비 분류는 바꾸지 않는다 | 6차 | 장소표 `reference_data.LINE_AREAS`·`default_area`, 표시 이름 `features/production_area.py`, 조회 조건의 Fab 자리에 Memory/Foundry(`ui/query_state.FabOption`). 보고서에서 둘을 나누는지는 확인 안 함 |
| 2 | 평택 메모리 라인은 세부 라인(P1F/P1D, P2F/P2D, P4는 H/D)으로 나누고 현황표 라인명을 그대로 인식한다. 평택 파운드리는 나누지 않는다 | 6차 | 장소표에 P1F·P1D·P2F·P2D·P3F·P3D·P4F. P4H·P4D는 장소표에 없고 데이터 관리에서 등록한 라인(`registered_lines.v1`, `reference_data.REGISTERED_AREAS`)이 장소표보다 먼저 쓰인다 |
| 3 | 라인·설비군·사이트를 사용자가 추가할 수 있어야 하고 보고서까지 나와야 한다 (11-08 팀 보류는 더 이상 적용 안 함) | 6차 | 데이터 관리 `features/data_admin`(설비군 등록, `registered_sites`, `registered_lines`), 조회 선택지 `ui/database_source.database_catalog(extra_groups, extra_sites, extra_lines)`. 보고서 반영과 릴리즈노트·목표 N 연결 방식은 기준 문서에서도 미정 |
| 4 | **데이터 가져오기에 SW 계열은 필요 없다** (6차 의견 → 11-11 VOC로 확정) | 6차, 11-11 VOC 1 | 필수값 `excel_import/service.py`의 `REQUIRED`에 SW 계열 없음. 빈 계열은 모델명 번역표로 채움(`_derive_families`). 단, DB 저장(`import_save._family`)은 계열이 정해져야 하므로 번역표에 없는 모델은 저장 전 검증에서 확인을 요구한다 |
| 5 | **C4 DFB는 수가 많아 독립 설비군으로 본다.** 릴리즈노트는 C4 노트(DFB 열, `_DFB` 버전)를 그대로 쓴다 | 11-11 VOC 2 | 독립 표시 설정 `data_admin.independent_groups`(`independent_groups.v1`), AI 분석 집계 `features/chat/aggregate.py`(설비군별), 라인별 히트맵 Excel `export/line_heatmap.py`. 조회 선택지 `GROUP_MODELS`는 아직 `c4`에 M-C4·M-C4 DFB·M-C4+를 함께 묶는다 |
| 6 | 가져오기 '구조 부분' 항목은 '공정'으로 바꾼다. AI 분석에서 공정별 버전 분포를 볼 때 쓴다 | 6차 | `equipment_snapshot.process_name`(스키마), 이전 DB 자동 보정 `features/equipment/process_upgrade.py`. 팀 확정 ERD에는 이 칸이 아직 없다 |
| 7 | UI 포함 여부는 필수가 아닌 선택값. 필수값이 아닌 항목은 비어 있어도 저장한다 | 6차 | `REQUIRED`: 현황표는 기준일·사이트·모델명·호기·SW 버전, 릴리즈노트는 버전만 |
| 8 | 아이템 매니저에 **버전 · 아이템 · 아이템별 Jira 코드**를 보인다. Jira 코드는 릴리즈 노트에 있다 | 6차 | `item.jira_code`(스키마), 추출 필드 `RELEASE_NOTE_FIELDS`의 `jira_code`, `features/item/jira.py`, 이전 DB 자동 보정 `features/item/jira_upgrade.py`, 화면 `ui/items.py` JIRA 열 |
| 9 | 히트맵 엑셀 내보내기는 설비군 → 라인으로 묶고 설비명과 색만 보인다. 화면 기본도 이 형식, 기존 상세 히트맵은 '상세 보기' | 6차 | Excel `features/export/line_heatmap.py`. 화면 기본 형식은 확인 안 함 |
| 10 | 기준 버전(N) 설정은 현재대로 유지 | 6차 | N 그룹 판정 `features/version/groups.py`, 저장·이력 `features/targets/groups.py` |
| 11 | 위험 기준 등록 기능은 필요하다(롤백·버그 버전). 대시보드에 위험 표시, 엑셀은 위험 표시본·미표시본 두 가지 | 6차 | 등록 `ManagementService.create_risk_rule`, `ui/risk_rule_dialog.py`. 엑셀 두 가지 출력은 확인 안 함 |
| 12 | AI 분석은 현재 방향 유지(DB에 있는 정보를 자연어로 묻고 답함) | 6차 | `features/chat`, `ui/chat_page.py` |
| 13 | 버전 리플레이는 꺾은선과 히트맵 둘 다, 설비군 하나를 고정하고 라인별로 보며, 그래프도 엑셀로 추출 | 6차 | 확인 안 함 |
| 14 | 보고서는 회사 고정 양식 없음. 라인별 적용률과 히트맵 엑셀 형태를 추가 | 6차 | 히트맵 엑셀은 #9. 라인별 적용률은 확인 안 함 |

## 3. 팀 규칙 (11-05 정함, 11-08 진행 결정)

멘토 확인 전 팀 규칙이다. 기준 문서는 "아직 확정되지 않은 것"에 팀 적용 규칙으로 적어 두었고, 팀은 11-08에 멘토 확인을 따로 기다리지 않고 이 규칙대로 진행하기로 했다.

- 포도송이 히트맵 색: 진초록 N+1 이상 · 연초록 N · 노랑 N-1 · 빨강 N-2 이하 · 회색 비교 불가, 위험은 테두리. 반영: `features/version/groups.py`(`Tier`), `export/line_heatmap.py`, 히트맵 화면.
- N 버전 그룹화: N-2 ~ N+2 칸에 사용자가 자유롭게 여러 버전, 저장마다 이력. 반영: `features/version/groups.py`, `features/targets/groups.py`(`app_setting`에 저장·이력·되돌리기), `ui/group_dialog.py`.
- N 그룹 이력은 바꾼 기록을 보고 되돌리는 데까지만 쓴다. 과거 날짜를 그때의 그룹으로 다시 판정하지 않는다(11-08).
- 적용률 분모: 비교 불가(노트 없음·SW 미등록·버전 누락·목표 미지정)는 분모에서 빼고 따로 표시.
- Item 포함 누적 판정: 코드(`features/item/service.py`)는 2026-11-07 사용자 확인을 근거로 같은 계열의 확인된 직전 포함 상태를 이어받는다. 기준 문서는 이 항목을 팀 11-08 "보류"로 두었다.

## 4. 아직 미확정

기준 문서의 "아직 확정되지 않은 것" 중 데이터·코드에 직접 닿는 항목만 옮겼다. 전체 목록은 기준 문서를 본다.

- 실제 현황표에서 HPA에도 SW 버전이 적혀 있는가. 지금은 HPA도 SW 버전 필수(저장 규칙: 현황마다 SW 1개 이상). 교육용 평택 S5 HPA 5대는 버전 칸이 비어 있어 입력해야 저장된다. 11-11 VOC 3(V2 라인 HPA 레코드 때문에 저장 불가)도 같은 계열로 보이나 원인은 확인 전이다.
- SW 계열을 뺀 뒤의 처리: C3 구UI·통합UI처럼 한 설비군에 계열이 둘 이상인 경우와 파운드리 계열을 무엇으로 가를지, '임베디드 버전'이 어느 항목인지.
- 개조 정보·형상에 넣을 값 (멘토가 정리해 전달하기로 함).
- 파운드리 C3 구UI 릴리즈노트 파일이 따로 있는가 (현재 메모리 구UI 로 대신 비교).
- 개조형(Prime 등)을 기본 집계에 포함하는가 (기준 문서의 "Prime 설비 제외 범위").
- 모델별 적용 열이 없는 릴리즈노트(O3·O5 등)의 Item을 그 계열을 쓰는 모든 모델에 적용된 것으로 볼지.
- C3 구UI 버전(`v1.1.1.6B_6`, `+Hotfix`, `_a`, `_c`)의 선후 관계.
