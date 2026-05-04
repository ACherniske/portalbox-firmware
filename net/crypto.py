# net/crypto.py
import ubinascii
import uhashlib
import machine
import time

try:
    import ucryptolib
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


def _is_hex(s) -> bool:
    """
    Check if a string is a valid hexadecimal string

    Args:
        s (str): The string to check
    Returns:
        bool: True if the string is hexadecimal, False otherwise
    """
    try:
        if not s:
            return False
        ubinascii.unhexlify(s)
        return True
    except (ValueError, TypeError):
        return False

def hash_password(password: str) -> str:
    """
    Hash password using SHA256

    Args:
        password (str): The password to hash

    Returns:
        str: The hexadecimal representation of the hashed password
    """
    hasher = uhashlib.sha256()
    hasher.update(password.encode('utf-8'))
    return ubinascii.hexlify(hasher.digest()).decode()

def get_encryption_key() -> bytes:
    """
    Generate a unique encryption key based on the device's unique ID

    Returns:
        bytes: A 16-byte encryption key derived from the device's unique ID
    """
    uid = machine.unique_id()
    hasher = uhashlib.sha256()
    hasher.update(uid)
    return hasher.digest()[:16]  # Use the first 16 bytes for AES-128

def encrypt_password(password: str) -> bytes:
    """
    Encrypt using AES-128 CBC or Base64 fallback

    Args:
        password (str): The password to encrypt

    Returns:
        bytes: The encrypted password, or Base64-encoded string if encryption is not available
    """
    if not CRYPTO_AVAILABLE:
        return ubinascii.b2a_base64(password.encode('utf-8')).decode().strip()

    try:
        import urandom
        # generate 128 bits (16 bytes) in 32-bit chunks
        iv = b''
        for _ in range(4):
            iv += urandom.getrandbits(32).to_bytes(4, 'big')
    except (ImportError, AttributeError, ValueError):
        iv = uhashlib.sha256(str(time.ticks_us()).encode()).digest()[:16]

    password_bytes = password.encode('utf-8')

    # Pad password to multiple of 16 bytes
    padding_length = 16 - (len(password_bytes) % 16)
    padded = password_bytes + bytes([padding_length] * padding_length)

    # Mode 2 = AES-128 CBC
    cipher = ucryptolib.aes(get_encryption_key(), 2, iv)  # AES-128 CBC
    encrypted = cipher.encrypt(padded)
    return ubinascii.hexlify(iv + encrypted).decode()

def decrypt_password(encrypted: str) -> str:
    """
    Decrypt password using AES-128 CBC or Base64 fallback

    Args:
        encrypted (str): The encrypted password, either as hex string for AES or Base64-encoded

    Returns:
        str: The decrypted password, or None if decryption fails
    """
    if not encrypted:
        return None

    if not _is_hex(encrypted):
        try:
            # Assume it's Base64-encoded if it's not hex
            return ubinascii.a2b_base64(encrypted).decode('utf-8')
        except Exception as e:
            print(f"[CRYPTO] Base64 decoding failed: {e}")
            return None

    if not CRYPTO_AVAILABLE:
        return None

    try:
        data = ubinascii.unhexlify(encrypted)
        iv, ciphertext = data[:16], data[16:]
        cipher = ucryptolib.aes(get_encryption_key(), 2, iv)  # AES-128 CBC
        decrypted = cipher.decrypt(ciphertext)
        padding_length = decrypted[-1]
        return decrypted[:-padding_length].decode('utf-8')
    except Exception as e:
        print(f"[CRYPTO] Decryption failed: {e}")
        return None
