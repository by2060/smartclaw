from __future__ import annotations

import os
from typing import Optional


def get_api_tool_sm4_key_hex() -> Optional[str]:
    key_hex = os.environ.get("FLOCKS_API_TOOL_SM4_KEY_HEX")
    if isinstance(key_hex, str) and key_hex.strip():
        return key_hex.strip()
    try:
        from flocks.security import get_secret_manager

        secret_value = get_secret_manager().get("api_tool_sm4_key")
        if isinstance(secret_value, str) and secret_value.strip():
            return secret_value.strip()
    except Exception:
        pass
    return None


def decrypt_sm4_value(cipher_hex: str) -> str:
    key_hex = get_api_tool_sm4_key_hex()
    if not key_hex:
        raise ValueError("API tool SM4 key is not configured")
    try:
        from gmssl.sm4 import CryptSM4, SM4_DECRYPT
    except ImportError as e:
        raise ValueError("gmssl is required to decrypt API tool SM4 values") from e

    try:
        sm4 = CryptSM4()
        sm4.set_key(bytes.fromhex(key_hex), SM4_DECRYPT)
        plaintext = sm4.crypt_ecb(bytes.fromhex(cipher_hex.strip()))
        if plaintext:
            padding = plaintext[-1]
            if 1 <= padding <= 16 and plaintext.endswith(bytes([padding]) * padding):
                plaintext = plaintext[:-padding]
        return plaintext.decode("utf-8")
    except Exception as e:
        raise ValueError("Failed to decrypt API tool SM4 value") from e
