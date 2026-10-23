"""End-to-end encryption for live sync.

Both machines turn the one-time PIN into a shared key with SPAKE2, a
password-authenticated key exchange: the PIN never crosses the relay, and a
relay that tries to take part gets one guess per session (a wrong guess
ends the session) and learns nothing it can test PINs against offline. The
key is bound to the protocol label and the room ID.

After the exchange every frame is AES-256-GCM. Each direction has its own
key and counts its frames; the counter is the nonce, so a frame that is
altered, dropped, replayed or reordered fails to decrypt.
"""

from __future__ import annotations

import base64
import binascii

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from spake2 import SPAKE2_A, SPAKE2_B
from spake2.spake2 import SPAKEError

PROTOCOL = 2
_LABEL = b"imprint-sync/2"


class KeyExchangeError(Exception):
    """The peer's key-exchange message was malformed."""


class FrameError(Exception):
    """A frame did not decrypt: wrong key, or altered, dropped, replayed or
    reordered on the way."""


def _derive(key: bytes, purpose: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_LABEL + b" " + purpose).derive(key)


def _nonce(counter: int) -> bytes:
    return counter.to_bytes(12, "big")


class Cipher:
    """One session's frame protection: a key and frame counter per direction."""

    def __init__(self, send_key: bytes, receive_key: bytes):
        self._send, self._receive = AESGCM(send_key), AESGCM(receive_key)
        self._sent = self._received = 0

    def seal(self, plaintext: bytes) -> bytes:
        frame = self._send.encrypt(_nonce(self._sent), plaintext, None)
        self._sent += 1
        return frame

    def open(self, frame: bytes) -> bytes:
        try:
            plaintext = self._receive.decrypt(_nonce(self._received), frame, None)
        except InvalidTag:
            raise FrameError("frame failed authentication") from None
        self._received += 1
        return plaintext


class KeyExchange:
    """One side of the PIN-authenticated key exchange for a room."""

    def __init__(self, role: str, pin: str, room_id: str):
        if role not in ("consumer", "provider"):
            raise ValueError(f"unknown role {role!r}")
        self._role = role
        room = room_id.encode()
        side = SPAKE2_A if role == "consumer" else SPAKE2_B
        self._spake = side(pin.encode(), idA=_LABEL + b" consumer " + room, idB=_LABEL + b" provider " + room)
        self._message = base64.b64encode(self._spake.start()).decode()

    @property
    def message(self) -> str:
        """This side's exchange message, safe to send in the clear."""
        return self._message

    def finish(self, peer_message: object) -> Cipher:
        """Derive the session cipher from the peer's message. Both sides
        always derive a key; it only matches when the PINs match, which the
        first encrypted frame proves."""
        if not isinstance(peer_message, str):
            raise KeyExchangeError("missing key-exchange message")
        try:
            key = self._spake.finish(base64.b64decode(peer_message, validate=True))
        except (SPAKEError, ValueError, binascii.Error) as e:
            raise KeyExchangeError(f"malformed key-exchange message: {e}") from None
        to_provider, to_consumer = _derive(key, b"consumer->provider"), _derive(key, b"provider->consumer")
        if self._role == "consumer":
            return Cipher(to_provider, to_consumer)
        return Cipher(to_consumer, to_provider)
