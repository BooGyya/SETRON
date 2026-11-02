# BE-007 전체 필드 평가 증거

화성 교육용 설비 현황표의 Memory 85개 행과 Foundry 50개 결과 행을 대상으로 16개 필드를 평가한다. 원본 셀을 기준으로 정답을 먼저 만들고 해시로 고정한 뒤, 실제 AI 원문과 제품의 정규화 결과를 각각 비교한다.

2026-11-02에 `gpt-6.1-sol` 실제 호출 2회를 완료했다. AI 원문은 2,151/2,160(99.58%), 후처리 결과는 평가 가능한 2,016/2,029(99.36%)가 일치했다. 후처리에서 값이 있는 정답만 보면 812/825(98.42%)다. 판정 보류 131개를 제외한 커버리지는 93.94%이며, 오류와 `null`을 구분한 해석은 [기존 테스트 문서 9절](../be-007-ai-extraction.md#9-원본-정답표를-이용한-전체-필드-재평가)에 기록했다.

## 정답과 판정 기준

- [원본 기반 평가 정책](reference-policy.md): 사용자 확인 기준, 필드별 근거, 누락·추가·보류 처리.
- [전체 필드 정답표](ground-truth.html): 시트·단계·필드·상태로 검색할 수 있는 표.
- [정답 JSON](ground-truth.json): 135개 레코드별 원문 정답·정규화 정답·근거 셀·규칙.
- [정답 고정 시점과 해시](reference-manifest.json): 원본·정답·정책·제품 파일별 SHA-256.
- [원본 셀 스냅샷](source-snapshot.json): 열린 Excel의 값·좌표·시작 위치·병합 범위.
- [원본 통합문서 보관본](source-workbook.xlsx): 스냅샷 시점의 디스크 파일을 바이트 그대로 복사한 파일.

각 단계의 필드 위치는 135 × 16 = 2,160개다. 원문 정답은 값이 있는 802개와 원본 누락에 따른 `null` 1,358개다. 정규화 정답 중 131개는 판정 보류다. 전체 필드를 정답표에 넣되, 정규화 정확도는 값이 확정되는 2,029개를 분모로 사용하고 커버리지를 공개한다.

원본 누락과 판정 보류는 다르다. 원본 누락은 입력에 값이 없으므로 `null` 반환을 평가할 수 있다. 판정 보류는 해당 DB 계열이나 정규화 대응을 이 원본과 확정 규칙만으로 결정할 수 없어 정답값을 채점하지 않는다. 보류 필드에 AI가 반환한 값도 대조표에 그대로 남긴다. 이 정답표는 독립적인 원본 규칙에 따라 작성했으며 업무 전문가의 최종 검수 완료를 주장하지 않는다.

## 실제 응답과 평가 결과

| 시트 | 전체 요청 | HTTP 응답·usage·시간 | 제품 후처리 결과 | 측정 시간 | 전체 토큰 |
| --- | --- | --- | --- | ---: | ---: |
| Memory | [request](request-Memory.json) | [response](response-Memory.json) | [result](result-Memory.json) | 70.962초 | 16,186 |
| Foundry | [request](request-Foundry.json) | [response](response-Foundry.json) | [result](result-Foundry.json) | 57.681초 | 10,593 |
| 합계 | 2회 | HTTP 200 · REAL | 135개 행 | 128.643초 | 26,779 |

- [검색 가능한 전체 대조표](field-comparison.html): 원문·정규화 두 단계의 4,320개 필드 행을 표시한다.
- [전체 필드 대조 JSON](field-comparison.json): 정답·실제 값·셀 근거·판정·정렬 방법을 보존한다. 값 일치와 출처 주소 일치는 별도 항목이다.
- [불일치 목록](field-comparison.md): 원문 불일치 9개와 후처리 불일치 13개. 같은 레코드가 두 단계에 걸쳐 포함된다.
- [정확도 지표 JSON](metrics.json): 전체·시트별·필드별 분모와 일치율, 보류, 누락·추가를 확인한다.

응답에는 GMS가 보고한 토큰 세부 정보도 보존했다. 추후 `gpt-6-luna`와 비교할 때는 이 스냅샷·정답·지시문을 재사용하고 새 응답을 별도 묶음에 기록한다. 현재 보관본에는 luna 호출 결과가 없다.

## 실행과 재계산

명령은 저장소 루트에서 실행한다. 실제 키는 Git에서 제외된 `.env`의 `SETRON_AI_API_KEY`에, 모델은 `SETRON_AI_MODEL`에 설정한다. 이번 요청 모델은 `gpt-6.1-sol`이었다. 키와 인증 헤더는 증거 파일에 저장하지 않는다. 아래 `reference`·`run` 명령은 최초 수집 절차를 설명하며, 이미 완성된 이 묶음에서는 보존 장치에 의해 중단된다.

```powershell
# 정답은 AI 호출 전에만 생성할 수 있다.
uv run --locked python docs/test-results/be-007-evaluation/benchmark.py reference

# 실제 GMS 요청. 각 시트의 고정 스냅샷을 한 번 전송한다.
uv run --locked --env-file .env python docs/test-results/be-007-evaluation/benchmark.py run --sheet Memory --timeout 180
uv run --locked --env-file .env python docs/test-results/be-007-evaluation/benchmark.py run --sheet Foundry --timeout 180

# 보관한 결과의 재계산. 아래 두 명령은 API를 호출하지 않는다.
uv run --locked python docs/test-results/be-007-evaluation/benchmark.py compare
uv run --locked python docs/test-results/be-007-evaluation/render_tables.py comparison

# 평가 분모·누락·추가·NULL 처리 검증. API를 호출하지 않는다.
uv run --locked python -m pytest docs/test-results/be-007-evaluation/test_benchmark.py -q
```

이미 기록된 `response-시트명.json`이 있으면 `run`은 덮어쓰거나 다시 호출하지 않는다. 원본·정답·정책 또는 제품 코드가 고정 시점과 달라져도 실제 호출을 중단한다. 이후 평가를 새로 실행할 때는 기존 증거를 보존한 별도 평가 묶음을 사용한다.

실제 요청 단계는 `request-Memory.json`·`request-Foundry.json`에 전체 지시문과 입력 메시지를 보관한다. `response-시트명.json`에는 모델·시간·실제 HTTP 응답 본문과 제공된 토큰 사용량을, `result-시트명.json`에는 후처리 결과를 기록한다. 정확도 계산 결과는 `metrics.json`, 전체 필드별 대조는 `field-comparison.json`, 불일치 요약은 `field-comparison.md`, 검색 가능한 대조표는 `field-comparison.html`로 생성한다. 호출 전에 실패하면 응답 파일이 아직 생성되지 않을 수 있다.

디스크 통합문서 보관본과 열린 Excel의 셀 스냅샷은 구분한다. 제품에 전달하는 값과 정답의 직접 근거는 열린 Excel에서 읽은 `source-snapshot.json`이다. 원본 파일을 앱에서 다시 저장하거나 변경하지 않는다. 보고서의 전체 결론은 [기존 테스트 문서](../be-007-ai-extraction.md)에 기록한다.
