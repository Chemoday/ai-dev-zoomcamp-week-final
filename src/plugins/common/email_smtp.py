"""E-mail channel (plugin "common.email_smtp"): an SMTP client of an external provider.

docs/epics/E1_NOTIFICATIONS.md 6.1. No mail server runs in the image: each environment points
SMTP_HOST at its provider. The notifications module renders the texts; this plugin only builds a
plain-text message and submits it. Addresses and texts are never logged.
"""

from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

import aiosmtplib

from src.plugins.registry import DeliveryResult, OutgoingMessage, PluginRegistry

CHANNEL_NAME = "email"
SEND_TIMEOUT_SECONDS = 10.0


class SmtpEmailChannel:
    name = CHANNEL_NAME

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        tls: str,
        username: str = "",
        password: str = "",
        timeout: float = SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._tls = tls
        self._username = username
        self._password = password
        self._timeout = timeout
        # Message-ID domain: the sender's domain, so ids look like the provider expects.
        self._msgid_domain = parseaddr(sender)[1].rpartition("@")[2] or None

    def build(self, message: OutgoingMessage) -> EmailMessage:
        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = str(message.address["email"])
        email["Subject"] = message.subject
        email["Date"] = formatdate(localtime=False, usegmt=True)
        email["Message-ID"] = make_msgid(domain=self._msgid_domain)
        if message.trace_id:
            email["X-Trace-Id"] = message.trace_id
        email.set_content(message.full_text)  # text/plain; charset utf-8
        return email

    async def send(self, message: OutgoingMessage) -> DeliveryResult:
        try:
            email = self.build(message)
        except ValueError:  # e.g. a line break in a header value: never retry a bad message
            return DeliveryResult(ok=False, error_code="EMAIL_INVALID")
        try:
            await aiosmtplib.send(
                email,
                hostname=self._host,
                port=self._port,
                username=self._username or None,
                password=self._password or None,
                use_tls=self._tls == "tls",
                start_tls=self._tls == "starttls",
                timeout=self._timeout,
            )
        except aiosmtplib.SMTPAuthenticationError:
            return DeliveryResult(ok=False, error_code="SMTP_AUTH_FAILED")
        except aiosmtplib.SMTPRecipientsRefused as exc:
            codes = [recipient.code for recipient in exc.recipients]
            return smtp_reply_result(max(codes) if codes else 550)
        except aiosmtplib.SMTPResponseException as exc:
            return smtp_reply_result(exc.code)
        except (OSError, TimeoutError):
            # Connection refused or dropped, TLS failure, timeout (aiosmtplib's connection errors
            # are OSError subclasses): the provider may be back soon.
            return DeliveryResult(ok=False, retryable=True, error_code="SMTP_UNAVAILABLE")
        except aiosmtplib.SMTPException:
            # The server lacks what the settings need (STARTTLS, AUTH, SMTPUTF8 for a non-ASCII
            # address): a configuration error, retrying cannot help.
            return DeliveryResult(ok=False, error_code="SMTP_NOT_SUPPORTED")
        return DeliveryResult(ok=True)


def smtp_reply_result(code: int) -> DeliveryResult:
    """4xx is temporary (retry), anything else a final rejection."""
    if 400 <= code < 500:
        return DeliveryResult(ok=False, retryable=True, error_code="SMTP_TEMPORARY")
    return DeliveryResult(ok=False, error_code="SMTP_REJECTED")


def register(registry: PluginRegistry) -> None:
    settings = registry.settings
    missing = [name for name in ("SMTP_HOST", "SMTP_FROM") if not getattr(settings, name)]
    if missing:
        raise RuntimeError(f"plugin common.email_smtp needs {', '.join(missing)}")
    if "@" not in parseaddr(settings.SMTP_FROM)[1]:
        raise RuntimeError("SMTP_FROM is not an e-mail address")
    if settings.SMTP_TLS == "none" and (
        settings.SMTP_USERNAME or settings.SMTP_PASSWORD.get_secret_value()
    ):
        raise RuntimeError("SMTP credentials need SMTP_TLS=starttls or tls (never in clear text)")
    registry.register_channel(
        SmtpEmailChannel(
            host=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            sender=settings.SMTP_FROM,
            tls=settings.SMTP_TLS,
            username=settings.SMTP_USERNAME,
            password=settings.SMTP_PASSWORD.get_secret_value(),
        )
    )
