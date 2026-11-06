import hashlib
import hmac

import pytest
from okline.e2ee_crypto import derive_file_keys, encrypt_blob

from line_agent.media_crypto import CHUNK_SIZE, decrypt_media_blob

KEY = hashlib.sha256(b"fictional-fixture-key-material").digest()


@pytest.mark.parametrize(
    "size", [1, CHUNK_SIZE - 1, CHUNK_SIZE, CHUNK_SIZE + 1, CHUNK_SIZE * 6 + 13]
)
def test_both_formats_and_tampering(size):
    plain = (b"fixture-video-data" * ((size // len(b"fixture-video-data")) + 1))[:size]
    assert len(plain) == size
    direct = encrypt_blob(KEY, plain)
    assert decrypt_media_blob(KEY, direct) == plain
    ciphertext = direct[:-32]
    _, mac, _ = derive_file_keys(KEY)
    hashes = b"".join(
        hashlib.sha256(ciphertext[i : i + CHUNK_SIZE]).digest()
        for i in range(0, len(ciphertext), CHUNK_SIZE)
    )
    chunked = ciphertext + hmac.new(mac, hashes, hashlib.sha256).digest()
    assert decrypt_media_blob(KEY, chunked) == plain
    for candidate in [
        bytes([chunked[0] ^ 1]) + chunked[1:],
        chunked[:-1] + bytes([chunked[-1] ^ 1]),
        chunked[:-33] + chunked[-32:],
    ]:
        with pytest.raises(ValueError):
            decrypt_media_blob(KEY, candidate)
    with pytest.raises(ValueError):
        decrypt_media_blob(b"x" * 32, chunked)


def test_order_and_truncation_rejected():
    plain = b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE
    ciphertext = encrypt_blob(KEY, plain)[:-32]
    _, mac, _ = derive_file_keys(KEY)
    hashes = b"".join(
        hashlib.sha256(ciphertext[i : i + CHUNK_SIZE]).digest()
        for i in range(0, len(ciphertext), CHUNK_SIZE)
    )
    tag = hmac.new(mac, hashes, hashlib.sha256).digest()
    with pytest.raises(ValueError):
        decrypt_media_blob(KEY, ciphertext[CHUNK_SIZE:] + ciphertext[:CHUNK_SIZE] + tag)
    with pytest.raises(ValueError):
        decrypt_media_blob(KEY, b"x" * 32)
