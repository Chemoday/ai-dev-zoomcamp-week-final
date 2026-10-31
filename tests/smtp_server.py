"""An in-process SMTP server for the e-mail tests (aiosmtpd; no container, no network)."""

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@dataclass
class RecordingHandler:
    """Stores accepted messages; `data_reply` / `rcpt_reply` simulate provider errors."""

    messages: list[EmailMessage] = field(default_factory=list)
    recipients: list[list[str]] = field(default_factory=list)
    data_reply: str | None = None
    rcpt_reply: str | None = None

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: N802
        if self.rcpt_reply:
            return self.rcpt_reply
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        if self.data_reply:
            return self.data_reply
        self.messages.append(message_from_bytes(envelope.content, policy=policy.default))
        self.recipients.append(list(envelope.rcpt_tos))
        return "250 Message accepted"


@dataclass
class SmtpServer:
    host: str
    port: int
    handler: RecordingHandler

    @property
    def messages(self) -> list[EmailMessage]:
        return self.handler.messages


def _reject_everyone(server, session, envelope, mechanism, auth_data) -> AuthResult:
    return AuthResult(success=False, handled=False)


@contextmanager
def running_smtp_server(*, reject_auth: bool = False) -> Iterator[SmtpServer]:
    handler = RecordingHandler()
    port = free_port()
    options = {}
    if reject_auth:
        options = {
            "authenticator": _reject_everyone,
            "auth_required": True,
            "auth_require_tls": False,
        }
    controller = Controller(handler, hostname="127.0.0.1", port=port, **options)
    controller.start()
    try:
        yield SmtpServer(host="127.0.0.1", port=port, handler=handler)
    finally:
        controller.stop()
