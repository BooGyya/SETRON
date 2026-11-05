"""예시 Excel 기반 300대·6개월 자료로 seed.sql을 재생성한다. DB에는 접속하지 않는다.

    uv run --locked python database/build_seed.py
    uv run --locked python database/build_seed.py --excel-dir ../docs/04_데이터/가공

--excel-dir는 설비 현황·릴리즈·Item 원문을 새로 읽는다. Excel 파일을 수정하지 않는다.
기본 재생성은 저장소의 demo_release_catalog.json만 사용한다.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "demo_release_catalog.json"
from seed_source import SEED_KEY, LEGACY_KEY
DATES = tuple(date(2026, month, 21) for month in range(5, 11))
MODELS = (
    ("M-C3", "POLY", None), ("M-C4", "POLY", None),
    ("M-C4 DFB", "POLY", "M-C4"), ("M-C4+", "POLY", "M-C4"),
    ("M-O2", "OXIDE", None), ("M-O3", "OXIDE", None), ("XMO3", "OXIDE", "M-O3"),
    ("M-O5", "OXIDE", None), ("M-O5 Prime", "OXIDE", "M-O5"),
    ("Inline Ox", "KCTC", None), ("Inline O2", "KCTC", None),
    ("Cluster Ox", "KCTC", None), ("Cluster O2", "KCTC", None), ("HPA", "HPA", None),
)
@dataclass(frozen=True)
class Expr:
    """SQL 변수/표현식. 일반 값은 항상 SQL 문자열로 인코딩한다."""
    sql: str


def literal(value):
    if isinstance(value, Expr):
        return value.sql
    if value is None:
        return "NULL"
    if isinstance(value, (int, bool)):
        return str(int(value))
    if isinstance(value, date):
        value = value.isoformat()
    # 문자열을 hex UTF-8로 써 SQL mode·따옴표·역슬래시 처리에 의존하지 않는다.
    return "CONVERT(0x" + str(value).encode("utf-8").hex() + " USING utf8mb4) COLLATE utf8mb4_unicode_ci" if value else "''"


def json_sql(value, *, spaced=False):
    """동적 ID를 포함한 JSON을 MySQL 5.6의 CONCAT으로 구성한다."""
    expressions = []

    def encode(item):
        if isinstance(item, Expr):
            token = f"__SEED_EXPR_{len(expressions)}__"
            expressions.append(item.sql)
            return token
        if isinstance(item, dict):
            return {key: encode(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [encode(val) for val in item]
        return item.isoformat() if isinstance(item, date) else item

    raw = json.dumps(encode(value), ensure_ascii=False, sort_keys=True,
                     separators=(", ", ": ") if spaced else (",", ":"))
    parts = re.split(r'"__SEED_EXPR_(\d+)__"', raw)
    if len(parts) == 1:
        return literal(raw)
    return "CONCAT(" + ", ".join(expressions[int(part)] if i % 2 else literal(part)
                                  for i, part in enumerate(parts)) + ")"


def model_var(name):
    return Expr("@m" + str(next(i for i, row in enumerate(MODELS) if row[0] == name)))



def read_excel_catalog(folder):
    from seed_source import read_sources
    return read_sources(folder)


def build_sql(catalog):
    from seed_source import (FAMILIES as SOURCE_FAMILIES, SEED_KEY as KEY, GENERATOR_REVISION,
                             make_plan, candidates, lineage_for_model)
    if catalog.get('schema') != 2:
        raise ValueError('원본 참조 목록을 --excel-dir로 다시 생성해 주세요.')
    statements = []
    def emit(sql):
        statements.append(sql)
    def insert(table, values):
        emit('INSERT INTO ' + table + ' (' + ', '.join(values) + ')\nSELECT ' +
             ', '.join(literal(value) for value in values.values()) + '\nFROM DUAL WHERE @seed_install = 1')
    def string_expr(sql):
        return Expr("CONCAT('\"', " + sql + ", '\"')")
    def ref(field, workbook, sheet, cell):
        return {field: {'sheet': sheet, 'cells': [cell]}}
    emit('SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci')
    emit('START TRANSACTION')
    reference = (HERE / 'seed_demo_catalog.sql').read_text(encoding='utf-8')
    reference = '\n'.join(line for line in reference.splitlines() if not line.startswith('--'))
    reference = reference.replace('SET NAMES utf8mb4;', 'SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;')
    statements.extend(part.strip() for part in reference.split(';') if part.strip())
    for name, _, _ in MODELS:
        emit(f'SET {model_var(name).sql} = (SELECT group_id FROM equipment_group WHERE group_name = {literal(name)})')
    for key, owner, family_name, ui in SOURCE_FAMILIES:
        emit(f'SET @f_{key} = (SELECT family_id FROM version_family WHERE group_id = {model_var(owner).sql} '
             f'AND family_name = {literal(family_name)} AND ui_type <=> {literal(ui)})')
    digest = hashlib.sha256(json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    plan = make_plan(catalog)
    plan_digest = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    emit(f'SET @seed_done = EXISTS(SELECT 1 FROM app_setting WHERE setting_key = {literal(KEY)})')
    collisions = [f'EXISTS(SELECT 1 FROM equipment WHERE equipment_code BETWEEN {literal("DEMO-0001")} AND {literal("DEMO-0300")})']
    pairs = sorted({(t['model'], t['family']) for t in catalog['templates'] if t['model'] != 'HPA'})
    for key, family in catalog['families'].items():
        names = ','.join(literal(v['name']) for v in family['versions'])
        collisions.append(f'EXISTS(SELECT 1 FROM release_version WHERE family_id = @f_{key} AND version_raw IN ({names}))')
    for model, key in pairs:
        collisions.append(f"EXISTS(SELECT 1 FROM app_setting WHERE setting_key = CONCAT('target_applicability.v1:',{model_var(model).sql},':',@f_{key}))")
    emit('SET @seed_install = NOT @seed_done AND NOT (' + '\n OR '.join(collisions) + ')')
    for index, book in enumerate(catalog['workbooks']):
        raw = {'seed': KEY, 'synthetic': True, 'source_workbook': book['name'], 'source_sha256': book['sha256'],
               'sheets': book['sheets'], 'augmentation': 'DEMO 호기와 6개월 관측은 합성. 원본 셀 값은 별도 보존.'}
        insert('import_batch', {'source_type': 'MANUAL', 'raw_data': Expr(json_sql(raw))})
        emit(f'SET @batch_{index} = IF(@seed_install, LAST_INSERT_ID(), NULL)')
    model_rows = {name: {'group_id': model_var(name), 'group_name': name, 'category': category,
                         'parent_group_id': model_var(parent) if parent else None,
                         'analysis_enabled': 0 if name == 'HPA' else 1} for name, category, parent in MODELS}
    family_rows = {key: {'family_id': Expr(f'@f_{key}'), 'group_id': model_var(owner), 'family_name': name, 'ui_type': ui}
                   for key, owner, name, ui in SOURCE_FAMILIES}
    release_rows = {}
    owned_settings = []
    for key, family in catalog['families'].items():
        batch = Expr(f"@batch_{family['workbook']}")
        owner = next(row[1] for row in SOURCE_FAMILIES if row[0] == key)
        insert('release_note', {'group_id': model_var(owner), 'release_date': None, 'batch_id': batch})
        emit(f'SET @note_{key} = IF(@seed_install, LAST_INSERT_ID(), NULL)')
        item_rows = {}
        for i, item in enumerate(family['items']):
            source = ref('content', family['workbook'], family['sheet'], item['cell'])
            row = {'release_note_id': Expr(f'@note_{key}'), 'item_no': item['no'], 'content': item['content'],
                   'batch_id': batch, 'source_reference': Expr(json_sql(source)), 'edited_fields': '[]'}
            insert('item', row)
            emit(f'SET @i_{key}_{i} = IF(@seed_install, LAST_INSERT_ID(), NULL)')
            item_rows[i] = {**row, 'item_id': Expr(f'@i_{key}_{i}'), 'source_reference': source, 'edited_fields': []}
        for i, version in enumerate(family['versions']):
            source = {**ref('version_raw', family['workbook'], family['sheet'], version['cell']),
                      **ref('released_at', family['workbook'], family['sheet'], version['date_cell']),
                      **ref('version_order', family['workbook'], family['sheet'], version['cell'])}
            row = {'release_note_id': Expr(f'@note_{key}'), 'family_id': Expr(f'@f_{key}'),
                   'version_raw': version['name'], 'version_suffix': version['variant'] or None,
                   'released_at': version['date'], 'version_order': version['order'], 'item_mapping_status': 'VERIFIED',
                   'batch_id': batch, 'source_reference': Expr(json_sql(source)), 'edited_fields': '[]'}
            insert('release_version', row)
            emit(f'SET @r_{key}_{i} = IF(@seed_install, LAST_INSERT_ID(), NULL)')
            release = {**row, 'release_version_id': Expr(f'@r_{key}_{i}'), 'source_reference': source, 'edited_fields': []}
            release_rows[key, i] = release
            for n in version['items']:
                item = family['items'][n]
                models = item['models']
                marks = item['marks']
                mixed = bool(marks and 0 < len(models) < len(marks))
                scope = 'UNKNOWN' if mixed else 'LISTED'
                state = 'UNKNOWN' if mixed else 'APPLIED' if models else 'NOT_APPLIED'
                targets = models or [m['model'] for m in marks] or [owner]
                link = {'release_version_id': Expr(f'@r_{key}_{i}'), 'item_id': Expr(f'@i_{key}_{n}'),
                        'item_state': state, 'target_scope': scope, 'target_group_ids': [model_var(m) for m in targets] if scope == 'LISTED' else None}
                insert('release_version_item', {**link, 'target_group_ids': Expr(json_sql(link['target_group_ids'])) if link['target_group_ids'] else None})
                if marks:
                    marked_models = [model_rows[m['model']] for m in marks]
                    # 모델 ID 정렬이 달라도 지문이 일치하도록 DB에서 실제 정렬을 선택한다.
                    def evidence(order):
                        return {'release': release, 'item': item_rows[n], 'models': order}
                    if len(marked_models) != 2:
                        raise ValueError('현재 예시 C4의 두 모델 표식 열을 확인해 주세요.')
                    a,b = marked_models
                    fp = f"IF({a['group_id'].sql} < {b['group_id'].sql}, SHA2({json_sql(evidence([a,b]), spaced=True)},256), SHA2({json_sql(evidence([b,a]), spaced=True)},256))"
                    entries = [{'model_id': model_var(m['model']), 'mark': {'note_model': owner, 'model_name': m['model'],
                               'state': 'APPLIED' if m['model'] in models else 'NOT_APPLIED', 'header_raw': str(m['header']),
                               'mark_raw': '' if m['raw'] is None else str(m['raw']), 'sheet_name': family['sheet'],
                               'header_address': m['header_cell'], 'mark_address': m['cell']}} for m in marks]
                    payload = {'schema': 1, 'release_id': Expr(f'@r_{key}_{i}'), 'item_id': Expr(f'@i_{key}_{n}'),
                               'fingerprint': string_expr(fp), 'link_fingerprint': string_expr(f'SHA2({json_sql(link, spaced=True)},256)'), 'entries': entries}
                    setting = Expr(f"CONCAT('shared_note_item_models.v1:',@r_{key}_{i},':',@i_{key}_{n})")
                    insert('app_setting', {'setting_key': setting, 'setting_value': Expr(json_sql(payload))})
                    owned_settings.append(string_expr(setting.sql))
        # 예시의 Item을 위험으로 자동 지정하지 않는다. 기존 v1의 임의 위험 규칙도 승계하지 않는다.
    for model, key in pairs:
        choices = candidates(catalog, model, key)
        if not choices:
            continue
        owner = next(row[1] for row in SOURCE_FAMILIES if row[0] == key)
        ancestors = [model_rows[owner]] if model == owner else [model_rows[owner], model_rows[model]]
        fp = f"SHA2({json_sql({'family': family_rows[key], 'models': ancestors})},256)"
        if len(ancestors) == 2:
            reverse = f"SHA2({json_sql({'family': family_rows[key], 'models': ancestors[::-1]})},256)"
            fp = f'IF({model_var(owner).sql} < {model_var(model).sql},{fp},{reverse})'
        family = catalog['families'][key]
        values = []
        for i, version in choices:
            source = ref('model_applicability', family['workbook'], family['sheet'], version['cell'])
            # 모델 표식이 있는 계열은 실제 O의 셀 주소를 근거로 쓴다.
            for n in version['items']:
                item = family['items'][n]
                mark = next((m for m in item['marks'] if m['model'] == model and m['model'] in item['models']), None)
                if mark:
                    source = ref('model_applicability', family['workbook'], family['sheet'], mark['cell'])
                    break
            values.append({'confirmation': {'release_id': Expr(f'@r_{key}_{i}'),
                           'applicability_basis': '예시 문서의 모델 또는 O 표식에 한정',
                           'order_basis': '예시 릴리즈 시트의 나열 순서. 파생형을 뒤에 덧붙이지 않음.',
                           'source_text': KEY + ' / ' + catalog['workbooks'][family['workbook']]['name'], 'source_reference': source},
                           'fingerprint': string_expr(f'SHA2({json_sql(release_rows[key,i])},256)')})
        payload = {'schema': 1, 'model_id': model_var(model), 'family_id': Expr(f'@f_{key}'),
                   'pair_fingerprint': string_expr(fp), 'candidates': values}
        lineage = lineage_for_model(model, key)
        if lineage is not None:
            payload['version_lineage'] = lineage
        setting = Expr(f"CONCAT('target_applicability.v1:',{model_var(model).sql},':',@f_{key})")
        insert('app_setting', {'setting_key': setting, 'setting_value': Expr(json_sql(payload))})
        owned_settings.append(string_expr(setting.sql))
    for entry in plan:
        t = catalog['templates'][entry['template']]
        batch = Expr(f"@batch_{t['workbook']}")
        insert('equipment', {'equipment_code': entry['code']})
        emit('SET @equipment = IF(@seed_install, LAST_INSERT_ID(), NULL)')
        for obs in entry['observations']:
            source = ref('template_equipment_code', t['workbook'], t['sheet'], t['equipment_cell'])
            insert('equipment_snapshot', {'equipment_id': Expr('@equipment'), 'base_date': obs['date'], 'site': t['site'],
                'line': t['line'], 'model_name': t['model_raw'], 'group_id': model_var(t['model']),
                'production_area': t['area'], 'structure_type': t['structure'], 'geometry_type': t['geometry'],
                'modification_type': t['modification'], 'batch_id': batch, 'source_reference': Expr(json_sql(source))})
            emit('SET @snapshot = IF(@seed_install, LAST_INSERT_ID(), NULL)')
            if obs['version']:
                rid = Expr(f"@r_{t['family']}_{obs['release']}") if obs['release'] is not None else None
                source = ref('version_raw', obs['workbook'], obs['sheet'], obs['cell'])
                insert('equipment_version', {'snapshot_id': Expr('@snapshot'), 'family_id': Expr(f"@f_{t['family']}"),
                    'version_raw': obs['version'], 'release_version_id': rid, 'batch_id': Expr(f"@batch_{obs['workbook']}"),
                    'source_reference': Expr(json_sql(source))})
    manifest = {'seed': KEY, 'synthetic': True, 'dataset_sha256': digest,
                'generator_revision': GENERATOR_REVISION, 'plan_sha256': plan_digest,
                'equipment': 300, 'snapshots': 1800,
                'hpa_equipment': 15, 'dates': [d.isoformat() for d in DATES],
                'owned_settings': owned_settings,
                'source_workbooks': [{'name': b['name'], 'sha256': b['sha256']} for b in catalog['workbooks']]}
    insert('app_setting', {'setting_key': KEY, 'setting_value': Expr(json_sql(manifest))})
    emit('SELECT @seed_install AS installed, @seed_done AS already_installed')
    emit('COMMIT')
    return ('-- 예시 설비 현황·Item 원문 기반 증강: 300대 / 2026년 5~10월 1,800개 관측\n'
            '-- 원본 Excel 보존. DEMO 호기·월별 관측만 합성. Item 원문·버전·모델 표식 출처 보존.\n'
            '-- 실행: 저장소 최상위 폴더에서 init_local_db.bat 실행 (기본 설정 .env.windows)\n\n' +
            ';\n\n'.join(statements) + ';\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--excel-dir', type=Path)
    args = parser.parse_args()
    if args.excel_dir:
        catalog = read_excel_catalog(args.excel_dir)
        CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    else:
        catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    output = HERE / 'seed.sql'
    output.write_text(build_sql(catalog), encoding='utf-8', newline='\n')
    print(f'{output.name}: 300대 / 1,800개 관측 / {output.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
