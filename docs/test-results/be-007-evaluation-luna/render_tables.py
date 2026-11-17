"""보존한 JSON 정답표·대조표를 네트워크 없이 볼 수 있는 HTML 표로 만든다."""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

TEMPLATE = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:light dark;font:15px/1.55 system-ui,sans-serif;--bg:#fff;--fg:#20242a;--muted:#596470;--line:#d8dee6;--bad:#fff0ed;--pending:#fff9dd}
@media(prefers-color-scheme:dark){:root{--bg:#181c22;--fg:#e8edf3;--muted:#b8c3d0;--line:#475260;--bad:#442923;--pending:#403a21}}
body{margin:0;background:var(--bg);color:var(--fg)}main{padding:24px;max-width:1800px;margin:auto}
h1{font-size:24px;margin:0 0 8px}p{margin:8px 0;color:var(--muted)}
form{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:20px 0}label{display:grid;gap:4px}
input,select,button{font:inherit;padding:7px 10px;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:4px}
input{min-width:240px}button{cursor:pointer}.scroll{overflow:auto;max-height:72vh;border-top:1px solid var(--line)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:9px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
th{position:sticky;top:0;background:var(--bg);white-space:nowrap}td{max-width:340px;overflow-wrap:anywhere}
tr.bad{background:var(--bad)}tr.pending{background:var(--pending)}code{font-family:ui-monospace,monospace}
nav{display:flex;gap:14px;align-items:center;margin-top:12px}#count{font-variant-numeric:tabular-nums}small{color:var(--muted)}
</style></head><body><main>
<h1>__TITLE__</h1><p>__DESCRIPTION__</p>
<form id="filters">
<label>단계<select id="stage"><option value="normalized">정규화 결과</option><option value="raw">AI 원문 표기</option><option value="">두 단계 모두</option></select></label>
<label>시트<select id="sheet"><option value="">전체</option><option>Memory</option><option>Foundry</option></select></label>
<label>필드<select id="field"><option value="">전체</option></select></label>
<label>판정<select id="verdict"><option value="">전체</option>__VERDICT_OPTIONS__</select></label>
<label>검색<input id="query" type="search" placeholder="호기·셀 주소·정답·실제 값 검색"></label>
<button type="reset">초기화</button></form>
<p id="count" aria-live="polite"></p><small>노란 행은 판정 보류, 붉은 행은 불일치·누락·추가다. null은 문자열이 아닌 빈 값이다.</small>
<div class="scroll"><table><thead><tr><th>시트</th><th>단계</th><th>원본 레코드 · 호기</th><th>필드</th><th>정답</th><th>실제</th><th>판정 / 상태</th><th>정답 근거 셀</th><th>실제 출처</th><th>규칙</th></tr></thead><tbody id="rows"></tbody></table></div>
<nav><button id="prev" type="button">이전</button><span id="page"></span><button id="next" type="button">다음</button><small>페이지당 100개 필드</small></nav>
</main><script>
const data=__DATA__,fields=__FIELDS__;const byId=id=>document.getElementById(id);let page=0,filtered=[];
for(const field of fields){const option=document.createElement('option');option.value=field;option.textContent=field;byId('field').append(option)}
const display=value=>value===null?'null':typeof value==='string'?value:JSON.stringify(value);
function filter(){page=0;const stage=byId('stage').value,sheet=byId('sheet').value,field=byId('field').value,verdict=byId('verdict').value,query=byId('query').value.toLowerCase();
filtered=data.filter(row=>(!stage||row[1]===stage)&&(!sheet||row[0]===sheet)&&(!field||row[3]===field)&&(!verdict||row[6]===verdict)&&(!query||row.map(display).join(' ').toLowerCase().includes(query)));render()}
function render(){const body=byId('rows');body.replaceChildren();for(const row of filtered.slice(page*100,(page+1)*100)){const tr=document.createElement('tr');
if(['mismatch','omitted_value','invented_value','missing_record','extra_record'].includes(row[6]))tr.className='bad';else if(row[6].startsWith('unresolved'))tr.className='pending';
for(const value of row){const td=document.createElement('td');td.textContent=display(value);tr.append(td)}body.append(tr)}
byId('count').textContent=`조건에 맞는 필드 ${filtered.length.toLocaleString()}개 / 전체 ${data.length.toLocaleString()}개`;
const pages=Math.max(1,Math.ceil(filtered.length/100));byId('page').textContent=`${page+1} / ${pages}`;byId('prev').disabled=page===0;byId('next').disabled=page+1>=pages}
byId('filters').addEventListener('input',filter);byId('filters').addEventListener('change',filter);byId('filters').addEventListener('reset',()=>setTimeout(filter,0));
byId('prev').addEventListener('click',()=>{page--;render()});byId('next').addEventListener('click',()=>{page++;render()});filter();
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("reference", "comparison"))
    args = parser.parse_args()
    reference = json.loads((HERE / "ground-truth.json").read_text(encoding="utf-8"))
    codes = {record["record_id"]: record["equipment_code"] for record in reference["records"]}
    if args.kind == "reference":
        title = "BE-007 전체 필드 정답표"
        description = "AI 호출 전에 원본 셀과 확정된 규칙으로 만든 정답이다. 업무 전문가의 최종 검수 여부는 ground-truth.json에 기록한다."
        rows = [[record["sheet"], stage, record["record_id"] + " · " + record["equipment_code"], field,
                 target["value"], None, target["state"], ", ".join(target["sources"]), None, target["rule"]]
                for record in reference["records"] for stage in ("raw", "normalized") for field,target in record[stage].items()]
        verdicts = ("explicit", "derived", "source_missing", "unresolved")
        output = "ground-truth.html"
    else:
        title = "BE-007 전체 필드 대조표"
        description = "원문 단계와 정규화 단계를 별도로 대조한다. 판정 보류는 값 정확도 분모에서 제외하고 그대로 표시한다."
        comparison = json.loads((HERE / "field-comparison.json").read_text(encoding="utf-8"))
        rows = [[row["sheet"], row["stage"], row["record_id"] + " · " + codes.get(row["record_id"], ""), row["field"],
                 row["expected"], row["actual"], row["verdict"], ", ".join(row["expected_sources"]), row["actual_source"], row["reference_rule"]]
                for row in comparison]
        verdicts = ("correct", "mismatch", "omitted_value", "invented_value", "missing_record", "extra_record", "unresolved_abstained", "unresolved_returned_value")
        output = "field-comparison.html"
    options = "".join('<option value="' + verdict + '">' + verdict + '</option>' for verdict in verdicts)
    html = TEMPLATE.replace("__TITLE__", title).replace("__DESCRIPTION__", description).replace("__VERDICT_OPTIONS__", options)
    html = html.replace("__DATA__", json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"))
    html = html.replace("__FIELDS__", json.dumps(reference["fields"]))
    (HERE / output).write_text(html + "\n", encoding="utf-8")
    print(output, len(rows), "field rows")


if __name__ == "__main__":
    main()
