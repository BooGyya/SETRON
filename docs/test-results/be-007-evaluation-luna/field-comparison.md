# BE-007 전체 필드 대조표

원본에서 먼저 만든 정답과 실제 응답을 비교했다. 전체 세부 행은 field-comparison.json에 보존한다.

| 단계 | 평가 필드 | 일치 | 일치율 | 비어 있지 않은 정답 일치율 | 커버리지 |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw | 2160 | 2018 | 93.4259% | 87.1571% | 100.0% |
| normalized | 2061 | 1857 | 90.1019% | 86.0606% | 93.9352% |

## 불일치와 누락

판정 보류는 아래 오류 표에서 제외하고 전체 JSON에 유지한다.

| 시트 | 단계 | 원본 레코드 | 필드 | 정답 | 실제 | 판정 | 근거 규칙 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Memory | raw | Memory:E4 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E5 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E5 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E6 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E6 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E7 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E7 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E8 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E9 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E10 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E11 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E12 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E12 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E13 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E13 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E14 | model_name | "M-C3" | "M-C3 (6각) (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E14 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E15 | model_name | "M-C3" | "M-C3 (6각) (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E15 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E16 | model_name | "M-C3" | "M-C3 (6각) (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E16 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E17 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E17 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E18 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E18 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E19 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E19 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E20 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E20 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E21 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E22 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E23 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E24 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E25 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E26 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E27 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E27 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E28 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E28 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E29 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E29 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E30 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E30 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E31 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E31 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E32 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E32 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E33 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E33 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E34 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E34 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E35 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E35 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E36 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E36 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E37 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E41 | model_name | "Inline Ox" | "Inline Ox (CIP)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E41 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E41 | modification_type | "CIP" | null | omitted_value | explicit_modification_annotation |
| Memory | raw | Memory:E42 | model_name | "Inline Ox" | "Inline Ox (CIP)" | mismatch | split_model_annotations |
| Memory | raw | Memory:E42 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E42 | modification_type | "CIP" | null | omitted_value | explicit_modification_annotation |
| Memory | raw | Memory:E43 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E44 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E45 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E46 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E47 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E48 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E49 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E50 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E51 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E52 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E53 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E54 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E55 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E56 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E57 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E58 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E59 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E60 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E61 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E62 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E63 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E64 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E65 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E66 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E67 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E68 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E69 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E70 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E71 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E72 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E73 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E74 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E75 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E76 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E77 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E78 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E79 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E80 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E81 | structure_type | null | "M-O2" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E82 | structure_type | null | "M-O2" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E83 | structure_type | null | "M-O3" | invented_value | explicit_structure_annotation |
| Memory | raw | Memory:E84 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E85 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E86 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E87 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E88 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E89 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E90 | structure_type | "Cluster" | null | omitted_value | explicit_structure_annotation |
| Memory | raw | Memory:E91 | structure_type | "Inline" | null | omitted_value | explicit_structure_annotation |
| Memory | normalized | Memory:E4 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E5 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E5 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E5 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E6 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E6 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E6 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E7 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E7 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E7 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E8 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E9 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E10 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E11 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E12 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E12 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E12 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E13 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E13 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E13 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E14 | model_name | "M-C3" | "M-C3 (6각) (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E14 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E14 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E15 | model_name | "M-C3" | "M-C3 (6각) (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E15 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E15 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E16 | model_name | "M-C3" | "M-C3 (6각) (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E16 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E16 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E17 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E17 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E17 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E18 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E18 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E18 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E19 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E19 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E19 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E20 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E20 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E20 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E21 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E22 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E23 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E24 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E25 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E26 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E27 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E27 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E27 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E28 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E28 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E28 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E29 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E29 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E29 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E30 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E30 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E30 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E31 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E31 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E31 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E32 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E32 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E32 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E33 | model_name | "M-C3" | "M-C3 (구UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E33 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E33 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E34 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E34 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E34 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E35 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E35 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E35 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E36 | model_name | "M-C3" | "M-C3 (통합UI)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E36 | family_name | "C3" | null | omitted_value | confirmed_c3_shared_family |
| Memory | normalized | Memory:E36 | structure_type | null | "M-C3" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E37 | structure_type | null | "M-C4" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E41 | model_name | "Inline Ox" | "Inline Ox (CIP)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E41 | structure_type | "INLINE" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E41 | modification_type | "CIP" | null | omitted_value | explicit_modification_annotation |
| Memory | normalized | Memory:E42 | model_name | "Inline Ox" | "Inline Ox (CIP)" | mismatch | confirmed_model_normalization |
| Memory | normalized | Memory:E42 | structure_type | "INLINE" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E42 | modification_type | "CIP" | null | omitted_value | explicit_modification_annotation |
| Memory | normalized | Memory:E43 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E44 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E45 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E46 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E47 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E48 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E49 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E50 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E51 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E52 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E53 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E54 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E55 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E56 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E57 | structure_type | "INLINE" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E58 | structure_type | "INLINE" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E77 | structure_type | "INLINE" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E78 | structure_type | "INLINE" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E79 | structure_type | "INLINE" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E80 | structure_type | "CLUSTER" | null | omitted_value | structure_uppercase |
| Memory | normalized | Memory:E81 | structure_type | null | "M-O2" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E82 | structure_type | null | "M-O2" | invented_value | explicit_structure_annotation |
| Memory | normalized | Memory:E83 | structure_type | null | "M-O3" | invented_value | explicit_structure_annotation |
| Foundry | raw | Foundry:D4 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D5 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D6 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D7 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D8 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D9 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D10 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D11 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D12 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D13 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D14 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D15 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D16 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D17 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D18 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D19 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D20 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D21 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D22 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D23 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D24 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D25 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | raw | Foundry:D54 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | raw | Foundry:D55 | line | null | "MFAB" | invented_value | actual_merge_only |
| Foundry | raw | Foundry:D55 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | raw | Foundry:D56 | line | null | "MFAB" | invented_value | actual_merge_only |
| Foundry | raw | Foundry:D56 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | raw | Foundry:D57:PM1 | model_name | "O3" | "M-O3" | mismatch | split_model_annotations |
| Foundry | raw | Foundry:D57:PM2 | model_name | "O3" | "M-O3" | mismatch | split_model_annotations |
| Foundry | raw | Foundry:D57:PM2 | pm_name | "2" | "PM2" | mismatch | explicit_pm_list |
| Foundry | raw | Foundry:D57:PM4 | pm_name | "4" | "PM4" | mismatch | explicit_pm_list |
| Foundry | normalized | Foundry:D4 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D5 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D6 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D7 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D8 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D9 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D10 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D11 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D12 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D13 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D14 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D15 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D16 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D17 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D18 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D19 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D20 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D21 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D22 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D23 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D24 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D25 | site | "기흥" | "(기흥)" | mismatch | explicit_site_in_line |
| Foundry | normalized | Foundry:D54 | group_name | "OXIDE" | null | omitted_value | confirmed_group_normalization |
| Foundry | normalized | Foundry:D54 | model_name | "M-O5 Prime" | "M-O5(Prime)" | mismatch | confirmed_model_normalization |
| Foundry | normalized | Foundry:D54 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | normalized | Foundry:D55 | line | null | "MFAB" | invented_value | actual_merge_only |
| Foundry | normalized | Foundry:D55 | group_name | "OXIDE" | null | omitted_value | confirmed_group_normalization |
| Foundry | normalized | Foundry:D55 | model_name | "M-O5 Prime" | "M-O5(Prime)" | mismatch | confirmed_model_normalization |
| Foundry | normalized | Foundry:D55 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | normalized | Foundry:D56 | line | null | "MFAB" | invented_value | actual_merge_only |
| Foundry | normalized | Foundry:D56 | group_name | "OXIDE" | null | omitted_value | confirmed_group_normalization |
| Foundry | normalized | Foundry:D56 | model_name | "M-O5 Prime" | "M-O5(Prime)" | mismatch | confirmed_model_normalization |
| Foundry | normalized | Foundry:D56 | modification_type | "Prime" | null | omitted_value | explicit_modification_annotation |
| Foundry | normalized | Foundry:D57:PM1 | group_name | "OXIDE" | null | omitted_value | confirmed_group_normalization |
| Foundry | normalized | Foundry:D57:PM1 | model_name | "M-O3" | null | omitted_value | confirmed_model_normalization |
| Foundry | normalized | Foundry:D57:PM2 | base_date | null | null | missing_record | source_absent |
| Foundry | normalized | Foundry:D57:PM2 | site | null | null | missing_record | explicit_site_in_line |
| Foundry | normalized | Foundry:D57:PM2 | line | "NRD (MR2동)" | null | missing_record | actual_merge_only |
| Foundry | normalized | Foundry:D57:PM2 | group_name | "OXIDE" | null | missing_record | confirmed_group_normalization |
| Foundry | normalized | Foundry:D57:PM2 | model_name | "M-O3" | null | missing_record | confirmed_model_normalization |
| Foundry | normalized | Foundry:D57:PM2 | production_area | null | null | missing_record | source_absent |
| Foundry | normalized | Foundry:D57:PM2 | equipment_code | "ESON81" | null | missing_record | equipment_column_exact |
| Foundry | normalized | Foundry:D57:PM2 | pm_name | "PM2" | null | missing_record | explicit_pm_position |
| Foundry | normalized | Foundry:D57:PM2 | version_raw | "v4.50.1.1_a" | null | missing_record | sw_column_or_labeled_component |
| Foundry | normalized | Foundry:D57:PM2 | ui_type | null | null | missing_record | explicit_ui_annotation |
| Foundry | normalized | Foundry:D57:PM2 | structure_type | null | null | missing_record | explicit_structure_annotation |
| Foundry | normalized | Foundry:D57:PM2 | geometry_type | null | null | missing_record | explicit_geometry_annotation |
| Foundry | normalized | Foundry:D57:PM2 | modification_type | null | null | missing_record | explicit_modification_annotation |
| Foundry | normalized | Foundry:D57:PM2 | engine_version | null | null | missing_record | engine_column_only |
| Foundry | normalized | Foundry:D57:PM2 | epd_version | null | null | missing_record | epd_column_only |
| Foundry | normalized | Foundry:D57:PM4 | base_date | null | null | missing_record | source_absent |
| Foundry | normalized | Foundry:D57:PM4 | site | null | null | missing_record | explicit_site_in_line |
| Foundry | normalized | Foundry:D57:PM4 | line | "NRD (MR2동)" | null | missing_record | actual_merge_only |
| Foundry | normalized | Foundry:D57:PM4 | group_name | "OXIDE" | null | missing_record | confirmed_group_normalization |
| Foundry | normalized | Foundry:D57:PM4 | model_name | "M-O2" | null | missing_record | confirmed_model_normalization |
| Foundry | normalized | Foundry:D57:PM4 | production_area | null | null | missing_record | source_absent |
| Foundry | normalized | Foundry:D57:PM4 | equipment_code | "ESON81" | null | missing_record | equipment_column_exact |
| Foundry | normalized | Foundry:D57:PM4 | pm_name | "PM4" | null | missing_record | explicit_pm_position |
| Foundry | normalized | Foundry:D57:PM4 | version_raw | "v3.53_F2" | null | missing_record | sw_column_or_labeled_component |
| Foundry | normalized | Foundry:D57:PM4 | ui_type | null | null | missing_record | explicit_ui_annotation |
| Foundry | normalized | Foundry:D57:PM4 | structure_type | null | null | missing_record | explicit_structure_annotation |
| Foundry | normalized | Foundry:D57:PM4 | geometry_type | null | null | missing_record | explicit_geometry_annotation |
| Foundry | normalized | Foundry:D57:PM4 | modification_type | null | null | missing_record | explicit_modification_annotation |
| Foundry | normalized | Foundry:D57:PM4 | engine_version | null | null | missing_record | engine_column_only |
| Foundry | normalized | Foundry:D57:PM4 | epd_version | null | null | missing_record | epd_column_only |
| Foundry | normalized | EXTRA:47 | base_date | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | site | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | line | null | "NRD (MR2동)" | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | group_name | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | model_name | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | production_area | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | equipment_code | null | "ESON81" | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | pm_name | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | family_name | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | version_raw | null | "v4.50.1.1_a" | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | ui_type | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | structure_type | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | geometry_type | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | modification_type | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | engine_version | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:47 | epd_version | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | base_date | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | site | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | line | null | "NRD (MR2동)" | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | group_name | null | "OXIDE" | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | model_name | null | "M-O2" | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | production_area | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | equipment_code | null | "ESON81" | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | pm_name | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | family_name | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | version_raw | null | "v3.53_F2" | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | ui_type | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | structure_type | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | geometry_type | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | modification_type | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | engine_version | null | null | extra_record | no_unique_source_record |
| Foundry | normalized | EXTRA:49 | epd_version | null | null | extra_record | no_unique_source_record |
