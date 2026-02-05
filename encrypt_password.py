#!/usr/bin/env python3
"""Helper script to encrypt password for WiFi configuration"""

import hashlib
import binascii
import os

# Try to use PyCryptodome, fall back to simple method
try:
    from Cryptodome.Cipher import AES
    CRYPTO_AVAILABLE = True
except ImportError:
    try:
        from Crypto.Cipher import AES
        CRYPTO_AVAILABLE = True
    except ImportError:
        CRYPTO_AVAILABLE = False

# Configuration
PASSWORD = "X638L4Wp"  # Replace with the password to encrypt
ENCRYPTION_KEY = b'portalbox_secret'  # Must be 16, 24, or 32 bytes for AES

def get_encryption_key():
    """Get encryption key (padded to 16 bytes for AES-128)"""
    key = ENCRYPTION_KEY[:16]
    if len(key) < 16:
        key = key + b'\x00' * (16 - len(key))
    return key

def encrypt_password(password):
    """Encrypt password using AES-128 CBC"""
    if not CRYPTO_AVAILABLE:
        print("WARNING: PyCryptodome not available, using base64")
        import base64
        return base64.b64encode(password.encode('utf-8')).decode()
    
    # Generate random IV
    iv = os.urandom(16)
    
    # Pad password to multiple of 16 bytes (PKCS7 padding)
    password_bytes = password.encode('utf-8')
    pad_len = 16 - (len(password_bytes) % 16)
    padded = password_bytes + bytes([pad_len] * pad_len)
    
    # Encrypt
    key = get_encryption_key()
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(padded)
    
    # Return IV + encrypted data as hex
    return binascii.hexlify(iv + encrypted).decode()

def hash_password(password):
    """Hash password using SHA256"""
    hasher = hashlib.sha256()
    hasher.update(password.encode('utf-8'))
    return binascii.hexlify(hasher.digest()).decode()

if __name__ == "__main__":
    print("Password Encryption Tool")
    print("=" * 60)
    print(f"Password: {PASSWORD}")
    print("=" * 60)
    
    encrypted = encrypt_password(PASSWORD)
    hashed = hash_password(PASSWORD)
    
    print(f"\nEncrypted password (for config):")
    print(encrypted)
    print(f"\nPassword hash (for config):")
    print(hashed)
    
    print("\n" + "=" * 60)
    print("Copy these values to config/wifi_config.json:")
    print("  password_encrypted: " + encrypted)
    print("  password_hash: " + hashed)
    print("=" * 60)
