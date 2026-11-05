"""Authenticate LINE media's single-blob and chunk-hash-list formats before decryption.

Wire contract independently checked against Chrome extension 3.7.2's FileEncryption
flow and a real authorized download. No keys or plaintext enter diagnostics.
"""

from __future__ import annotations

import hashlib
import hmac

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from okline.e2ee_crypto import derive_file_keys

CHUNK_SIZE = 128 * 1024
MAX_BLOB = 32 * 1024 * 1024


def decrypt_media_blob(key_material: bytes, blob: bytes) -> bytes:
    if not isinstance(key_material, bytes) or len(key_material) != 32:
        raise ValueError("media_material_invalid")
    if not isinstance(blob, bytes) or not 32 < len(blob) <= MAX_BLOB:
        raise ValueError("media_blob_size_invalid")
    ciphertext, tag = blob[:-32], blob[-32:]
    enc_key, mac_key, nonce = derive_file_keys(key_material)
    direct_tag = hmac.new(mac_key, ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(direct_tag, tag):
        # Large/streamed uploads authenticate the ordered SHA-256 digest of
        # each 128-KiB ciphertext chunk, rather than the ciphertext directly.
        hashes = b"".join(
            hashlib.sha256(ciphertext[i : i + CHUNK_SIZE]).digest()
            for i in range(0, len(ciphertext), CHUNK_SIZE)
        )
        chunked_tag = hmac.new(mac_key, hashes, hashlib.sha256).digest()
        if not hmac.compare_digest(chunked_tag, tag):
            raise ValueError("media_authentication_failed")
    # Only authenticated ciphertext reaches AES. Below the bounded size the
    # 32-bit WebCrypto counter cannot wrap, so full CTR increment is equivalent.
    decryptor = Cipher(algorithms.AES(enc_key), modes.CTR(nonce + b"\0" * 4)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()
