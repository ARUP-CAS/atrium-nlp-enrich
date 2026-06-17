"""
tests/test_remote_apis.py
=========================
Unit tests for the resilient network clients used to call LINDAT services.
"""
import sys
import pytest
import requests
from unittest.mock import patch, MagicMock
from api_util import call_udpipe, call_nametag


@patch('api_util.call_udpipe.requests.Session.post')
def test_udpipe_process_chunk_success(mock_post):
    """Verify UDPipe client handles successful 200 OK responses."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"result": "# sent_id = 1\nProcessed Text"}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    session = call_udpipe.get_robust_session(retries=1)
    result = call_udpipe.process_chunk(session, "test text", "model-x", 60)

    assert "Processed Text" in result
    mock_post.assert_called_once()


@patch('api_util.call_udpipe.requests.Session.post')
def test_udpipe_permanent_failure(mock_post):
    """Verify UDPipe client raises an exception when retries are exhausted."""
    mock_post.side_effect = requests.exceptions.ConnectionError("Network down")

    session = call_udpipe.get_robust_session(retries=1)
    with pytest.raises(requests.exceptions.RequestException):
        call_udpipe.process_chunk(session, "test text", "model-x", 60)


@patch('api_util.call_nametag.requests.Session.post')
def test_nametag_success(mock_post):
    """Verify NameTag client handles successful 200 OK responses."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"result": "NER Output"}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    session = call_nametag.get_robust_session(retries=1)
    result = call_nametag.call_nametag(session, "conllu text", "model-y", "http://fake.url", 60)

    assert result == {"result": "NER Output"}


@patch('api_util.call_nametag.requests.Session.post')
def test_nametag_permanent_failure(mock_post):
    """Verify NameTag client gracefully returns None on permanent failure."""
    mock_post.side_effect = requests.exceptions.Timeout("Timed out")

    session = call_nametag.get_robust_session(retries=1)
    result = call_nametag.call_nametag(session, "conllu text", "model-y", "http://fake.url", 60)

    assert result is None