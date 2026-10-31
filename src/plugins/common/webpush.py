"""WebPush channel (plugin "common.webpush"): push to a browser or an installed PWA.

docs/API.md 6 ("WebPush"), docs/epics/E1_NOTIFICATIONS.md 6.3 and 10.7. The notifications module
owns the subscriptions (bindings); this plugin checks endpoints, encrypts and sends.

- Encryption: RFC 8291 (`aes128gcm`, RFC 8188), one record; the push service never sees the text.
- Authentication: VAPID (RFC 8292), an ES256 JWT for the push service's origin.
- Server-side request forgery guard: an endpoint is accepted, and later called, only when it is
  https on the default port with no user info and its host is a known push service. No redirects
  are followed and every send has a 10 s timeout.

Endpoints are capability URLs (whoever knows one can push to that device): they are never logged,
and errors keep no provider text.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.plugins.registry import DeliveryResult, OutgoingMessage, PluginRegistry

CHANNEL_NAME = "webpush"
SEND_TIMEOUT_SECONDS = 10.0
MAX_ENDPOINT_LENGTH = 2048
# Push services must accept 4096 bytes of body (RFC 8030 7.2): the 86-byte header, the payload,
# the 1-byte padding delimiter and the 16-byte tag.
MAX_PAYLOAD_BYTES = 4096 - 86 - 1 - 16
RECORD_SIZE = 4096
VAPID_TOKEN_LIFETIME_SECONDS = 12 * 3600  # RFC 8292: at most 24 hours
DEFAULT_TTL_SECONDS = 24 * 3600
TOPIC_LENGTH = 32  # RFC 8030 5.4: at most 32 characters of the base64url alphabet

# Known push services (docs/API.md 6). "*." entries match sub-domains at a dot boundary only.
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "*.notify.windows.com",
    "web.push.apple.com",
)
# Printable ASCII only, and the authority ends at the first "/": nothing a URL parser could read
# differently (backslashes, "@", spaces, percent-encoded hosts). The host is lower-case LDH labels
# (browsers send it so), never punycode: none of the push services uses it, and httpx would reject
# an invalid A-label only at send time.
_LABEL = r"(?!xn--)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_ENDPOINT = re.compile(
    rf"https://(?P<host>(?:{_LABEL}\.)+{_LABEL})(?P<path>/[\x21-\x5b\x5d-\x7e]*)"
)


class PayloadTooLargeError(Exception):
    """The message does not fit one push message even with its body cut."""


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(value: str) -> bytes:
    """Strict base64url, with or without padding; raises ValueError."""
    if not re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", value):
        raise ValueError("not base64url")
    stripped = value.rstrip("=")
    return base64.urlsafe_b64decode(stripped + "=" * (-len(stripped) % 4))


# --- Endpoint check -----------------------------------------------------------------------------


def _host_allowed(host: str) -> bool:
    for allowed in PUSH_SERVICE_HOSTS:
        if allowed.startswith("*."):
            if host.endswith(allowed[1:]) and len(host) > len(allowed) - 1:
                return True
        elif host == allowed:
            return True
    return False


def is_allowed_endpoint(endpoint: str) -> bool:
    """https, default port, no user info, no IP literal, a known push service (docs/API.md 6)."""
    if len(endpoint) > MAX_ENDPOINT_LENGTH:
        return False
    match = _ENDPOINT.fullmatch(endpoint)
    if match is None:
        return False
    parts = urlsplit(endpoint)
    host = match.group("host")
    if parts.scheme != "https" or parts.username or parts.password or parts.hostname != host:
        return False
    try:  # the parser that sends must see the same host
        if httpx.URL(endpoint).host != host:
            return False
    except (httpx.InvalidURL, ValueError):
        return False
    try:
        if parts.port is not None:
            return False
    except ValueError:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return _host_allowed(host)


# --- Keys ---------------------------------------------------------------------------------------


def public_key_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    """The 65-byte uncompressed point, as browsers and VAPID use it."""
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def load_public_key(raw: bytes) -> ec.EllipticCurvePublicKey:
    """A P-256 point from its 65 uncompressed bytes; raises ValueError for anything else."""
    if len(raw) != 65 or raw[0] != 0x04:
        raise ValueError("not an uncompressed P-256 point")
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)


def load_private_key(raw: bytes) -> ec.EllipticCurvePrivateKey:
    """A P-256 private key from its 32-byte scalar (the format of VAPID_PRIVATE_KEY)."""
    if len(raw) != 32:
        raise ValueError("a VAPID private key is 32 bytes")
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def generate_vapid_keys() -> tuple[str, str]:
    """(public, private), both base64url, for VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return b64url_encode(public_key_bytes(key.public_key())), b64url_encode(private)


