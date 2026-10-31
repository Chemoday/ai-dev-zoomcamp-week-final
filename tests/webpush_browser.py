"""The browser's side of WebPush for tests: decrypting an `aes128gcm` push (RFC 8291).

Written out separately from src/plugins/common/webpush.py, so a round trip checks the plugin
against a second reading of the RFC.
"""

import hashlib
import hmac

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.plugins.common.webpush import public_key_bytes


def decrypt(body: bytes, ua_key: ec.EllipticCurvePrivateKey, auth_secret: bytes) -> bytes:
    """The browser's side of RFC 8291, written out separately from the plugin."""

    def hkdf_one_block(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
        prk = hmac.new(salt, ikm, hashlib.sha256).digest()
        return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]

    salt, record_size, key_length = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    sender_public = body[21 : 21 + key_length]
    assert record_size == 4096 and key_length == 65
    sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public)
    ua_public = public_key_bytes(ua_key.public_key())
    shared = ua_key.exchange(ec.ECDH(), sender)
    ikm = hkdf_one_block(auth_secret, shared, b"WebPush: info\x00" + ua_public + sender_public, 32)
    cek = hkdf_one_block(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf_one_block(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    padded = AESGCM(cek).decrypt(nonce, body[21 + key_length :], None)
    assert padded.endswith(b"\x02")  # the last (and only) record
    return padded[:-1]
