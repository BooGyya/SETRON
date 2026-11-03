# -*- coding: utf-8 -*-
"""E2 채점: 예문 30개를 LLM(또는 mock)에 넣고 기대 필드와 비교해 정답률을 낸다.
    python eval_query.py            # .env 의 설정으로 실제 호출 (GMS)
    SVHM_LLM_MOCK=1 python eval_query.py   # 오프라인 mock
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svhm.ai.llm_client import LLMClient
from svhm.ai.query_parser import parse_query

ex = json.load(open(os.path.join(os.path.dirname(__file__), "svhm", "ai", "query_examples.json"), encoding="utf-8"))
client = LLMClient()
print("설정:", client.s.describe())

def get(d, path):
    cur = d
    for p in path.split("."):
        cur = cur.get(p) if isinstance(cur, dict) else None
        if cur is None: return None
    return cur

ok, field_ok, field_total = 0, 0, 0
for e in ex:
    try:
        qc = parse_query(e["q"], client).model_dump()
    except Exception as err:
        print("!! 오류:", e["q"], err); continue
    fails = []
    for k, v in e["expect"].items():
        field_total += 1
        if k == "item_keyword_contains":
            hit = bool(qc.get("item_keyword")) and v.lower() in qc["item_keyword"].lower()
        else:
            got = get(qc, k)
            hit = (set(v) == set(got or [])) if isinstance(v, list) else (got == v)
        if hit: field_ok += 1
        else: fails.append((k, v, get(qc, k) if k != "item_keyword_contains" else qc.get("item_keyword")))
    if not fails: ok += 1; print("O ", e["q"])
    else: print("X ", e["q"], "→", fails)
print(f"\n문장 정답률 {ok}/{len(ex)} ({ok/len(ex)*100:.0f}%), 필드 정답률 {field_ok}/{field_total} ({field_ok/field_total*100:.0f}%)")
