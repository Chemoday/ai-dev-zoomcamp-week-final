"""E-mail channel (plugin "common.email_smtp"): an SMTP client of an external provider.

docs/epics/E1_NOTIFICATIONS.md 6.1. No mail server runs in the image: each environment points
SMTP_HOST at its provider. The notifications module renders the texts; this plugin only builds a
plain-text message and submits it. Addresses and texts are never logged.
"""

from email.message import EmailMessage
from email.policy import EmailPolicy
from email.utils import formatdate, make_msgid, parseaddr

import aiosmtplib

from src.plugins.registry import DeliveryResult, OutgoingMessage, PluginRegistry

CHANNEL_NAME = "email"
SEND_TIMEOUT_SECONDS = 10.0
# RFC 2369 headers whose value is a URI in angle brackets: written as they are, never as RFC 2047
# encoded-words (which the default folder uses for a long value without spaces, and which mail
# services then do not read as a URI).
_VERBATIM_HEADERS = frozenset({"list-unsubscribe"})
MAX_HEADER_LINE = 998  # RFC 5322, without the CRLF


class _SmtpPolicy(EmailPolicy):
    """email.policy.SMTP, except that the list headers stay on one line, unencoded."""

    def fold_binary(self, name: str, value: str) -> bytes:
        line = f"{name}: {value}"
        if name.lower() in _VERBATIM_HEADERS and line.isascii() and len(line) <= MAX_HEADER_LINE:
            return f"{line}{self.linesep}".encode("ascii")
        return super().fold_binary(name, value)


SMTP_POLICY = _SmtpPolicy(linesep="\r\n")
SMTPUTF8_POLICY = SMTP_POLICY.clone(utf8=True)


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
        if message.unsubscribe_url:
            # RFC 8058: mail clients POST "List-Unsubscribe=One-Click" to the URL; a GET of it
            # changes nothing, so link scanners cannot unsubscribe anyone.
            email["List-Unsubscribe"] = f"<{message.unsubscribe_url}>"
            email["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        # text/plain; charset utf-8, quoted-printable: the message is 7-bit on the wire, so no
        # BODY=8BITMIME is needed (the bytes are sent raw, without aiosmtplib's negotiation).
        email.set_content(message.full_text, cte="quoted-printable")
        return email

    def envelope(self, message: OutgoingMessage) -> tuple[str, str, bytes, list[str]]:
        """Sender, recipient, the bytes on the wire and the MAIL options. The message is
        flattened here, with SMTP_POLICY, because aiosmtplib would fold the list headers into
        encoded-words; a non-ASCII address needs SMTPUTF8, as aiosmtplib itself would decide."""
        email = self.build(message)
        sender = parseaddr(self._sender)[1]
        recipient = str(message.address["email"])
        utf8 = not (sender + recipient).isascii()
        data = email.as_bytes(policy=SMTPUTF8_POLICY if utf8 else SMTP_POLICY)
        return sender, recipient, data, ["SMTPUTF8"] if utf8 else []

    async def send(self, message: OutgoingMessage) -> DeliveryResult:
        try:
            sender, recipient, data, mail_options = self.envelope(message)
        except (ValueError, UnicodeError):  # e.g. a line break in a header value: never retry
            return DeliveryResult(ok=False, error_code="EMAIL_INVALID")
        try:
            await aiosmtplib.send(
                data,
                sender=sender,
                recipients=[recipient],
                mail_options=mail_options,
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
