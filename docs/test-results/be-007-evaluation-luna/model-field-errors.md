# BE-007 모델별 필드 오류 비교

동일한 정답 필드에 두 모델의 실제 값을 나란히 기록했다. 원문 표기 차이도 사전 고정한 엄격한 계약에 따라 판정한다.

전체 필드와 판정 보류의 반환값은 [전체 JSON](model-field-comparison.json), 집계·조건 검증은 [모델 비교 JSON](model-comparison.json)에 보관한다. 대응되지 않은 실제 결과는 [추가 레코드 JSON](model-extra-records.json)과 아래 추가 레코드 표에 남긴다.

| 단계 | 둘 다 일치 | sol만 일치 | luna만 일치 | 둘 다 불일치 | 보류 |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw | 2016 | 135 | 2 | 7 | 0 |
| normalized | 1857 | 159 | 0 | 13 | 131 |

## 한 모델 이상 불일치한 정답 필드

| 시트 | 단계 | 원본 레코드 | 필드 | 정답 | sol 실제 / 판정 | luna 실제 / 판정 | 정답 근거 셀 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Memory | raw | Memory:E4 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E5 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D5 |
| Memory | raw | Memory:E5 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E6 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D6 |
| Memory | raw | Memory:E6 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E7 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D7 |
| Memory | raw | Memory:E7 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E8 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E9 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E10 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E11 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E12 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D12 |
| Memory | raw | Memory:E12 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E13 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D13 |
| Memory | raw | Memory:E13 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E14 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (6각) (통합UI)" / mismatch | D14 |
| Memory | raw | Memory:E14 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E15 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (6각) (통합UI)" / mismatch | D15 |
| Memory | raw | Memory:E15 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E16 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (6각) (통합UI)" / mismatch | D16 |
| Memory | raw | Memory:E16 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E17 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D17 |
| Memory | raw | Memory:E17 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E18 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D18 |
| Memory | raw | Memory:E18 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E19 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D19 |
| Memory | raw | Memory:E19 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E20 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D20 |
| Memory | raw | Memory:E20 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E21 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E22 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E23 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E24 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E25 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E26 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E27 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D27 |
| Memory | raw | Memory:E27 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E28 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D28 |
| Memory | raw | Memory:E28 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E29 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D29 |
| Memory | raw | Memory:E29 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E30 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D30 |
| Memory | raw | Memory:E30 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E31 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D31 |
| Memory | raw | Memory:E31 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E32 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D32 |
| Memory | raw | Memory:E32 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E33 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D33 |
| Memory | raw | Memory:E33 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E34 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D34 |
| Memory | raw | Memory:E34 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E35 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D35 |
| Memory | raw | Memory:E35 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E36 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D36 |
| Memory | raw | Memory:E36 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | raw | Memory:E37 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | raw | Memory:E41 | model_name | "Inline Ox" | "Inline Ox (CIP)" / mismatch | "Inline Ox (CIP)" / mismatch | D41 |
| Memory | raw | Memory:E41 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D41 |
| Memory | raw | Memory:E41 | modification_type | "CIP" | null / omitted_value | null / omitted_value | D41 |
| Memory | raw | Memory:E42 | model_name | "Inline Ox" | "Inline Ox (CIP)" / mismatch | "Inline Ox (CIP)" / mismatch | D42 |
| Memory | raw | Memory:E42 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D42 |
| Memory | raw | Memory:E42 | modification_type | "CIP" | null / omitted_value | null / omitted_value | D42 |
| Memory | raw | Memory:E43 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D43 |
| Memory | raw | Memory:E44 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D44 |
| Memory | raw | Memory:E45 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D45 |
| Memory | raw | Memory:E46 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D46 |
| Memory | raw | Memory:E47 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D47 |
| Memory | raw | Memory:E48 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D48 |
| Memory | raw | Memory:E49 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D49 |
| Memory | raw | Memory:E50 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D50 |
| Memory | raw | Memory:E51 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D51 |
| Memory | raw | Memory:E52 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D52 |
| Memory | raw | Memory:E53 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D53 |
| Memory | raw | Memory:E54 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D54 |
| Memory | raw | Memory:E55 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D55 |
| Memory | raw | Memory:E56 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D56 |
| Memory | raw | Memory:E57 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D57 |
| Memory | raw | Memory:E58 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D58 |
| Memory | raw | Memory:E59 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D59 |
| Memory | raw | Memory:E60 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D60 |
| Memory | raw | Memory:E61 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D61 |
| Memory | raw | Memory:E62 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D62 |
| Memory | raw | Memory:E63 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D63 |
| Memory | raw | Memory:E64 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D64 |
| Memory | raw | Memory:E65 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D65 |
| Memory | raw | Memory:E66 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D66 |
| Memory | raw | Memory:E67 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D67 |
| Memory | raw | Memory:E68 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D68 |
| Memory | raw | Memory:E69 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D69 |
| Memory | raw | Memory:E70 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D70 |
| Memory | raw | Memory:E71 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D71 |
| Memory | raw | Memory:E72 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D72 |
| Memory | raw | Memory:E73 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D73 |
| Memory | raw | Memory:E74 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D74 |
| Memory | raw | Memory:E75 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D75 |
| Memory | raw | Memory:E76 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D76 |
| Memory | raw | Memory:E77 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D77 |
| Memory | raw | Memory:E78 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D78 |
| Memory | raw | Memory:E79 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D79 |
| Memory | raw | Memory:E80 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D80 |
| Memory | raw | Memory:E81 | structure_type | null | null / correct | "M-O2" / invented_value |  |
| Memory | raw | Memory:E82 | structure_type | null | null / correct | "M-O2" / invented_value |  |
| Memory | raw | Memory:E83 | structure_type | null | null / correct | "M-O3" / invented_value |  |
| Memory | raw | Memory:E84 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D84 |
| Memory | raw | Memory:E85 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D85 |
| Memory | raw | Memory:E86 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D86 |
| Memory | raw | Memory:E87 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D87 |
| Memory | raw | Memory:E88 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D88 |
| Memory | raw | Memory:E89 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D89 |
| Memory | raw | Memory:E90 | structure_type | "Cluster" | "Cluster" / correct | null / omitted_value | D90 |
| Memory | raw | Memory:E91 | structure_type | "Inline" | "Inline" / correct | null / omitted_value | D91 |
| Memory | normalized | Memory:E4 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E5 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D5 |
| Memory | normalized | Memory:E5 | family_name | "C3" | "C3" / correct | null / omitted_value | D5 |
| Memory | normalized | Memory:E5 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E6 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D6 |
| Memory | normalized | Memory:E6 | family_name | "C3" | "C3" / correct | null / omitted_value | D6 |
| Memory | normalized | Memory:E6 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E7 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D7 |
| Memory | normalized | Memory:E7 | family_name | "C3" | "C3" / correct | null / omitted_value | D7 |
| Memory | normalized | Memory:E7 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E8 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E9 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E10 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E11 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E12 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D12 |
| Memory | normalized | Memory:E12 | family_name | "C3" | "C3" / correct | null / omitted_value | D12 |
| Memory | normalized | Memory:E12 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E13 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D13 |
| Memory | normalized | Memory:E13 | family_name | "C3" | "C3" / correct | null / omitted_value | D13 |
| Memory | normalized | Memory:E13 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E14 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (6각) (통합UI)" / mismatch | D14 |
| Memory | normalized | Memory:E14 | family_name | "C3" | "C3" / correct | null / omitted_value | D14 |
| Memory | normalized | Memory:E14 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E15 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (6각) (통합UI)" / mismatch | D15 |
| Memory | normalized | Memory:E15 | family_name | "C3" | "C3" / correct | null / omitted_value | D15 |
| Memory | normalized | Memory:E15 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E16 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (6각) (통합UI)" / mismatch | D16 |
| Memory | normalized | Memory:E16 | family_name | "C3" | "C3" / correct | null / omitted_value | D16 |
| Memory | normalized | Memory:E16 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E17 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D17 |
| Memory | normalized | Memory:E17 | family_name | "C3" | "C3" / correct | null / omitted_value | D17 |
| Memory | normalized | Memory:E17 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E18 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D18 |
| Memory | normalized | Memory:E18 | family_name | "C3" | "C3" / correct | null / omitted_value | D18 |
| Memory | normalized | Memory:E18 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E19 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D19 |
| Memory | normalized | Memory:E19 | family_name | "C3" | "C3" / correct | null / omitted_value | D19 |
| Memory | normalized | Memory:E19 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E20 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D20 |
| Memory | normalized | Memory:E20 | family_name | "C3" | "C3" / correct | null / omitted_value | D20 |
| Memory | normalized | Memory:E20 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E21 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E22 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E23 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E24 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E25 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E26 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E27 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D27 |
| Memory | normalized | Memory:E27 | family_name | "C3" | "C3" / correct | null / omitted_value | D27 |
| Memory | normalized | Memory:E27 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E28 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D28 |
| Memory | normalized | Memory:E28 | family_name | "C3" | "C3" / correct | null / omitted_value | D28 |
| Memory | normalized | Memory:E28 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E29 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D29 |
| Memory | normalized | Memory:E29 | family_name | "C3" | "C3" / correct | null / omitted_value | D29 |
| Memory | normalized | Memory:E29 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E30 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D30 |
| Memory | normalized | Memory:E30 | family_name | "C3" | "C3" / correct | null / omitted_value | D30 |
| Memory | normalized | Memory:E30 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E31 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D31 |
| Memory | normalized | Memory:E31 | family_name | "C3" | "C3" / correct | null / omitted_value | D31 |
| Memory | normalized | Memory:E31 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E32 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D32 |
| Memory | normalized | Memory:E32 | family_name | "C3" | "C3" / correct | null / omitted_value | D32 |
| Memory | normalized | Memory:E32 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E33 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (구UI)" / mismatch | D33 |
| Memory | normalized | Memory:E33 | family_name | "C3" | "C3" / correct | null / omitted_value | D33 |
| Memory | normalized | Memory:E33 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E34 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D34 |
| Memory | normalized | Memory:E34 | family_name | "C3" | "C3" / correct | null / omitted_value | D34 |
| Memory | normalized | Memory:E34 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E35 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D35 |
| Memory | normalized | Memory:E35 | family_name | "C3" | "C3" / correct | null / omitted_value | D35 |
| Memory | normalized | Memory:E35 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E36 | model_name | "M-C3" | "M-C3" / correct | "M-C3 (통합UI)" / mismatch | D36 |
| Memory | normalized | Memory:E36 | family_name | "C3" | "C3" / correct | null / omitted_value | D36 |
| Memory | normalized | Memory:E36 | structure_type | null | null / correct | "M-C3" / invented_value |  |
| Memory | normalized | Memory:E37 | structure_type | null | null / correct | "M-C4" / invented_value |  |
| Memory | normalized | Memory:E41 | model_name | "Inline Ox" | "Inline Ox (CIP)" / mismatch | "Inline Ox (CIP)" / mismatch | D41 |
| Memory | normalized | Memory:E41 | structure_type | "INLINE" | "INLINE" / correct | null / omitted_value | D41 |
| Memory | normalized | Memory:E41 | modification_type | "CIP" | null / omitted_value | null / omitted_value | D41 |
| Memory | normalized | Memory:E42 | model_name | "Inline Ox" | "Inline Ox (CIP)" / mismatch | "Inline Ox (CIP)" / mismatch | D42 |
| Memory | normalized | Memory:E42 | structure_type | "INLINE" | "INLINE" / correct | null / omitted_value | D42 |
| Memory | normalized | Memory:E42 | modification_type | "CIP" | null / omitted_value | null / omitted_value | D42 |
| Memory | normalized | Memory:E43 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D43 |
| Memory | normalized | Memory:E44 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D44 |
| Memory | normalized | Memory:E45 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D45 |
| Memory | normalized | Memory:E46 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D46 |
| Memory | normalized | Memory:E47 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D47 |
| Memory | normalized | Memory:E48 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D48 |
| Memory | normalized | Memory:E49 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D49 |
| Memory | normalized | Memory:E50 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D50 |
| Memory | normalized | Memory:E51 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D51 |
| Memory | normalized | Memory:E52 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D52 |
| Memory | normalized | Memory:E53 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D53 |
| Memory | normalized | Memory:E54 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D54 |
| Memory | normalized | Memory:E55 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D55 |
| Memory | normalized | Memory:E56 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D56 |
| Memory | normalized | Memory:E57 | structure_type | "INLINE" | "INLINE" / correct | null / omitted_value | D57 |
| Memory | normalized | Memory:E58 | structure_type | "INLINE" | "INLINE" / correct | null / omitted_value | D58 |
| Memory | normalized | Memory:E77 | structure_type | "INLINE" | "INLINE" / correct | null / omitted_value | D77 |
| Memory | normalized | Memory:E78 | structure_type | "INLINE" | "INLINE" / correct | null / omitted_value | D78 |
| Memory | normalized | Memory:E79 | structure_type | "INLINE" | "INLINE" / correct | null / omitted_value | D79 |
| Memory | normalized | Memory:E80 | structure_type | "CLUSTER" | "CLUSTER" / correct | null / omitted_value | D80 |
| Memory | normalized | Memory:E81 | structure_type | null | null / correct | "M-O2" / invented_value |  |
| Memory | normalized | Memory:E82 | structure_type | null | null / correct | "M-O2" / invented_value |  |
| Memory | normalized | Memory:E83 | structure_type | null | null / correct | "M-O3" / invented_value |  |
| Foundry | raw | Foundry:D4 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B4 |
| Foundry | raw | Foundry:D5 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B4 |
| Foundry | raw | Foundry:D6 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B4 |
| Foundry | raw | Foundry:D7 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | raw | Foundry:D8 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | raw | Foundry:D9 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | raw | Foundry:D10 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | raw | Foundry:D11 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | raw | Foundry:D12 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | raw | Foundry:D13 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | raw | Foundry:D14 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | raw | Foundry:D15 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | raw | Foundry:D16 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | raw | Foundry:D17 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | raw | Foundry:D18 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | raw | Foundry:D19 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | raw | Foundry:D20 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | raw | Foundry:D21 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | raw | Foundry:D22 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | raw | Foundry:D23 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | raw | Foundry:D24 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | raw | Foundry:D25 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | raw | Foundry:D54 | modification_type | "Prime" | null / omitted_value | null / omitted_value | C54 |
| Foundry | raw | Foundry:D55 | line | null | null / correct | "MFAB" / invented_value | B55 |
| Foundry | raw | Foundry:D55 | modification_type | "Prime" | null / omitted_value | null / omitted_value | C55 |
| Foundry | raw | Foundry:D56 | line | null | null / correct | "MFAB" / invented_value | B56 |
| Foundry | raw | Foundry:D56 | modification_type | "Prime" | null / omitted_value | null / omitted_value | C56 |
| Foundry | raw | Foundry:D57:PM1 | model_name | "O3" | "O3" / correct | "M-O3" / mismatch | C57 |
| Foundry | raw | Foundry:D57:PM2 | model_name | "O3" | "O3" / correct | "M-O3" / mismatch | C57 |
| Foundry | raw | Foundry:D57:PM2 | pm_name | "2" | "2" / correct | "PM2" / mismatch | P57 |
| Foundry | raw | Foundry:D57:PM3 | model_name | "M-O2" | "O2" / mismatch | "M-O2" / correct | C57 |
| Foundry | raw | Foundry:D57:PM4 | model_name | "M-O2" | "O2" / mismatch | "M-O2" / correct | C57 |
| Foundry | raw | Foundry:D57:PM4 | pm_name | "4" | "4" / correct | "PM4" / mismatch | P57 |
| Foundry | normalized | Foundry:D4 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B4 |
| Foundry | normalized | Foundry:D5 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B4 |
| Foundry | normalized | Foundry:D6 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B4 |
| Foundry | normalized | Foundry:D7 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | normalized | Foundry:D8 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | normalized | Foundry:D9 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | normalized | Foundry:D10 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | normalized | Foundry:D11 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | normalized | Foundry:D12 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | normalized | Foundry:D13 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | normalized | Foundry:D14 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | normalized | Foundry:D15 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B7 |
| Foundry | normalized | Foundry:D16 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | normalized | Foundry:D17 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | normalized | Foundry:D18 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | normalized | Foundry:D19 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | normalized | Foundry:D20 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | normalized | Foundry:D21 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | normalized | Foundry:D22 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | normalized | Foundry:D23 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | normalized | Foundry:D24 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | normalized | Foundry:D25 | site | "기흥" | "기흥" / correct | "(기흥)" / mismatch | B16 |
| Foundry | normalized | Foundry:D54 | group_name | "OXIDE" | null / omitted_value | null / omitted_value | C54 |
| Foundry | normalized | Foundry:D54 | model_name | "M-O5 Prime" | "M-O5(Prime)" / mismatch | "M-O5(Prime)" / mismatch | C54 |
| Foundry | normalized | Foundry:D54 | modification_type | "Prime" | null / omitted_value | null / omitted_value | C54 |
| Foundry | normalized | Foundry:D55 | line | null | null / correct | "MFAB" / invented_value | B55 |
| Foundry | normalized | Foundry:D55 | group_name | "OXIDE" | null / omitted_value | null / omitted_value | C55 |
| Foundry | normalized | Foundry:D55 | model_name | "M-O5 Prime" | "M-O5(Prime)" / mismatch | "M-O5(Prime)" / mismatch | C55 |
| Foundry | normalized | Foundry:D55 | modification_type | "Prime" | null / omitted_value | null / omitted_value | C55 |
| Foundry | normalized | Foundry:D56 | line | null | null / correct | "MFAB" / invented_value | B56 |
| Foundry | normalized | Foundry:D56 | group_name | "OXIDE" | null / omitted_value | null / omitted_value | C56 |
| Foundry | normalized | Foundry:D56 | model_name | "M-O5 Prime" | "M-O5(Prime)" / mismatch | "M-O5(Prime)" / mismatch | C56 |
| Foundry | normalized | Foundry:D56 | modification_type | "Prime" | null / omitted_value | null / omitted_value | C56 |
| Foundry | normalized | Foundry:D57:PM1 | group_name | "OXIDE" | "OXIDE" / correct | null / omitted_value | C57 |
| Foundry | normalized | Foundry:D57:PM1 | model_name | "M-O3" | "M-O3" / correct | null / omitted_value | C57 |
| Foundry | normalized | Foundry:D57:PM2 | base_date | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM2 | site | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM2 | line | "NRD (MR2동)" | "NRD (MR2동)" / correct | null / missing_record | B57 |
| Foundry | normalized | Foundry:D57:PM2 | group_name | "OXIDE" | "OXIDE" / correct | null / missing_record | C57 |
| Foundry | normalized | Foundry:D57:PM2 | model_name | "M-O3" | "M-O3" / correct | null / missing_record | C57 |
| Foundry | normalized | Foundry:D57:PM2 | production_area | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM2 | equipment_code | "ESON81" | "ESON81" / correct | null / missing_record | D57 |
| Foundry | normalized | Foundry:D57:PM2 | pm_name | "PM2" | "PM2" / correct | null / missing_record | P57 |
| Foundry | normalized | Foundry:D57:PM2 | version_raw | "v4.50.1.1_a" | "v4.50.1.1_a" / correct | null / missing_record | G57 |
| Foundry | normalized | Foundry:D57:PM2 | ui_type | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM2 | structure_type | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM2 | geometry_type | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM2 | modification_type | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM2 | engine_version | null | null / correct | null / missing_record | H57 |
| Foundry | normalized | Foundry:D57:PM2 | epd_version | null | null / correct | null / missing_record | J57 |
| Foundry | normalized | Foundry:D57:PM4 | base_date | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM4 | site | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM4 | line | "NRD (MR2동)" | "NRD (MR2동)" / correct | null / missing_record | B57 |
| Foundry | normalized | Foundry:D57:PM4 | group_name | "OXIDE" | "OXIDE" / correct | null / missing_record | C57 |
| Foundry | normalized | Foundry:D57:PM4 | model_name | "M-O2" | "M-O2" / correct | null / missing_record | C57 |
| Foundry | normalized | Foundry:D57:PM4 | production_area | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM4 | equipment_code | "ESON81" | "ESON81" / correct | null / missing_record | D57 |
| Foundry | normalized | Foundry:D57:PM4 | pm_name | "PM4" | "PM4" / correct | null / missing_record | P57 |
| Foundry | normalized | Foundry:D57:PM4 | version_raw | "v3.53_F2" | "v3.53_F2" / correct | null / missing_record | G57 |
| Foundry | normalized | Foundry:D57:PM4 | ui_type | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM4 | structure_type | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM4 | geometry_type | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM4 | modification_type | null | null / correct | null / missing_record |  |
| Foundry | normalized | Foundry:D57:PM4 | engine_version | null | null / correct | null / missing_record | H57 |
| Foundry | normalized | Foundry:D57:PM4 | epd_version | null | null / correct | null / missing_record | J57 |