# --- RFC 8291 encryption ------------------------------------------------------------------------


def _hmac_sha256(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(
    plaintext: bytes,
    ua_public: bytes,
    auth_secret: bytes,
    *,
    sender_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """The `aes128gcm` body for one subscription (RFC 8291 3.4 and 4, RFC 8188 2).

    `sender_key` and `salt` are fresh for every message; tests pass fixed ones to compare with
    the RFC's example. Raises ValueError for invalid subscription keys and PayloadTooLargeError.
    """
    if len(auth_secret) != 16:
        raise ValueError("the auth secret is 16 bytes")
    if len(plaintext) > MAX_PAYLOAD_BYTES:
        raise PayloadTooLargeError
    receiver = load_public_key(ua_public)
    sender_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    salt = salt if salt is not None else os.urandom(16)
    as_public = public_key_bytes(sender_key.public_key())

    ecdh_secret = sender_key.exchange(ec.ECDH(), receiver)
    prk_key = _hmac_sha256(auth_secret, ecdh_secret)
    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = _hmac_sha256(prk_key, key_info + b"\x01")
    prk = _hmac_sha256(salt, ikm)
    cek = _hmac_sha256(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac_sha256(prk, b"Content-Encoding: nonce\x00\x01")[:12]

    # One record, so it is the last one: the padding delimiter is 0x02 and no padding follows.
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


# --- VAPID --------------------------------------------------------------------------------------


def vapid_authorization(
    endpoint: str, private_key: ec.EllipticCurvePrivateKey, public_key: str, subject: str
) -> str:
    """The `Authorization: vapid t=..., k=...` header (RFC 8292 3) for the endpoint's origin."""
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int(time.time()) + VAPID_TOKEN_LIFETIME_SECONDS,
        "sub": subject,
    }
    token = jwt.encode(claims, private_key, algorithm="ES256", headers={"typ": "JWT"})
    return f"vapid t={token}, k={public_key}"


# --- The payload the service worker receives ----------------------------------------------------


def build_payload(message: OutgoingMessage) -> bytes:
    """JSON for the service worker (docs/FRONTEND_GUIDE.md 11), cut to fit one push message.

    The footer is left out: a click on the notification opens `url`.
    """

    def encode(body: str) -> bytes:
        data: dict[str, Any] = {
            "v": 1,
            "event_type": message.event_type,
            "title": message.subject,
            "body": body,
            "url": message.deep_link,
            "tag": message.collapse_key,
            "urgent": message.urgent,
        }
        return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()

    payload = encode(message.body)
    if len(payload) <= MAX_PAYLOAD_BYTES:
        return payload
    # The longest prefix of the body that fits (JSON escapes make bytes per character vary).
    low, high = 0, len(message.body)
    while low < high:
        middle = (low + high + 1) // 2
        if len(encode(message.body[:middle] + "\u2026")) <= MAX_PAYLOAD_BYTES:
            low = middle
        else:
            high = middle - 1
    return encode(message.body[:low] + "\u2026" if low else "")


def topic(collapse_key: str) -> str:
    """The `Topic` header: a newer message with the same topic replaces a queued one. A hash, as
    the push service may only see 32 base64url characters (and no post ids)."""
    return b64url_encode(hashlib.sha256(collapse_key.encode()).digest())[:TOPIC_LENGTH]


def push_service_result(status: int) -> DeliveryResult:
    """Map a push service answer to a delivery result (machine codes only, RFC 8030 and 8292)."""
    if 200 <= status <= 202:
        return DeliveryResult(ok=True)
    if status in (404, 410):  # the subscription expired or was unsubscribed
        return DeliveryResult(ok=False, error_code="WEBPUSH_GONE", revoke_binding=True)
    if status == 429:
        return DeliveryResult(ok=False, retryable=True, error_code="WEBPUSH_RATE_LIMITED")
    if status >= 500:
        return DeliveryResult(ok=False, retryable=True, error_code="WEBPUSH_UNAVAILABLE")
    if status in (401, 403):  # VAPID keys or subject rejected: a configuration error
        return DeliveryResult(ok=False, error_code="WEBPUSH_AUTH_FAILED")
    if status == 413:
        return DeliveryResult(ok=False, error_code="WEBPUSH_PAYLOAD_TOO_LARGE")
    return DeliveryResult(ok=False, error_code="WEBPUSH_REJECTED")  # 400, redirects, the rest


class WebPushChannel:
    name = CHANNEL_NAME
    # Tests swap in an httpx.MockTransport (a fake push service); None means the real network.
    http_transport: ClassVar[httpx.AsyncBaseTransport | None] = None

    def __init__(
        self, *, public_key: str, private_key: ec.EllipticCurvePrivateKey, subject: str
    ) -> None:
        self.public_key = public_key  # served to browsers for pushManager.subscribe
        self._private_key = private_key
        self._subject = subject

    def accepts_endpoint(self, endpoint: str) -> bool:
        return is_allowed_endpoint(endpoint)

    async def send(self, message: OutgoingMessage) -> DeliveryResult:
        address: Mapping[str, object] = message.address
        endpoint = str(address.get("endpoint", ""))
        if not is_allowed_endpoint(endpoint):  # checked on subscribe too; never call elsewhere
            return DeliveryResult(
                ok=False, error_code="WEBPUSH_ENDPOINT_REJECTED", revoke_binding=True
            )
        try:
            body = encrypt(
                build_payload(message),
                b64url_decode(str(address.get("p256dh", ""))),
                b64url_decode(str(address.get("auth", ""))),
            )
        except PayloadTooLargeError:  # titles are short, so a bug; the device is fine
            return DeliveryResult(ok=False, error_code="WEBPUSH_PAYLOAD_TOO_LARGE")
        except ValueError:  # keys are checked on subscribe, so this is defensive
            return DeliveryResult(ok=False, error_code="WEBPUSH_INVALID_KEYS", revoke_binding=True)
        headers = {
            "Authorization": vapid_authorization(
                endpoint, self._private_key, self.public_key, self._subject
            ),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(_ttl_seconds(message.expires_at)),
            "Urgency": "high" if message.urgent else "normal",
        }
        if message.collapse_key:
            headers["Topic"] = topic(message.collapse_key)
        try:
            async with httpx.AsyncClient(
                timeout=SEND_TIMEOUT_SECONDS,
                follow_redirects=False,
                transport=self.http_transport,
            ) as client:
                response = await client.post(endpoint, content=body, headers=headers)
        except httpx.HTTPError:  # timeouts and connection errors; the text may hold the endpoint
            return DeliveryResult(ok=False, retryable=True, error_code="WEBPUSH_UNAVAILABLE")
        except (httpx.InvalidURL, ValueError):  # a host httpx cannot use (the check should stop it)
            return DeliveryResult(
                ok=False, error_code="WEBPUSH_ENDPOINT_REJECTED", revoke_binding=True
            )
        return push_service_result(response.status_code)


def _ttl_seconds(expires_at: datetime | None) -> int:
    if expires_at is None:
        return DEFAULT_TTL_SECONDS
    return max(int((expires_at - datetime.now(UTC)).total_seconds()), 0)


def register(registry: PluginRegistry) -> None:
    settings = registry.settings
    public_key = settings.VAPID_PUBLIC_KEY
    subject = settings.VAPID_SUBJECT
    try:
        private_key = load_private_key(b64url_decode(settings.VAPID_PRIVATE_KEY.get_secret_value()))
    except ValueError:
        raise RuntimeError(
            "plugin common.webpush needs VAPID_PRIVATE_KEY (base64url of 32 bytes)"
        ) from None
    if b64url_encode(public_key_bytes(private_key.public_key())) != public_key.rstrip("="):
        raise RuntimeError("VAPID_PUBLIC_KEY does not belong to VAPID_PRIVATE_KEY")
    if not re.fullmatch(r"mailto:\S+@\S+|https://\S+", subject):
        raise RuntimeError("VAPID_SUBJECT must be a mailto: or https: contact (RFC 8292)")
    registry.register_channel(
        WebPushChannel(public_key=public_key.rstrip("="), private_key=private_key, subject=subject)
    )


def main(argv: list[str] | None = None) -> int:
    """`uv run python -m src.plugins.common.webpush generate-vapid-keys`: a new key pair.

    Prints the private key to the terminal, like any key generator: put it into the secret store
    of one environment and nowhere else. Changing the keys makes every browser subscribe again.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m src.plugins.common.webpush")
    parser.add_argument("command", choices=["generate-vapid-keys"])
    parser.parse_args(argv)
    public, private = generate_vapid_keys()
    print(f"VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
