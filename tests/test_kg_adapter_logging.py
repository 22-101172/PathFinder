"""
Caplog tests for KGAdapter operation-level logging.
Does NOT require a live Neo4j connection — uses mocks for client and query functions.
"""
from __future__ import annotations
import logging
from unittest.mock import MagicMock, patch

import pytest

from adapters.kg_adapter import KGAdapter


@pytest.fixture
def adapter_with_mock_client():
    """KGAdapter with _client replaced by a MagicMock so dispatch runs without Neo4j."""
    a = KGAdapter.__new__(KGAdapter)
    a._client = MagicMock()
    return a


@pytest.fixture
def adapter_unavailable():
    """KGAdapter with _client=None (Neo4j unreachable)."""
    a = KGAdapter.__new__(KGAdapter)
    a._client = None
    return a


_LOG = "adapters.kg_adapter"


class TestKGAdapterLogging:

    def test_start_log_emitted(self, adapter_with_mock_client, caplog):
        fake = {"course_code": "C-CS219", "name": "OOP"}
        with patch("engines.kg.queries.q_get_course_profile", return_value=fake):
            with caplog.at_level(logging.INFO, logger=_LOG):
                adapter_with_mock_client.call("get_course_profile", {"course_code": "C-CS219"})
        messages = [r.message for r in caplog.records]
        assert any("KGAdapter.call start" in m for m in messages)
        assert any("operation=get_course_profile" in m for m in messages)

    def test_success_result_log(self, adapter_with_mock_client, caplog):
        fake = {"course_code": "C-CS219", "name": "OOP"}
        with patch("engines.kg.queries.q_get_course_profile", return_value=fake):
            with caplog.at_level(logging.INFO, logger=_LOG):
                result = adapter_with_mock_client.call("get_course_profile", {"course_code": "C-CS219"})
        assert result == fake
        result_msgs = [r.message for r in caplog.records if "KGAdapter.call result" in r.message]
        assert result_msgs, "Expected a result log line"
        assert any("status=success" in m for m in result_msgs)

    def test_unknown_operation_logs_adapter_error(self, adapter_with_mock_client, caplog):
        with caplog.at_level(logging.WARNING, logger=_LOG):
            result = adapter_with_mock_client.call("not_a_real_op", {})
        assert result.get("error") == "unknown_operation"
        assert any(
            "status=adapter_error" in r.message and "error=unknown_operation" in r.message
            for r in caplog.records
        )

    def test_kg_unavailable_logs_adapter_error(self, adapter_unavailable, caplog):
        with caplog.at_level(logging.WARNING, logger=_LOG):
            result = adapter_unavailable.call("get_course_profile", {"course_code": "C-CS219"})
        assert result.get("error") == "kg_unavailable"
        assert any(
            "status=adapter_error" in r.message and "error=kg_unavailable" in r.message
            for r in caplog.records
        )

    def test_bad_params_logs_adapter_error(self, adapter_with_mock_client, caplog):
        with caplog.at_level(logging.WARNING, logger=_LOG):
            result = adapter_with_mock_client.call("get_course_profile", {"wrong_param": "C-CS219"})
        assert result.get("error") == "bad_params"
        assert any(
            "status=adapter_error" in r.message and "error=bad_params" in r.message
            for r in caplog.records
        )

    def test_business_error_logs_business_error_not_adapter_error(self, adapter_with_mock_client, caplog):
        fake = {"error": "course_not_found", "submitted_code": "C-FAKE999"}
        with patch("engines.kg.queries.q_get_course_profile", return_value=fake):
            with caplog.at_level(logging.WARNING, logger=_LOG):
                result = adapter_with_mock_client.call("get_course_profile", {"course_code": "C-FAKE999"})
        assert result.get("error") == "course_not_found"
        result_msgs = [r.message for r in caplog.records if "KGAdapter.call result" in r.message]
        assert any("status=business_error" in m for m in result_msgs)
        assert not any("status=adapter_error" in m for m in result_msgs)
