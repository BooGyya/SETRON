"""새 선택 필드의 NULL만 기존 item 구조에서 생략하고 실제 Jira 값은 보호한다."""
import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, select
from sqlalchemy.orm import Session

from setron.features.storage.repository import StorageRepository
from setron.features.storage.validation import StorageValidationError, encode_record


@pytest.mark.parametrize("has_jira", [False, True])
def test_null_jira_insert_and_update_work_with_both_item_layouts(has_jira):
    engine = create_engine("sqlite://")
    metadata = MetaData()
    columns = [Column("item_id", Integer, primary_key=True), Column("content", String(100), nullable=False), Column("edited_fields", Text)]
    if has_jira:
        columns.append(Column("jira_key", String(50)))
    item = Table("item", metadata, *columns)
    metadata.create_all(engine)
    repository = object.__new__(StorageRepository)
    repository.metadata = metadata
    with Session(engine) as session:
        values = encode_record("item", {"content": "원문"}, inserting=True)
        key = repository.insert(session, "item", values)
        repository.update(session, "item", key, {"content": "변경", "jira_key": None})
        assert values["jira_key"] is None
        assert session.execute(select(item.c.content)).scalar_one() == "변경"
        if has_jira:
            assert session.execute(select(item.c.jira_key)).scalar_one() is None
        else:
            with pytest.raises(StorageValidationError, match="DB 구조"):
                repository.insert(session, "item", {**values, "jira_key": "ETOJ-3257"})
            with pytest.raises(StorageValidationError, match="DB 구조"):
                repository.update(session, "item", key, {"jira_key": "ETOJ-3257"})
        with pytest.raises(StorageValidationError, match="없는 필드"):
            repository.insert(session, "item", {**values, "unrecognized": None})
    engine.dispose()


def test_same_jira_key_can_be_stored_on_multiple_items():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    item = Table("item", metadata, Column("item_id", Integer, primary_key=True), Column("jira_key", String(50)),
                 Column("content", Text), Column("edited_fields", Text))
    metadata.create_all(engine)
    repository = object.__new__(StorageRepository)
    repository.metadata = metadata
    with Session(engine) as session:
        for content in ("첫 항목", "둘째 항목"):
            repository.insert(session, "item", encode_record("item", {"content": content, "jira_key": "etoj-3257"}, inserting=True))
        assert session.execute(select(item.c.jira_key)).scalars().all() == ["etoj-3257", "etoj-3257"]
    engine.dispose()
