"""Tests for schema declarations."""

from __future__ import annotations

import pytest

from core import config
from core.schemas import EVENT_LOG, REPORT_SCHEMAS, ColumnSpec, TableSchema


def test_every_report_has_exactly_one_schema():
    assert set(REPORT_SCHEMAS) == set(config.REPORT_FILES)
    for name, schema in REPORT_SCHEMAS.items():
        assert schema.name == name


def test_unique_keys_reference_declared_columns():
    for schema in [*REPORT_SCHEMAS.values(), EVENT_LOG]:
        assert set(schema.unique_key) <= set(schema.column_names)


def test_duplicate_column_declaration_is_rejected():
    with pytest.raises(ValueError, match="more than once"):
        TableSchema("t", (ColumnSpec("a", "string"), ColumnSpec("a", "integer")))


def test_unique_key_on_undeclared_column_is_rejected():
    with pytest.raises(ValueError, match="undeclared"):
        TableSchema("t", (ColumnSpec("a", "string"),), unique_key=("b",))


def test_subset_keeps_key_only_when_all_key_columns_selected():
    sub = EVENT_LOG.subset((config.COL_EVENT_ID, config.COL_ACTIVITY))
    assert sub.unique_key == (config.COL_EVENT_ID,)
    assert EVENT_LOG.subset((config.COL_ACTIVITY,)).unique_key == ()


def test_offer_attributes_are_nullable_and_case_attributes_required():
    for name in ("Accepted", "Selected", "CreditScore", "OfferedAmount", "OfferID"):
        assert EVENT_LOG.column(name).nullable
    for name in (config.COL_CASE_ID, config.COL_ACTIVITY, config.COL_TIMESTAMP, config.COL_LIFECYCLE):
        assert not EVENT_LOG.column(name).nullable


def test_application_outcomes_schema_is_described_as_last_activity_not_status():
    assert "LAST RECORDED ACTIVITY" in REPORT_SCHEMAS["application_outcomes"].description
