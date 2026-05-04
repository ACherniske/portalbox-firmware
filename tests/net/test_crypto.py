from tests.test_helper import TestRunner
import net.crypto as crypto

def run_all():
    runner = TestRunner()

    # Define test wrappers
    def test_hashing():
        h1 = crypto.hash_password("pass123")
        assert h1 == crypto.hash_password("pass123")
        assert h1 != crypto.hash_password("pass124")

    def test_aes_roundtrip():
        original = "Secret_123!"
        encrypted = crypto.encrypt_password(original)
        decrypted = crypto.decrypt_password(encrypted)
        assert original == decrypted, f"Expected {original}, got {decrypted}"

    def test_hex_check():
        assert crypto._is_hex("abcdef0123") == True
        assert crypto._is_hex("not-hex!") == False

    # Execute via runner
    runner.run_test("Hashing Logic", test_hashing)
    runner.run_test("Hex Validation", test_hex_check)
    runner.run_test("AES Encrypt/Decrypt", test_aes_roundtrip)

    # Show final LED result
    runner.final_report()

if __name__ == "__main__":
    run_all()
