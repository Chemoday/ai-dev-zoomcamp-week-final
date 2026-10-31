"""The WebPush plugin on its own: endpoint check, RFC 8291 encryption, VAPID, answers."""

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from src.core.config import Settings
from src.plugins.common import webpush
from src.plugins.common.webpush import (
    MAX_PAYLOAD_BYTES,
    WebPushChannel,
    b64url_decode,
    b64url_encode,
    build_payload,
    encrypt,
    generate_vapid_keys,
    is_allowed_endpoint,
    load_private_key,
    public_key_bytes,
    push_service_result,
    topic,
)
from src.plugins.registry import DeliveryResult, OutgoingMessage, PluginRegistry
from tests.webpush_browser import decrypt

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123:APA91bTest"
VAPID_PUBLIC, VAPID_PRIVATE = generate_vapid_keys()


def browser_keys() -> tuple[ec.EllipticCurvePrivateKey, str, bytes]:
    """A simulated browser: its private key, its p256dh (base64url) and its auth secret."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key, b64url_encode(public_key_bytes(key.public_key())), b"0123456789abcdef"


def message(**overrides: object) -> OutgoingMessage:
    values: dict = {
        "address": {"endpoint": ENDPOINT, "p256dh": "", "auth": ""},
        "locale": "en",
        "event_type": "EMERGENCY_PUBLISHED",
        "subject": "Emergency: Water off",
        "body": "Riser 2.",
        "footer": "Open the site: https://kyu.example.test",
        "deep_link": "https://kyu.example.test",
        "urgent": True,
        "collapse_key": "post:5f0c",
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    return OutgoingMessage(**(values | overrides))


def channel() -> WebPushChannel:
    return WebPushChannel(
        public_key=VAPID_PUBLIC,
        private_key=load_private_key(b64url_decode(VAPID_PRIVATE)),
        subject="mailto:board@kyu.example.test",
    )


# --- Encryption ---------------------------------------------------------------------------------


def test_encryption_matches_the_example_of_rfc_8291() -> None:
    # RFC 8291, Appendix A.
    sender = load_private_key(b64url_decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"))
    ua_public = b64url_decode(
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
    )
    body = encrypt(
        b"When I grow up, I want to be a watermelon",
        ua_public,
        b64url_decode("BTBZMqHH6r4Tts7J_aSIgg"),
        sender_key=sender,
        salt=b64url_decode("DGv6ra1nlYgDCS1FRnbzlw"),
    )

    assert b64url_encode(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYW"
        "AmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgS"
        "xsj_Qulcy4a-fN"
    )


def test_a_browser_can_decrypt_what_was_sent() -> None:
    ua_key, p256dh, auth = browser_keys()

    body = encrypt(b'{"title":"x"}', b64url_decode(p256dh), auth)

    assert decrypt(body, ua_key, auth) == b'{"title":"x"}'


@pytest.mark.parametrize(
    ("ua_public", "auth"),
    [
        (b"\x04" + b"\x00" * 64, b"0123456789abcdef"),  # not a point on the curve
        (b"\x02" + b"\x00" * 32, b"0123456789abcdef"),  # compressed
        (None, b"short"),
    ],
)
def test_invalid_subscription_keys_raise(ua_public: bytes | None, auth: bytes) -> None:
    ua_public = ua_public or b64url_decode(browser_keys()[1])

    with pytest.raises(ValueError):
        encrypt(b"x", ua_public, auth)


# --- Endpoint check -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/gAAAA",
        "https://wns2-par02p.notify.windows.com/w/?token=BQYAAAB",
        "https://web.push.apple.com/QGuQyavXutnMH",
    ],
)
def test_known_push_services_are_accepted(endpoint: str) -> None:
    assert is_allowed_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/abc",  # not https
        "https://fcm.googleapis.com:8443/fcm/send/abc",  # not the default port
        "https://fcm.googleapis.com:443/fcm/send/abc",
        "https://user@fcm.googleapis.com/fcm/send/abc",  # user info
        "https://fcm.googleapis.com@evil.test/x",
        "https://fcm.googleapis.com\\@evil.test/x",  # parsers disagree on backslashes
        "https://evil.test/x\\@fcm.googleapis.com/",
        "https://evilfcm.googleapis.com/x",  # not a dot boundary
        "https://fcm.googleapis.com.evil.test/x",
        "https://notify.windows.com/x",  # the wildcard needs a sub-domain
        "https://xnotify.windows.com/x",
        "https://142.250.74.106/fcm/send/abc",  # an IP address
        "https://[::1]/x",
        "https://fcm.googleapis.com",  # no path
        "https://fcm.googleapis.com/fcm send",  # whitespace
        "https://fcm.googleapis.com/é",  # not ASCII
        "https://fcm%2Egoogleapis.com/x",
        "https://FCM.googleapis.com/fcm/send/abc",  # browsers send lower case; one hash per device
        "https://a..notify.windows.com/x",  # an empty label
        "https://xn--zz.notify.windows.com/x",  # punycode (here an invalid A-label)
        "https://-a.notify.windows.com/x",
        "https://" + "a" * 64 + ".notify.windows.com/x",  # a label over 63 characters
        "https://fcm.googleapis.com/" + "a" * 2048,  # too long
        "",
    ],
)
def test_anything_else_is_rejected(endpoint: str) -> None:
    assert not is_allowed_endpoint(endpoint)


# --- Payload, headers and VAPID -----------------------------------------------------------------


def test_the_payload_has_no_footer_and_a_link_to_the_site() -> None:
    payload = json.loads(build_payload(message()))

    assert payload == {
        "v": 1,
        "event_type": "EMERGENCY_PUBLISHED",
        "title": "Emergency: Water off",
        "body": "Riser 2.",
        "url": "https://kyu.example.test",
        "tag": "post:5f0c",
        "urgent": True,
    }


def test_a_long_body_is_cut_to_fit_one_push_message() -> None:
    payload = build_payload(message(body="é" * 5000))

    assert len(payload) <= MAX_PAYLOAD_BYTES
    assert json.loads(payload)["body"].endswith("…")


def test_the_topic_is_32_base64url_characters_without_the_post_id() -> None:
    value = topic("post:5f0c1a2b-0000-4000-8000-000000000000")

    assert len(value) == 32 and "5f0c" not in value
    assert set(value) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


async def test_a_push_is_encrypted_signed_and_marked_urgent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ua_key, p256dh, auth = browser_keys()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201)

    monkeypatch.setattr(WebPushChannel, "http_transport", httpx.MockTransport(handler))
    address = {"endpoint": ENDPOINT, "p256dh": p256dh, "auth": b64url_encode(auth)}

    result = await channel().send(message(address=address))

    assert result == DeliveryResult(ok=True)
    [request] = requests
    assert str(request.url) == ENDPOINT
    assert request.headers["content-encoding"] == "aes128gcm"
    assert request.headers["urgency"] == "high"
    assert 3500 <= int(request.headers["ttl"]) <= 3600
    assert request.headers["topic"] == topic("post:5f0c")
    assert json.loads(decrypt(request.content, ua_key, auth))["title"] == "Emergency: Water off"

    scheme, credentials = request.headers["authorization"].split(" ", 1)
    fields = dict(part.split("=", 1) for part in credentials.split(", "))
    assert scheme == "vapid" and fields["k"] == VAPID_PUBLIC
    vapid_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), b64url_decode(VAPID_PUBLIC)
    )
    claims = jwt.decode(
        fields["t"], vapid_key, algorithms=["ES256"], audience="https://fcm.googleapis.com"
    )
    assert claims["sub"] == "mailto:board@kyu.example.test"
    assert time.time() < claims["exp"] <= time.time() + 24 * 3600


async def test_other_messages_are_normal_and_an_expired_one_has_ttl_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, p256dh, auth = browser_keys()
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        WebPushChannel,
        "http_transport",
        httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(201)),
    )
    address = {"endpoint": ENDPOINT, "p256dh": p256dh, "auth": b64url_encode(auth)}

    await channel().send(
        message(address=address, urgent=False, expires_at=datetime.now(UTC) - timedelta(1))
    )

    assert requests[0].headers["urgency"] == "normal"
    assert requests[0].headers["ttl"] == "0"


async def test_a_stored_endpoint_off_the_allowlist_is_never_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    monkeypatch.setattr(WebPushChannel, "http_transport", httpx.MockTransport(handler))
    address = {"endpoint": "https://169.254.169.254/latest", "p256dh": "x", "auth": "y"}

    result = await channel().send(message(address=address))

    assert result == DeliveryResult(
        ok=False, error_code="WEBPUSH_ENDPOINT_REJECTED", revoke_binding=True
    )


async def test_a_payload_that_cannot_fit_fails_without_revoking() -> None:
    _, p256dh, auth = browser_keys()
    address = {"endpoint": ENDPOINT, "p256dh": p256dh, "auth": b64url_encode(auth)}

    result = await channel().send(message(address=address, subject="x" * 5000))

    assert result == DeliveryResult(ok=False, error_code="WEBPUSH_PAYLOAD_TOO_LARGE")


async def test_broken_keys_revoke_the_binding() -> None:
    address = {"endpoint": ENDPOINT, "p256dh": b64url_encode(b"\x04" + b"\0" * 64), "auth": "AA"}

    result = await channel().send(message(address=address))

    assert result == DeliveryResult(
        ok=False, error_code="WEBPUSH_INVALID_KEYS", revoke_binding=True
    )


async def test_a_network_error_is_retryable_and_redirects_are_not_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, p256dh, auth = browser_keys()
    address = {"endpoint": ENDPOINT, "p256dh": p256dh, "auth": b64url_encode(auth)}

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr(WebPushChannel, "http_transport", httpx.MockTransport(broken))
    assert await channel().send(message(address=address)) == DeliveryResult(
        ok=False, retryable=True, error_code="WEBPUSH_UNAVAILABLE"
    )

    calls: list[str] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://169.254.169.254/"})

    monkeypatch.setattr(WebPushChannel, "http_transport", httpx.MockTransport(redirect))
    result = await channel().send(message(address=address))

    assert calls == [ENDPOINT]
    assert result == DeliveryResult(ok=False, error_code="WEBPUSH_REJECTED")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (201, DeliveryResult(ok=True)),
        (404, DeliveryResult(ok=False, error_code="WEBPUSH_GONE", revoke_binding=True)),
        (410, DeliveryResult(ok=False, error_code="WEBPUSH_GONE", revoke_binding=True)),
        (413, DeliveryResult(ok=False, error_code="WEBPUSH_PAYLOAD_TOO_LARGE")),
        (429, DeliveryResult(ok=False, retryable=True, error_code="WEBPUSH_RATE_LIMITED")),
        (503, DeliveryResult(ok=False, retryable=True, error_code="WEBPUSH_UNAVAILABLE")),
        (403, DeliveryResult(ok=False, error_code="WEBPUSH_AUTH_FAILED")),
        (400, DeliveryResult(ok=False, error_code="WEBPUSH_REJECTED")),
    ],
)
def test_push_service_answers(status: int, expected: DeliveryResult) -> None:
    assert push_service_result(status) == expected


# --- Registration and the admin command ---------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"VAPID_PRIVATE_KEY": ""}, "VAPID_PRIVATE_KEY"),
        ({"VAPID_PRIVATE_KEY": "not base64!"}, "VAPID_PRIVATE_KEY"),
        ({"VAPID_PUBLIC_KEY": generate_vapid_keys()[0]}, "VAPID_PUBLIC_KEY"),
        ({"VAPID_SUBJECT": ""}, "VAPID_SUBJECT"),
        ({"VAPID_SUBJECT": "board@kyu.example.test"}, "VAPID_SUBJECT"),
    ],
)
def test_the_plugin_refuses_incomplete_settings(overrides: dict, error: str) -> None:
    values = {
        "VAPID_PUBLIC_KEY": VAPID_PUBLIC,
        "VAPID_PRIVATE_KEY": VAPID_PRIVATE,
        "VAPID_SUBJECT": "mailto:board@kyu.example.test",
        **overrides,
    }

    with pytest.raises(RuntimeError, match=error):
        PluginRegistry(Settings(**values)).load(["common.webpush"])


def test_the_plugin_registers_the_webpush_channel() -> None:
    registry = PluginRegistry(
        Settings(
            VAPID_PUBLIC_KEY=VAPID_PUBLIC,
            VAPID_PRIVATE_KEY=VAPID_PRIVATE,
            VAPID_SUBJECT="https://kyu.example.test",
        )
    )

    registry.load(["common.webpush"])

    push = registry.channel("webpush")
    assert isinstance(push, WebPushChannel)
    assert push.public_key == VAPID_PUBLIC
    assert VAPID_PRIVATE not in repr(vars(push))


def test_generated_keys_are_a_matching_pair(capsys: pytest.CaptureFixture[str]) -> None:
    assert webpush.main(["generate-vapid-keys"]) == 0
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())

    private = load_private_key(b64url_decode(lines["VAPID_PRIVATE_KEY"]))
    assert b64url_encode(public_key_bytes(private.public_key())) == lines["VAPID_PUBLIC_KEY"]
