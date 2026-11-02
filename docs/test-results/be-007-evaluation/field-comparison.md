# BE-007 전체 필드 대조표

원본에서 먼저 만든 정답과 실제 응답을 비교했다. 전체 세부 행은 field-comparison.json에 보존한다.

| 단계 | 평가 필드 | 일치 | 일치율 | 비어 있지 않은 정답 일치율 | 커버리지 |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw | 2160 | 2151 | 99.5833% | 98.8778% | 100.0% |
| normalized | 2029 | 2016 | 99.3593% | 98.4242% | 93.9352% |

## 불일치와 누락

판정 보류는 아래 오류 표에서 제외하고 전체 JSON에 유지한다.

| 시트 | 단계 | 원본 레코드 | 필드 | 정답 | 실제 | 판정 | 근거 규칙 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Memory | raw | Memory:E41 | model_name | "Inline Ox" | "Inline Ox (CIP)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E41 | modification_type | "CIP" | null | omitted_value | explicit_modification_annotation |
| Memory | raw | Memory:E42 | model_name | "Inline Ox" | "Inline Ox (CIP)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E42 | modification_type | "CIP" | null | omitted_value | explicit_modification_annotation |
| Memory | normalized | Memory:E41 | model_name | "Inline Ox" | "Inline Ox (CIP)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E41 | modification_type | "CIP" | null | omitted_value | explicit_modification_annotation |
| Memory | normalized | Memory:E42 | model_name | "Inline Ox" | "Inline Ox (CIP)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E42 | modification_type | "CIP" | null | omitted_value | explicit_modification_annotation |
| Foundry | raw | Foundry:D54 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | raw | Foundry:D55 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | raw | Foundry:D56 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | raw | Foundry:D57:PM3 | model_name | "M-O2" | "O2" | mismatch | split_model_annotations |
| Foundry | raw | Foundry:D57:PM4 | model_name | "M-O2" | "O2" | mismatch | split_model_annotations |
| Foundry | normalized | Foundry:D54 | group_name | "OXIDE" | null | omitted_value | confirmed_group_normalization |
| Foundry | normalized | Foundry:D54 | model_name | "M-O5 Prime" | "M-O5(Prime)" | mismatch | confirmed_model_normalization |
| Foundry | normalized | Foundry:D54 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | normalized | Foundry:D55 | group_name | "OXIDE" | null | omitted_value | confirmed_group_normalization |
| Foundry | normalized | Foundry:D55 | model_name | "M-O5 Prime" | "M-O5(Prime)" | mismatch | confirmed_model_normalization |
| Foundry | normalized | Foundry:D55 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | normalized | Foundry:D56 | group_name | "OXIDE" | null | omitted_value | confirmed_group_normalization |
| Foundry | normalized | Foundry:D56 | model_name | "M-O5 Prime" | "M-O5(Prime)" | mismatch | confirmed_model_normalization |
| Foundry | normalized | Foundry:D56 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
