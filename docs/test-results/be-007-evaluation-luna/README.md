# BE-007 gpt-6-luna 평가와 sol 비교 증거

2026-11-02에 `gpt-6-luna`로 Memory·Foundry를 각각 한 번 호출했다. 기존 [gpt-6.1-sol 평가](../be-007-evaluation/README.md)의 셀 스냅샷·정답·정책·지시문·제품 코드를 그대로 사용했다. 정답을 새로 생성하거나 모델 응답에 맞춰 바꾸지 않았다. 원본 통합문서와 검색 가능한 정답표는 기존 묶음에 보존되어 있다.

| 항목 | gpt-6.1-sol | gpt-6-luna |
| --- | ---: | ---: |
| 원문 계약 일치율 | 2,151/2,160 = 99.58% | 2,018/2,160 = 93.43% |
| 후처리 일치율, 추가 결과 페널티 포함 | 2,016/2,029 = 99.36% | 1,857/2,061 = 90.10% |
| 같은 정답 위치만의 후처리 일치율 | 2,016/2,029 = 99.36% | 1,857/2,029 = 91.52% |
| 후처리 값 있는 정답 일치율 | 812/825 = 98.42% | 710/825 = 86.06% |
| 전체 토큰 | 26,779 | 21,217 |
| 시간 합계 | 128.643초 | 105.199초 |

정답 위치는 모델별 2,160개이며 판정 보류 131개를 제외한 커버리지는 93.94%로 같다. luna의 후처리에서는 PM2·PM4가 원본 대조에서 차단되어 정답 PM과 대응되지 않은 결과 2개가 발생했다. 사전 정책대로 각 16필드의 추가 페널티를 분모에 넣어 2,061개를 채점했다. 실제 행 수는 두 모델 모두 135개이며, 2개 대응 실패를 물리 설비가 추가된 결과로 해석하지 않는다.

## 입력과 보존 확인

- [공유 파일·기존 증거 해시](comparison-setup.json): 기존 sol 묶음의 20개 파일을 모두 보존했는지 검증한다.
- [고정 셀 스냅샷](source-snapshot.json), [고정 정답](ground-truth.json), [평가 정책](reference-policy.md), [정답 고정 이력](reference-manifest.json): sol 파일과 바이트가 같은 사본이다.
- [원본 통합문서](../be-007-evaluation/source-workbook.xlsx), [검색 가능한 정답표](../be-007-evaluation/ground-truth.html): 기존 기준선을 공통으로 사용한다.
- [수집·채점기](benchmark.py), [표 생성기](render_tables.py): sol과 동일한 파일이다. [luna 실행기](run_luna.py)는 공유 파일을 검증하고 현재 Python 프로세스의 모델만 바꾼다. `.env`와 제품 코드는 수정하지 않는다.

## 실제 입력·응답·출력

| 시트 | 전체 요청 메시지 | 실제 HTTP 응답·usage·시간 | 제품 후처리 결과 | 시간 | 전체 토큰 |
| --- | --- | --- | --- | ---: | ---: |
| Memory | [request](request-Memory.json) | [response](response-Memory.json) | [result](result-Memory.json) | 55.371초 | 12,114 |
| Foundry | [request](request-Foundry.json) | [response](response-Foundry.json) | [result](result-Foundry.json) | 49.828초 | 9,103 |

두 요청 모두 HTTP 200, `OK`, `REAL`, 요청·응답 모델 `gpt-6-luna`다. 각 시트의 메시지 본문은 sol과 완전히 같다. 시간 제한은 180초, 자동 재시도는 0회이며 이번에 추가한 실제 API 요청은 2회다.

## 어떤 값에서 틀렸는지 확인

- [luna 전체 대조표](field-comparison.html): 원본 셀·정답·실제 값·판정을 필터로 찾는다.
- [luna 전체 대조 JSON](field-comparison.json), [luna 불일치 목록](field-comparison.md), [luna 정확도 지표](metrics.json).
- [두 모델의 오류를 나란히 비교](model-field-errors.md): 한 모델 이상 틀린 모든 정답 필드와 대응되지 않은 실제 결과를 기록했다.
- [두 모델 전체 필드 비교 JSON](model-field-comparison.json): 정답 위치 4,320개를 원문/후처리 두 단계로 비교했다.
- [대응되지 않은 실제 결과 JSON](model-extra-records.json): luna 후처리의 PM 대응 실패 2개 × 16필드 = 32개 추가 페널티 필드를 따로 보존한다.
- [모델 비교 지표·조건 검증 JSON](model-comparison.json), [비교 생성 스크립트](compare_models.py).

`missing_record`는 기대한 원본 호기·PM 위치에 대응하지 못한 결과다. 원문 JSON의 행 자체가 사라졌다는 뜻과 구분한다. 해당 행의 `raw_values`, 실제 반환값과 차단 사유는 `result-Foundry.json`에도 남아 있다. `(기흥)`과 `기흥`, `PM2`와 `2`처럼 의미가 유사한 표기 차이와, 구조 구분·라인 값의 잘못된 배정도 구분해서 [기존 문서 10절](../be-007-ai-extraction.md#10-gpt-6-luna와-gpt-61-sol-동일-조건-비교)에 설명했다.

## 실행과 재계산

저장소 루트에서 실행한다. 키는 Git에서 제외된 `.env`의 `SETRON_AI_API_KEY`로 읽는다. `run_luna.py`가 모델을 프로세스 안에서만 선택하므로 `.env`의 모델을 변경할 필요가 없다. 이미 응답이 있는 시트를 재호출하거나 덮어쓰지 않는다.

```powershell
# 최초 수집 명령. 완료된 이 묶음에서는 중복 호출 방지로 중단된다.
uv run --locked --env-file .env python docs/test-results/be-007-evaluation-luna/run_luna.py --sheet Memory --timeout 180
uv run --locked --env-file .env python docs/test-results/be-007-evaluation-luna/run_luna.py --sheet Foundry --timeout 180

# 아래 명령은 보관한 응답으로 재계산하며 API를 호출하지 않는다.
uv run --locked python docs/test-results/be-007-evaluation-luna/benchmark.py compare
uv run --locked python docs/test-results/be-007-evaluation-luna/render_tables.py comparison
uv run --locked python docs/test-results/be-007-evaluation-luna/compare_models.py

# 두 모델이 공통으로 사용하는 평가기 규칙 검증.
uv run --locked python -m pytest docs/test-results/be-007-evaluation/test_benchmark.py -q
```

각 모델·시트당 한 번 측정했다. 서버 부하·네트워크·모델별 기본 추론 설정과 출력 분량 차이가 시간에 영향을 줄 수 있다. 가격·크레딧은 수집하지 않아 토큰 차이를 비용 절감률로 환산하지 않는다. 전체 비교와 해석은 [기존 테스트 문서](../be-007-ai-extraction.md)에 반영했다.
