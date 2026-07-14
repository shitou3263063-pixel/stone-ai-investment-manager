from __future__ import annotations

import smtplib
import socket
import ssl
from unittest.mock import Mock, patch

import pytest

from src.notifier.email_notifier import (
    EmailDeliveryError,
    _safe_error,
    _send_email,
    diagnose_email_connection,
)


def _config(**overrides: str) -> dict[str, str]:
    config = {
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "465",
        "SMTP_SECURITY": "ssl",
        "SMTP_CONNECT_TIMEOUT_SECONDS": "12",
        "SMTP_SEND_TIMEOUT_SECONDS": "30",
        "SMTP_MAX_RETRIES": "2",
        "SMTP_USER": "sender@gmail.com",
        "SMTP_PASSWORD": "secret-app-password",
        "EMAIL_TO": "receiver@gmail.com",
    }
    config.update(overrides)
    return config


@pytest.mark.parametrize(
    ("exc", "stage", "category"),
    [
        (socket.gaierror("lookup failed"), "connection", "DNS_ERROR"),
        (socket.timeout("timed out"), "connection", "CONNECTION_TIMEOUT"),
        (ssl.SSLError("handshake"), "tls_handshake", "TLS_HANDSHAKE_ERROR"),
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), "authentication", "AUTHENTICATION_FAILED"),
        (smtplib.SMTPDataError(554, b"rejected"), "smtp_send", "SMTP_REJECTED"),
        (ConnectionResetError("reset"), "connection", "NETWORK_PROXY_ERROR"),
        (OSError("opaque failure"), "smtp_send", "UNKNOWN_ERROR"),
    ],
)
def test_email_error_classification(exc: BaseException, stage: str, category: str) -> None:
    safe = _safe_error(exc, stage)
    assert safe.category == category
    assert "secret-app-password" not in str(safe)


def test_connection_failure_retries_at_most_twice() -> None:
    error = EmailDeliveryError("CONNECTION_TIMEOUT", "connection", "timeout")
    with (
        patch("src.notifier.email_notifier._send_once", side_effect=error) as sender,
        patch("src.notifier.email_notifier.time.sleep") as sleeper,
    ):
        with pytest.raises(EmailDeliveryError, match="CONNECTION_TIMEOUT"):
            _send_email(_config(), "subject", "body", [])
    assert sender.call_count == 3
    assert sleeper.call_count == 2


def test_authentication_failure_is_not_retried() -> None:
    error = EmailDeliveryError("AUTHENTICATION_FAILED", "authentication", "rejected")
    with (
        patch("src.notifier.email_notifier._send_once", side_effect=error) as sender,
        patch("src.notifier.email_notifier.time.sleep") as sleeper,
    ):
        with pytest.raises(EmailDeliveryError, match="AUTHENTICATION_FAILED"):
            _send_email(_config(), "subject", "body", [])
    assert sender.call_count == 1
    sleeper.assert_not_called()


def test_send_timeout_is_not_retried_to_avoid_duplicate_mail() -> None:
    error = EmailDeliveryError("CONNECTION_TIMEOUT", "smtp_send", "delivery result unknown")
    with patch("src.notifier.email_notifier._send_once", side_effect=error) as sender:
        with pytest.raises(EmailDeliveryError, match="CONNECTION_TIMEOUT"):
            _send_email(_config(), "subject", "body", [])
    assert sender.call_count == 1


def test_quit_timeout_after_delivery_is_non_fatal() -> None:
    class FakeSocket:
        def settimeout(self, timeout: int) -> None:
            self.timeout = timeout

    class FakeSMTP:
        sock = FakeSocket()
        sent = 0

        def __init__(self, *args, **kwargs):
            self.connect_timeout = kwargs["timeout"]

        def login(self, *_args):
            return None

        def send_message(self, _message):
            FakeSMTP.sent += 1

        def quit(self):
            raise socket.timeout("quit timed out")

        def close(self):
            return None

    with patch("src.notifier.email_notifier.smtplib.SMTP_SSL", FakeSMTP):
        _send_email(_config(), "subject", "body", [])
    assert FakeSMTP.sent == 1
    assert FakeSMTP.sock.timeout == 30


def test_diagnostic_reports_each_stage_without_credentials() -> None:
    smtp = Mock()
    smtp.login.return_value = None
    with (
        patch("src.notifier.email_notifier._get_email_config", return_value=_config()),
        patch("src.notifier.email_notifier.socket.getaddrinfo", return_value=[("family",)]),
        patch("src.notifier.email_notifier._probe_tcp"),
        patch("src.notifier.email_notifier._probe_tls"),
        patch("src.notifier.email_notifier._open_smtp", return_value=smtp),
    ):
        result = diagnose_email_connection()
    assert result["status"] == "passed"
    assert set(result["stages"]) == {"configuration", "dns", "tcp_connection", "tls_handshake", "authentication"}
    assert "SMTP_PASSWORD" not in str(result)
    assert "secret-app-password" not in str(result)


def test_gmail_sender_rejects_non_gmail_server_before_network() -> None:
    with patch("src.notifier.email_notifier._send_once") as sender:
        # Provider validation lives inside _send_once in production; exercise it
        # through the public diagnostic path to avoid any network request.
        with patch(
            "src.notifier.email_notifier._get_email_config",
            return_value=_config(SMTP_HOST="smtp.qq.com"),
        ):
            result = diagnose_email_connection()
    sender.assert_not_called()
    assert result["status"] == "failed"
    assert result["error_stage"] == "configuration"
    assert result["error_category"] == "AUTHENTICATION_FAILED"
