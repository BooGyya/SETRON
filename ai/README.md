# ai/ — 데이터·AI 파트 (B106)

현황표·릴리즈노트 파서, 번역표·버전 맞추기, 집계·위험·예측, AI 연동(자연어→조회 조건, 열 매핑, 보고서). 자급자족 폴더: 이 폴더 안에서만 실행.

```
ai/
├ svhm/excel/   status_parser.py, release_parser.py        ← A·B
├ svhm/core/    refdata, translate, matcher, pipeline, risk, forecast   ← C·D·E
├ svhm/ai/      llm_client, query_parser(+예문 30), column_mapper, report_writer   ← E1·E2·E3
├ tests/        골든테스트 80개, data/ (합성 데이터 v2 + 멘토 교육용 원본)
├ demo.py demo_release.py demo_match.py demo_risk.py demo_ai.py eval_query.py
├ docs/         설명 문서 7편
└ .env.example  AI 설정 견본 (복사해서 .env — git 에 안 올림)
```

실행 (이 폴더에서)
```
pip install -r requirements.txt
python -m pytest tests -q                                   # 80 passed
python demo_match.py tests/data/평택설비현황표_교육용_2026-10-05.xlsx --release tests/data --mentor tests/data --out parsed_match
python demo_risk.py  --status tests/data --release tests/data --mentor tests/data --site 평택
python demo_ai.py query "평택 O2 중 두 칸 이상 뒤처진 설비만 보여줘"   # .env 필요
```
백엔드 연동 포인트: `svhm.core.pipeline.match_equipment` (설비 행 → 매칭 결과), `summarize`, `risk.assess_all`, `priority_list`, `forecast.forecast`, `ai.query_parser.parse_query` (조회 조건 JSON). 설명은 docs/ 참고.
