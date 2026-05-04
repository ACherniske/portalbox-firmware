import net.crypto as crypto
import ubinascii

def run_crypto_tests():
    print("--- Starting Crypto Test Suite ---")
    
    # Test Data
    plain_pass = "MicroPython_2026_Secure!"
    
    # 1. Test Hashing
    print("[TEST] Hashing consistency: ", end="")
    h1 = crypto.hash_password(plain_pass)
    h2 = crypto.hash_password(plain_pass)
    assert h1 == h2, "Hash should be deterministic"
    assert len(h1) == 64, "SHA256 hex string should be 64 chars"
    print("PASS")

    # 2. Test Hex Validation (Your new logic)
    print("[TEST] Hex validation: ", end="")
    assert crypto._is_hex("abcdef0123456789") == True
    assert crypto._is_hex("not-hex") == False
    assert crypto._is_hex("abc") == False # Odd length is invalid for unhexlify
    print("PASS")

    # 3. Test AES Round-trip (Encrypt -> Decrypt)
    print("[TEST] AES Round-trip: ", end="")
    encrypted = crypto.encrypt_password(plain_pass)
    decrypted = crypto.decrypt_password(encrypted)
    assert encrypted != plain_pass, "Encrypted string must not match plaintext"
    assert decrypted == plain_pass, f"Decryption failed! Got: {decrypted}"
    print("PASS")

    # 4. Test Base64 Fallback (if hex check fails)
    print("[TEST] Base64 Fallback: ", end="")
    b64_data = ubinascii.b2a_base64(b"b64_test_pass").decode().strip()
    decrypted_b64 = crypto.decrypt_password(b64_data)
    assert decrypted_b64 == "b64_test_pass", "Base64 fallback decryption failed"
    print("PASS")

    # 5. Test Key Uniqueness (Hardware-bound)
    print("[TEST] Hardware key access: ", end="")
    key = crypto.get_encryption_key()
    assert len(key) == 16, "AES key must be 16 bytes"
    print("PASS")

    print("--- All Crypto Tests Passed! ---")

if __name__ == "__main__":
    run_crypto_tests()