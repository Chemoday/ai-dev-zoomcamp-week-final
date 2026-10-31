"""The e-mail channel against an in-process SMTP server (no database)."""

import pytest

from src.core.config import Settings
from src.plugins.common.email_smtp import SmtpEmailChannel, smtp_reply_result
from src.plugins.registry import DeliveryResult, OutgoingMessage, PluginRegistry
from tests.smtp_server import SmtpServer, free_port, running_smtp_server

SENDER = "KÜ Sõpruse 12 <noreply@kyu.example.test>"


def channel_for(server_host: str, port: int, **overrides) -> SmtpEmailChannel:
    options = {"host": server_host, "port": port, "sender": SENDER, "tls": "none", "timeout": 5.0}
    return SmtpEmailChannel(**{**options, **overrides})


def message(**overrides) -> OutgoingMessage:
    values = {
        "address": {"email": "ivan.petrov@example.com"},
        "locale": "et",
        "event_type": "EMERGENCY_PUBLISHED",
        "subject": "Avarii: Külm vesi on kinni",
        "body": "Püstik vahetatakse.\n\nPuudutab: trepikoda 2",
        "deep_link": "https://kyu.example.test",
        "urgent": True,
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
    }
    return OutgoingMessage(**{**values, **overrides})


async def test_a_message_is_submitted_as_plain_utf8_text_with_the_trace_id(
    smtp_server: SmtpServer,
) -> None:
    result = await channel_for(smtp_server.host, smtp_server.port).send(message())

    assert result == DeliveryResult(ok=True)
    [email] = smtp_server.messages
    assert smtp_server.handler.recipients == [["ivan.petrov@example.com"]]
    assert email["Subject"] == "Avarii: Külm vesi on kinni"
    assert email["From"] == SENDER
    assert email["X-Trace-Id"] == "0af7651916cd43dd8448eb211c80319c"
    assert email["Message-ID"].endswith("@kyu.example.test>")
    assert email["Date"]
    assert email.get_content_type() == "text/plain"
    text = email.get_content().replace("\r\n", "\n").strip()
    assert text == "Püstik vahetatakse.\n\nPuudutab: trepikoda 2"


async def test_without_a_trace_there_is_no_trace_header(smtp_server: SmtpServer) -> None:
    await channel_for(smtp_server.host, smtp_server.port).send(message(trace_id=None))

    assert "X-Trace-Id" not in smtp_server.messages[0]


async def test_a_temporary_reply_is_retryable(smtp_server: SmtpServer) -> None:
    smtp_server.handler.data_reply = "451 4.3.0 Try again later"

    result = await channel_for(smtp_server.host, smtp_server.port).send(message())

    assert result == DeliveryResult(ok=False, retryable=True, error_code="SMTP_TEMPORARY")


async def test_a_rejected_recipient_fails_without_retry(smtp_server: SmtpServer) -> None:
    smtp_server.handler.rcpt_reply = "550 5.1.1 No such user"

    result = await channel_for(smtp_server.host, smtp_server.port).send(message())

    assert result == DeliveryResult(ok=False, error_code="SMTP_REJECTED")
    assert smtp_server.messages == []


async def test_an_unreachable_server_is_retryable() -> None:
    result = await channel_for("127.0.0.1", free_port(), timeout=2.0).send(message())

    assert result == DeliveryResult(ok=False, retryable=True, error_code="SMTP_UNAVAILABLE")


@pytest.mark.filterwarnings("ignore:Requiring AUTH while not requiring TLS")
async def test_wrong_credentials_fail_as_a_configuration_error() -> None:
    with running_smtp_server(reject_auth=True) as server:
        channel = channel_for(server.host, server.port, username="app", password="wrong")

        result = await channel.send(message())

    assert result == DeliveryResult(ok=False, error_code="SMTP_AUTH_FAILED")
    assert server.messages == []


async def test_a_line_break_in_the_subject_is_never_sent(smtp_server: SmtpServer) -> None:
    result = await channel_for(smtp_server.host, smtp_server.port).send(
        message(subject="Hi\r\nBcc: someone@example.com")
    )

    assert result == DeliveryResult(ok=False, error_code="EMAIL_INVALID")
    assert smtp_server.messages == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (421, DeliveryResult(ok=False, retryable=True, error_code="SMTP_TEMPORARY")),
        (452, DeliveryResult(ok=False, retryable=True, error_code="SMTP_TEMPORARY")),
        (500, DeliveryResult(ok=False, error_code="SMTP_REJECTED")),
        (554, DeliveryResult(ok=False, error_code="SMTP_REJECTED")),
    ],
)
def test_reply_codes(code: int, expected: DeliveryResult) -> None:
    assert smtp_reply_result(code) == expected


def test_the_plugin_refuses_to_load_without_host_or_sender() -> None:
    registry = PluginRegistry(Settings(SMTP_HOST="", SMTP_FROM=""))

    with pytest.raises(RuntimeError, match="SMTP_HOST, SMTP_FROM"):
        registry.load(["common.email_smtp"])
    with pytest.raises(RuntimeError, match="not an e-mail address"):
        PluginRegistry(Settings(SMTP_HOST="smtp.example.test", SMTP_FROM="nobody")).load(
            ["common.email_smtp"]
        )


def test_the_plugin_registers_the_email_channel() -> None:
    registry = PluginRegistry(Settings(SMTP_HOST="smtp.example.test", SMTP_FROM=SENDER))

    registry.load(["common.email_smtp"])

    assert registry.is_enabled("common.email_smtp")
    channel = registry.channel("email")
    assert isinstance(channel, SmtpEmailChannel)


async def test_a_server_without_starttls_is_not_retried(smtp_server: SmtpServer) -> None:
    channel = channel_for(smtp_server.host, smtp_server.port, tls="starttls")

    result = await channel.send(message())

    assert result == DeliveryResult(ok=False, error_code="SMTP_NOT_SUPPORTED")


def test_credentials_are_never_sent_in_clear_text() -> None:
    settings = Settings(
        SMTP_HOST="smtp.example.test", SMTP_FROM=SENDER, SMTP_TLS="none", SMTP_USERNAME="app"
    )

    with pytest.raises(RuntimeError, match="clear text"):
        PluginRegistry(settings).load(["common.email_smtp"])
