"""이미 만든 DB의 설비 현황 표에 공정 칸을 더한다 (11/13 멘토: 공정은 선택값, 저장해 두고 AI 분석에 쓴다).

앱이 DB 자료를 처음 읽을 때(:func:`setron.ui.database_source.dashboard_source_from_env`) 부른다. 칸이 있으면 아무것도
하지 않고, 없으면 맨 끝에 더한다(새 DB는 스키마 SQL에 이미 있다). 실패해도 앱 시작을 막지 않으며, 그때 공정은
저장되지 않을 뿐 나머지 저장은 그대로다(값이 없으면 칸을 쓰지 않는다).
"""

from __future__ import annotations

import logging

from sqlalchemy import text

LOG = logging.getLogger(__name__)
COLUMN_SQL = ("ALTER TABLE equipment_snapshot ADD COLUMN process_name VARCHAR(100) NULL "
              "COMMENT '공정 원문(복합공정 AAAA/BBBB 포함); 없으면 NULL'")


def has_process_column(connection) -> bool:
    return bool(connection.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'equipment_snapshot' AND COLUMN_NAME = 'process_name'")).scalar())


def ensure_process_column(engine) -> bool:
    """칸이 있으면 True. 없으면 더하고 True, 더하지 못하면 False."""
    try:
        with engine.begin() as connection:
            if has_process_column(connection):
                return True
            connection.execute(text(COLUMN_SQL))
            LOG.info("equipment_snapshot.process_name 칸을 더했습니다.")
            return True
    except Exception as error:                      # noqa: BLE001 — 앱 시작을 막지 않는다
        LOG.warning("equipment_snapshot.process_name 칸을 더하지 못했습니다: %s", error)
        return False