## 대응되지 않은 실제 결과

missing_record는 기대 PM 위치에 대응하지 못했다는 뜻이다. 실제 반환 행 자체가 사라졌다는 뜻과 구분한다. 위 표의 대응 실패와 아래 추가 결과는 같은 정렬 실패에 따른 누락·추가 페널티일 수 있다.

| 모델 | 시트 | 단계 | 결과 인덱스 | 필드 | 실제 값 | 추가 판정 이유 |
| --- | --- | --- | --- | --- | --- | --- |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | base_date | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | site | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | line | "NRD (MR2동)" | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | group_name | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | model_name | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | production_area | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | equipment_code | "ESON81" | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | pm_name | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | family_name | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | version_raw | "v4.50.1.1_a" | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | ui_type | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | structure_type | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | geometry_type | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | modification_type | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | engine_version | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:47 | epd_version | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | base_date | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | site | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | line | "NRD (MR2동)" | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | group_name | "OXIDE" | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | model_name | "M-O2" | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | production_area | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | equipment_code | "ESON81" | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | pm_name | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | family_name | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | version_raw | "v3.53_F2" | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | ui_type | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | structure_type | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | geometry_type | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | modification_type | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | engine_version | null | no_unique_source_record |
| gpt-6-luna | Foundry | normalized | EXTRA:49 | epd_version | null | no_unique_source_record |
