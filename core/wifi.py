# core/wifi.py
# Module for managing WiFi connections

import network
import time
import ubinascii
import uhashlib
import ujson as json
import machine
import sys
from micropython import const
try:
    import ucryptolib
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


# Network status codes
class NetworkStatus:
    """Network connection status codes."""
    IDLE = const(1000)
    CONNECTING = const(1001)
    WRONG_PASSWORD = const(1002)
    NO_AP_FOUND = const(1003)
    CONNECT_FAIL = const(1004)
    GOT_IP = const(1010)


# Network types
class NetworkType:
    """Network authentication types."""
    WPA2_PSK = const(0)
    WPA3_PSK = const(1)
    WPA2_ENTERPRISE = const(2)


# EAP methods for Enterprise networks
class EAPMethod:
    """EAP authentication methods for WPA Enterprise."""
    PWD = 'PWD'           # EAP-PWD (recommended - fast, secure)
    PEAP = 'PEAP'         # EAP-PEAP (common)
    TTLS = 'TTLS'         # EAP-TTLS
    TLS = 'TLS'           # EAP-TLS (requires client cert)


# TTLS Phase 2 methods
class TTLSPhase2:
    """TTLS Phase 2 authentication methods."""
    PWD = const(0)        # PWD
    MSCHAPV2 = const(1)   # MSCHAPv2 (default, but has security issues)
    MSCHAP = const(2)     # MSCHAP (has security issues)
    PAP = const(3)        # PAP
    CHAP = const(4)       # CHAP (has security issues)


class WiFiConfig:
    """WiFi configuration with secure credential storage."""

    CONFIG_FILE = '/config/wifi_config.json'
    # Default encryption key (device-specific MAC-based key should be used in production)
    ENCRYPTION_KEY = b'portalbox_secret'

    def __init__(self):
        """Initialize WiFi configuration."""
        self.ssid = None
        self.network_type = None
        self.bssid = None  # Optional: Restrict to specific AP

        # WPA2/3-PSK credentials
        self.psk_encrypted = None  # Encrypted PSK password
        self.psk_hash = None
        self.wpa3 = False  # Enforce WPA3 

        # WPA2 Enterprise fields
        self.eap_method = None
        self.username = None
        self.password_encrypted = None  # Encrypted enterprise password
        self.password_hash = None
        self.identity = None

        # Certification files
        self.ca_cert_file = None
        self.client_cert_file = None
        self.private_key_file = None
        self.private_key_password_hash = None

        # TTLS-specific
        self.ttls_phase2_method = TTLSPhase2.MSCHAPV2

        # TLS-specific
        self.disable_time_check = False

        # Connection settings
        self.timeout = 30  # seconds
        self.retry_attempts = 3
        self.retry_delay = 5  # seconds

        # Auto-connect
        self.auto_connect = True

    def set_wpa2_psk(self, ssid, password, bssid=None):
        """
        Configure WPA2-PSK network.
        
        Args:
            ssid: Network SSID
            password: WiFi password (will be hashed)
            bssid: Optional MAC address to restrict to specific AP
        """
        self.ssid = ssid
        self.network_type = NetworkType.WPA2_PSK
        self.psk_hash = self._hash_password(password)
        self.bssid = bssid
        self.wpa3 = False
        
        print(f"[CONFIG] WPA2-PSK configured for SSID: {ssid}")
    
    def set_wpa3_psk(self, ssid, password, bssid=None):
        """
        Configure WPA3-PSK network (enforces WPA3).
        
        Args:
            ssid: Network SSID
            password: WiFi password (will be hashed)
            bssid: Optional MAC address to restrict to specific AP
        """
        self.ssid = ssid
        self.network_type = NetworkType.WPA3_PSK
        self.psk_hash = self._hash_password(password)
        self.bssid = bssid
        self.wpa3 = True
        
        print(f"[CONFIG] WPA3-PSK configured for SSID: {ssid}")
    
    def set_eap_pwd(self, ssid, username, password):
        """
        Configure EAP-PWD (recommended - fast and secure).
        
        Args:
            ssid: Network SSID
            username: Network username
            password: Network password (will be hashed)
        """
        self.ssid = ssid
        self.network_type = NetworkType.WPA2_ENTERPRISE
        self.eap_method = EAPMethod.PWD
        self.username = username
        self.password_hash = self._hash_password(password)
        
        print(f"[CONFIG] EAP-PWD configured for SSID: {ssid}")
    
    def set_eap_peap(self, ssid, username, password, identity=None,
                     ca_cert_file='ca-cert.pem'):
        """
        Configure EAP-PEAP.
        
        Args:
            ssid: Network SSID
            username: Network username
            password: Network password (will be encrypted and hashed)
            identity: Anonymous identity (optional)
            ca_cert_file: Path to CA certificate file
        """
        self.ssid = ssid
        self.network_type = NetworkType.WPA2_ENTERPRISE
        self.eap_method = EAPMethod.PEAP
        self.username = username
        self.password_encrypted = self._encrypt_password(password)
        self.password_hash = self._hash_password(password)
        self.identity = identity or username
        self.ca_cert_file = ca_cert_file
        
        print(f"[CONFIG] EAP-PEAP configured for SSID: {ssid}")
    
    def set_eap_ttls(self, ssid, username, password, identity=None,
                     ca_cert_file='ca-cert.pem', phase2_method=TTLSPhase2.MSCHAPV2):
        """
        Configure EAP-TTLS.
        
        Args:
            ssid: Network SSID
            username: Network username
            password: Network password (will be hashed)
            identity: Anonymous identity (optional)
            ca_cert_file: Path to CA certificate file
            phase2_method: TTLS Phase 2 method (default: MSCHAPv2)
                          Note: MSCHAPv2 and CHAP have known security issues
        """
        self.ssid = ssid
        self.network_type = NetworkType.WPA2_ENTERPRISE
        self.eap_method = EAPMethod.TTLS
        self.username = username
        self.password_hash = self._hash_password(password)
        self.identity = identity or username
        self.ca_cert_file = ca_cert_file
        self.ttls_phase2_method = phase2_method
        
        # Warn about insecure methods
        if phase2_method in [TTLSPhase2.MSCHAPV2, TTLSPhase2.CHAP]:
            print("[CONFIG] WARNING: Selected Phase 2 method has known security issues")
        
        print(f"[CONFIG] EAP-TTLS configured for SSID: {ssid}")
    
    def set_eap_tls(self, ssid, client_cert_file, private_key_file,
                    private_key_password=None, disable_time_check=False):
        """
        Configure EAP-TLS (certificate-based authentication).
        
        EXPERIMENTAL: EAP-TLS is untested and experimental.
        
        Args:
            ssid: Network SSID
            client_cert_file: Path to client certificate file
            private_key_file: Path to private key file
            private_key_password: Private key password (optional, will be hashed)
            disable_time_check: Suppress certificate validity check (NOT RECOMMENDED)
        """
        self.ssid = ssid
        self.network_type = NetworkType.WPA2_ENTERPRISE
        self.eap_method = EAPMethod.TLS
        self.client_cert_file = client_cert_file
        self.private_key_file = private_key_file
        
        if private_key_password:
            self.private_key_password_hash = self._hash_password(private_key_password)
        
        self.disable_time_check = disable_time_check
        
        if disable_time_check:
            print("[CONFIG] WARNING: Certificate time check disabled - NOT RECOMMENDED")
        
        print(f"[CONFIG] EAP-TLS configured for SSID: {ssid} (EXPERIMENTAL)")

    def _hash_password(self, password):
        """
        Hash password using SHA256

        Args:
            password (string): Plaintext password
        """
        hasher = uhashlib.sha256()
        hasher.update(password.encode('utf-8'))
        return ubinascii.hexlify(hasher.digest()).decode()

    def _get_encryption_key(self):
        """
        Get encryption key (padded to 16 bytes for AES-128).
        
        Returns:
            bytes: 16-byte encryption key
        """
        key = self.ENCRYPTION_KEY[:16]
        if len(key) < 16:
            key = key + b'\x00' * (16 - len(key))
        return key

    def _encrypt_password(self, password):
        """
        Encrypt password using AES-128 CBC.
        
        Args:
            password (str): Plaintext password
            
        Returns:
            str: Hex-encoded encrypted password (IV + ciphertext)
        """
        if not CRYPTO_AVAILABLE:
            print("[CONFIG] WARNING: Crypto not available, storing as base64")
            # Fallback to base64 encoding (not secure, but functional)
            return ubinascii.b2a_base64(password.encode('utf-8')).decode().strip()
        
        try:
            # Generate IV (using simple method for MicroPython)
            import urandom
            iv = urandom.getrandbits(128).to_bytes(16, 'big')
        except:
            # Fallback IV if urandom not available
            iv = uhashlib.sha256(str(time.ticks_us()).encode()).digest()[:16]
        
        # Pad password to multiple of 16 bytes (PKCS7 padding)
        password_bytes = password.encode('utf-8')
        pad_len = 16 - (len(password_bytes) % 16)
        padded = password_bytes + bytes([pad_len] * pad_len)
        
        # Encrypt
        key = self._get_encryption_key()
        cipher = ucryptolib.aes(key, 2, iv)  # Mode 2 = CBC
        encrypted = cipher.encrypt(padded)
        
        # Return IV + encrypted data as hex
        return ubinascii.hexlify(iv + encrypted).decode()

    def _decrypt_password(self, encrypted_hex):
        """
        Decrypt password using AES-128 CBC or base64.
        
        Args:
            encrypted_hex (str): Hex-encoded encrypted password or base64
            
        Returns:
            str: Plaintext password or None on error
        """
        if not encrypted_hex:
            return None
        
        # First, try to detect if it's base64 (contains non-hex characters like = or +)
        is_base64 = any(c in encrypted_hex for c in ['+', '/', '=']) or not all(c in '0123456789abcdefABCDEF' for c in encrypted_hex)
        
        if is_base64:
            # Decode from base64 (fallback encoding)
            try:
                return ubinascii.a2b_base64(encrypted_hex).decode('utf-8')
            except Exception as e:
                print(f"[CONFIG] Base64 decode error: {e}")
                return None
        
        # Try AES decryption (hex-encoded)
        if not CRYPTO_AVAILABLE:
            print("[CONFIG] WARNING: Crypto not available, but data is hex-encoded")
            return None
        
        try:
            # Decode hex
            encrypted_data = ubinascii.unhexlify(encrypted_hex)
            
            # Extract IV and ciphertext
            iv = encrypted_data[:16]
            ciphertext = encrypted_data[16:]
            
            # Decrypt
            key = self._get_encryption_key()
            cipher = ucryptolib.aes(key, 2, iv)  # Mode 2 = CBC
            decrypted = cipher.decrypt(ciphertext)
            
            # Remove PKCS7 padding
            pad_len = decrypted[-1]
            password_bytes = decrypted[:-pad_len]
            
            return password_bytes.decode('utf-8')
        except Exception as e:
            print(f"[CONFIG] AES decryption error: {e}")
            return None

    def get_decrypted_password(self):
        """
        Get decrypted password for connection.
        
        Returns:
            str: Plaintext password or None if not available
        """
        if self.network_type in [NetworkType.WPA2_PSK, NetworkType.WPA3_PSK]:
            return self._decrypt_password(self.psk_encrypted)
        elif self.network_type == NetworkType.WPA2_ENTERPRISE:
            return self._decrypt_password(self.password_encrypted)
        return None

    def verify_password(self, password, password_type='main'):
        """
        Verify password against stored hash.
        
        Args:
            password: Plain text password to verify
            password_type: Type of password ('main', 'private_key')
            
        Returns:
            bool: True if password matches
        """
        if password_type == 'private_key':
            return self._hash_password(password) == self.private_key_password_hash

        # Main password verification
        if self.network_type in [NetworkType.WPA2_PSK, NetworkType.WPA3_PSK]:
            return self._hash_password(password) == self.psk_hash
        elif self.network_type == NetworkType.WPA2_ENTERPRISE:
            return self._hash_password(password) == self.password_hash
        return False

    def save(self):
        """Save configuration to file."""
        try:
            config_data = {
                'ssid': self.ssid,
                'network_type': self.network_type,
                'bssid': self.bssid,
                'wpa3': self.wpa3,
                'eap_method': self.eap_method,
                'username': self.username,
                'password_encrypted': self.password_encrypted,
                'password_hash': self.password_hash,
                'identity': self.identity,
                'psk_encrypted': self.psk_encrypted,
                'psk_hash': self.psk_hash,
                'ca_cert_file': self.ca_cert_file,
                'client_cert_file': self.client_cert_file,
                'private_key_file': self.private_key_file,
                'private_key_password_hash': self.private_key_password_hash,
                'ttls_phase2_method': self.ttls_phase2_method,
                'disable_time_check': self.disable_time_check,
                'timeout': self.timeout,
                'retry_attempts': self.retry_attempts,
                'retry_delay': self.retry_delay,
                'auto_connect': self.auto_connect
            }
            
            # Ensure config directory exists
            try:
                import os
                os.mkdir('/config')
            except OSError:
                pass  # Directory exists
            
            with open(self.CONFIG_FILE, 'w') as f:
                json.dump(config_data, f)
            
            print(f"[CONFIG] Saved to {self.CONFIG_FILE}")
            return True
            
        except Exception as e:
            print(f"[CONFIG] Error saving: {e}")
            return False

    def load(self):
        """Load configuration from file."""
        try:
            with open(self.CONFIG_FILE, 'r') as f:
                config_data = json.load(f)
            
            self.ssid = config_data.get('ssid')
            self.network_type = config_data.get('network_type')
            self.bssid = config_data.get('bssid')
            self.wpa3 = config_data.get('wpa3', False)
            self.eap_method = config_data.get('eap_method')
            self.username = config_data.get('username')
            self.password_encrypted = config_data.get('password_encrypted')
            self.password_hash = config_data.get('password_hash')
            self.identity = config_data.get('identity')
            self.psk_encrypted = config_data.get('psk_encrypted')
            self.psk_hash = config_data.get('psk_hash')
            self.ca_cert_file = config_data.get('ca_cert_file')
            self.client_cert_file = config_data.get('client_cert_file')
            self.private_key_file = config_data.get('private_key_file')
            self.private_key_password_hash = config_data.get('private_key_password_hash')
            self.ttls_phase2_method = config_data.get('ttls_phase2_method', TTLSPhase2.MSCHAPV2)
            self.disable_time_check = config_data.get('disable_time_check', False)
            self.timeout = config_data.get('timeout', 30)
            self.retry_attempts = config_data.get('retry_attempts', 3)
            self.retry_delay = config_data.get('retry_delay', 5)
            self.auto_connect = config_data.get('auto_connect', True)
            
            print(f"[CONFIG] Loaded from {self.CONFIG_FILE}")
            return True
            
        except Exception as e:
            print(f"[CONFIG] Error loading: {e}")
            return False

    def clear(self):
        """Clear stored configuration."""
        try:
            import os
            os.remove(self.CONFIG_FILE)
            print("[CONFIG] Configuration cleared")
            return True
        except Exception as e:
            print(f"[CONFIG] Error clearing: {e}")
            return False

class WifiManager:
    """
    Wifi Connection manager

    Handles WPA2 Enterprise and WPA2-PSK networks with automatic
    reconnection and status monitoring.
    """

    def __init__(self, config=None, verbose=True):
        """
        Initialize WiFi manager.

        Args:
            config: WiFiConfig instance (creates new if None)
            verbose: Enable verbose logging
        """
        self.config = config or WiFiConfig()
        self.verbose = verbose
        self.nic = None
        self.connected = False
        self.connection_time = None
        self.last_error = None
        
        print("[WiFi] Manager initialized")

    def _log(self, message):
        """Log message if verbose mode enabled."""
        if self.verbose:
            print(message)

    def _init_interface(self):
        """Initialize WiFi interface."""
        self._log("[WiFi] Initializing interface...")

        # Create interface
        self.nic = network.WLAN(network.WLAN.IF_STA)
        
        # Reset interface (needs full seconds for proper reset)
        self._log("[WiFi] Deactivating interface...")
        self.nic.active(False)
        time.sleep(1)
        
        self._log("[WiFi] Activating interface...")
        self.nic.active(True)
        time.sleep(1)

        # Disconnect if already connected
        if self.nic.isconnected():
            self._log("[WiFi] Disconnecting existing connection...")
            self.nic.disconnect()
            time.sleep(2)

        # Display MAC address
        mac = ubinascii.hexlify(self.nic.config('mac')).decode()
        self._log(f"[WiFi] MAC Address: {mac}")
        
        return True

    # def _set_time_from_ntp(self):
    #     """Set system time from NTP server."""
    #     try:
    #         import ntptime
    #         self._log("[WiFi] Syncing time with NTP...")
    #         ntptime.settime()
    #         self._log("[WiFi] Time synchronized")
    #     except Exception as e:
    #         self._log(f"[WiFi] NTP sync failed: {e}")

    def _set_time_from_build(self):
        """Set RTC time from build date (workaround)."""
        try:
            version_parts = sys.version.split(" on ")
            if len(version_parts) > 1:
                date_str = version_parts[1]
                year, month, day = date_str.split("-")
                rtc = machine.RTC()
                date_time = (int(year), int(month), int(day), 0, 12, 0, 0, 0)
                rtc.init(date_time)
                self._log(f"[WiFi] RTC set to: {year}-{month}-{day} 12:00:00")
        except Exception as e:
            self._log(f"[WiFi] Warning: Could not set RTC time: {e}")

    def _load_ca_cert(self):
        """
        Load CA certificate from file.
        
        Returns:
            str: Certificate content or None
        """
        try:
            with open(self.config.ca_cert_file, 'r') as f:
                cert_content = f.read()
            
            cert_count = cert_content.count("-----BEGIN CERTIFICATE-----")
            self._log(f"[WiFi] Loaded {cert_count} certificate(s) from {self.config.ca_cert_file}")
            
            return cert_content
            
        except Exception as e:
            self._log(f"[WiFi] Error loading certificate: {e}")
            self.last_error = f"Certificate load failed: {e}"
            return None

    def _connect_wpa2_enterprise(self, password, private_key_password=None):
        """
        Connect to WPA2 Enterprise network using configured EAP method.
        
        Args:
            password: Plain text password (for PWD/PEAP/TTLS)
            private_key_password: Plain text private key password (for TLS)
            
        Returns:
            bool: True if connection initiated successfully
        """

        # Set time for certificate validation
        self._set_time_from_build()
        
        # Route to appropriate EAP method
        if self.config.eap_method == EAPMethod.PWD:
            return self._connect_eap_pwd(password)
        
        elif self.config.eap_method == EAPMethod.PEAP:
            return self._connect_eap_peap(password)
        
        elif self.config.eap_method == EAPMethod.TTLS:
            return self._connect_eap_ttls(password)
        
        elif self.config.eap_method == EAPMethod.TLS:
            return self._connect_eap_tls(private_key_password)
        
        else:
            self._log(f"[WiFi] ERROR: Unknown EAP method: {self.config.eap_method}")
            self.last_error = "Unknown EAP method"
            return False

    def _connect_eap_pwd(self, password):
        """
        Connect using EAP-PWD (recommended).
        
        Args:
            password: Plain text password
            
        Returns:
            bool: True if connection initiated successfully
        """
        # Verify password
        if not self.config.verify_password(password):
            self._log("[WiFi] ERROR: Password verification failed")
            self.last_error = "Invalid password"
            return False
        
        self._log(f"[WiFi] Connecting to {self.config.ssid} (EAP-PWD)...")
        self._log(f"[WiFi] Username: {self.config.username}")
        
        try:
            connect_params = {
                'eap_method': network.WLAN.EAP_PWD,
                'username': self.config.username,
                'password': password
            }
            
            # Add optional BSSID
            if self.config.bssid:
                connect_params['bssid'] = self.config.bssid
                self._log(f"[WiFi] Restricting to BSSID: {self.config.bssid}")
            
            self.nic.connect(self.config.ssid, **connect_params)
            self._log("[WiFi] Connection initiated")
            return True
            
        except Exception as e:
            self._log(f"[WiFi] Connection error: {e}")
            self.last_error = str(e)
            return False

    def _connect_eap_peap(self, password):
        """
        Connect using EAP-PEAP.
        
        Args:
            password: Plain text password
            
        Returns:
            bool: True if connection initiated successfully
        """
        # Verify password
        if not self.config.verify_password(password):
            self._log("[WiFi] ERROR: Password verification failed")
            self.last_error = "Invalid password"
            return False
        
        # Load certificate
        cert_content = self._load_ca_cert()
        if not cert_content:
            return False
        
        self._log(f"[WiFi] Connecting to {self.config.ssid} (EAP-PEAP)...")
        self._log(f"[WiFi] Username: {self.config.username}")
        self._log(f"[WiFi] Identity: {self.config.identity}")
        
        try:
            # Build connection parameters
            connect_params = {
                'eap_method': network.WLAN.EAP_PEAP,
                'username': self.config.username,
                'password': password,
                'identity': self.config.identity,
                'ca_cert': cert_content
            }
            
            # Add optional BSSID
            if self.config.bssid:
                connect_params['bssid'] = self.config.bssid
            
            # Connect with SSID as positional argument
            self.nic.connect(self.config.ssid, **connect_params)
            
            self.nic.connect(**connect_params)
            self._log("[WiFi] Connection initiated")
            return True
            
        except Exception as e:
            self._log(f"[WiFi] Connection error: {e}")
            self.last_error = str(e)
            return False
    
    def _connect_eap_ttls(self, password):
        """
        Connect using EAP-TTLS.
        
        Args:
            password: Plain text password
            
        Returns:
            bool: True if connection initiated successfully
        """
        # Verify password
        if not self.config.verify_password(password):
            self._log("[WiFi] ERROR: Password verification failed")
            self.last_error = "Invalid password"
            return False
        
        # Load certificate
        cert_content = self._load_ca_cert()
        if not cert_content:
            return False
        
        self._log(f"[WiFi] Connecting to {self.config.ssid} (EAP-TTLS)...")
        self._log(f"[WiFi] Username: {self.config.username}")
        self._log(f"[WiFi] Identity: {self.config.identity}")
        self._log(f"[WiFi] Phase 2 Method: {self.config.ttls_phase2_method}")
        
        try:
            connect_params = {
                'eap_method': network.WLAN.EAP_TTLS,
                'username': self.config.username,
                'password': password,
                'identity': self.config.identity,
                'ca_cert': cert_content,
                'ttls_phase2_method': self.config.ttls_phase2_method
            }
            
            # Add optional BSSID
            if self.config.bssid:
                connect_params['bssid'] = self.config.bssid
            
            self.nic.connect(self.config.ssid, **connect_params)
            self._log("[WiFi] Connection initiated")
            return True
            
        except Exception as e:
            self._log(f"[WiFi] Connection error: {e}")
            self.last_error = str(e)
            return False
    
    def _connect_eap_tls(self, private_key_password=None):
        """
        Connect using EAP-TLS (certificate-based).
        
        EXPERIMENTAL: EAP-TLS is untested.
        
        Args:
            private_key_password: Plain text private key password (if encrypted)
            
        Returns:
            bool: True if connection initiated successfully
        """
        self._log("[WiFi] WARNING: EAP-TLS is EXPERIMENTAL and UNTESTED")
        
        # Verify private key password if set
        if self.config.private_key_password_hash and private_key_password:
            if not self.config.verify_password(private_key_password, 'private_key'):
                self._log("[WiFi] ERROR: Private key password verification failed")
                self.last_error = "Invalid private key password"
                return False
        
        # Load client certificate
        try:
            with open(self.config.client_cert_file, 'r') as f:
                client_cert = f.read()
            self._log(f"[WiFi] Loaded client certificate: {self.config.client_cert_file}")
        except Exception as e:
            self._log(f"[WiFi] Error loading client certificate: {e}")
            self.last_error = f"Certificate load failed: {e}"
            return False
        
        # Load private key
        try:
            with open(self.config.private_key_file, 'r') as f:
                private_key = f.read()
            self._log(f"[WiFi] Loaded private key: {self.config.private_key_file}")
        except Exception as e:
            self._log(f"[WiFi] Error loading private key: {e}")
            self.last_error = f"Private key load failed: {e}"
            return False
        
        self._log(f"[WiFi] Connecting to {self.config.ssid} (EAP-TLS)...")
        
        try:
            connect_params = {
                'eap_method': network.WLAN.EAP_TLS,
                'client_cert': client_cert,
                'private_key': private_key,
                'disable_time_check': self.config.disable_time_check
            }
            
            # Add private key password if provided
            if private_key_password:
                connect_params['private_key_password'] = private_key_password
            
            # Add optional BSSID
            if self.config.bssid:
                connect_params['bssid'] = self.config.bssid
            
            self.nic.connect(self.config.ssid, **connect_params)
            self._log("[WiFi] Connection initiated")
            return True
            
        except Exception as e:
            self._log(f"[WiFi] Connection error: {e}")
            self.last_error = str(e)
            return False
    
    def _connect_wpa2_psk(self, password):
        """
        Connect to WPA2/WPA3-PSK network.
        
        Args:
            password: Plain text password
            
        Returns:
            bool: True if connection initiated successfully
        """
        # Verify password
        if not self.config.verify_password(password):
            self._log("[WiFi] ERROR: Password verification failed")
            self.last_error = "Invalid password"
            return False
        
        # Determine network type string for logging
        net_type = "WPA3-PSK" if self.config.wpa3 else "WPA2-PSK"
        self._log(f"[WiFi] Connecting to {self.config.ssid} ({net_type})...")
        
        try:
            connect_params = {
                'key': password
            }
            
            # Add optional BSSID
            if self.config.bssid:
                connect_params['bssid'] = self.config.bssid
                self._log(f"[WiFi] Restricting to BSSID: {self.config.bssid}")
            
            # Enforce WPA3 if configured
            if self.config.wpa3:
                connect_params['wpa3'] = True
                self._log("[WiFi] Enforcing WPA3 authentication")
            
            self.nic.connect(self.config.ssid, **connect_params)
            self._log("[WiFi] Connection initiated")
            return True
            
        except Exception as e:
            self._log(f"[WiFi] Connection error: {e}")
            self.last_error = str(e)
            return False
    
    def _wait_for_connection(self):
        """
        Wait for connection to complete.
        
        Returns:
            bool: True if connected successfully
        """
        self._log("[WiFi] Waiting for connection...")
        
        start_time = time.time()
        last_status = None
        
        while not self.nic.isconnected():
            elapsed = time.time() - start_time
            status = self.nic.status()
            
            # Log status changes
            if status != last_status:
                status_name = self._get_status_name(status)
                self._log(f"[WiFi] [{elapsed:.1f}s] Status: {status_name}")
                last_status = status
            
            # Check for failure states
            if status == NetworkStatus.WRONG_PASSWORD:
                self.last_error = "Wrong password"
                self._log(f"[WiFi] FAILED: {self.last_error}")
                return False
            
            elif status == NetworkStatus.NO_AP_FOUND:
                self.last_error = "Access point not found"
                self._log(f"[WiFi] FAILED: {self.last_error}")
                return False
            
            elif status == NetworkStatus.CONNECT_FAIL:
                self.last_error = "Connection failed"
                self._log(f"[WiFi] FAILED: {self.last_error}")
                return False
            
            # Check timeout
            if elapsed > self.config.timeout:
                self.last_error = f"Timeout after {self.config.timeout}s"
                self._log(f"[WiFi] FAILED: {self.last_error}")
                return False
            
            time.sleep(1)
        
        # Success
        self.connected = True
        self.connection_time = time.time()
        
        ifconfig = self.nic.ifconfig()
        self._log("[WiFi] Connected successfully!")
        self._log(f"[WiFi] IP: {ifconfig[0]}")
        self._log(f"[WiFi] Gateway: {ifconfig[2]}")
        
        return True

    def connect(self, password, private_key_password=None, skip_init=False):
        """
        Connect to configured network.
        
        Args:
            password: Plain text password (for PSK/Enterprise)
            private_key_password: Plain text private key password (for EAP-TLS only)
            skip_init: Skip interface initialization (for retries)
            
        Returns:
            bool: True if connected successfully
        """
        # Validate configuration
        if not self.config.ssid:
            self._log("[WiFi] ERROR: No SSID configured")
            self.last_error = "No SSID configured"
            return False
        
        if self.config.network_type is None:
            self._log("[WiFi] ERROR: No network type configured")
            self.last_error = "No network type configured"
            return False
        
        # Initialize interface (only on first attempt)
        if not skip_init:
            if not self._init_interface():
                return False
        
        # Connect based on network type
        if self.config.network_type == NetworkType.WPA2_ENTERPRISE:
            if not self._connect_wpa2_enterprise(password, private_key_password):
                return False
        
        elif self.config.network_type in [NetworkType.WPA2_PSK, NetworkType.WPA3_PSK]:
            if not self._connect_wpa2_psk(password):
                return False
        
        else:
            self._log(f"[WiFi] ERROR: Unknown network type: {self.config.network_type}")
            self.last_error = "Unknown network type"
            return False
        
        # Wait for connection
        return self._wait_for_connection()
    
    def connect_with_retry(self, password, private_key_password=None):
        """
        Connect with automatic retry on failure.
        
        Args:
            password: Plain text password
            private_key_password: Plain text private key password (for EAP-TLS)
            
        Returns:
            bool: True if connected successfully
        """
        # Initialize interface once before all attempts
        if not self._init_interface():
            return False
        
        for attempt in range(self.config.retry_attempts):
            self._log(f"[WiFi] Attempt {attempt + 1}/{self.config.retry_attempts}")
            
            # Skip init on all attempts (already initialized above)
            if self.connect(password, private_key_password, skip_init=True):
                return True
            
            if attempt < self.config.retry_attempts - 1:
                self._log(f"[WiFi] Retrying in {self.config.retry_delay}s...")
                time.sleep(self.config.retry_delay)
        
        self._log("[WiFi] All retry attempts failed")
        return False
    
    def disconnect(self):
        """Disconnect from network."""
        if self.nic and self.nic.isconnected():
            self._log("[WiFi] Disconnecting...")
            self.nic.disconnect()
            self.connected = False
            self.connection_time = None
            self._log("[WiFi] Disconnected")
            return True
        return False
    
    def is_connected(self):
        """
        Check if connected to network.
        
        Returns:
            bool: True if connected
        """
        if self.nic:
            self.connected = self.nic.isconnected()
            return self.connected
        return False
    
    def get_status(self):
        """
        Get current connection status.
        
        Returns:
            dict: Status information
        """
        if not self.nic:
            return {
                'connected': False,
                'error': 'Interface not initialized'
            }
        
        status = {
            'connected': self.is_connected(),
            'ssid': self.config.ssid,
        }
        
        # Network type
        if self.config.network_type == NetworkType.WPA2_ENTERPRISE:
            status['network_type'] = f'WPA2-Enterprise ({self.config.eap_method})'
        elif self.config.network_type == NetworkType.WPA3_PSK:
            status['network_type'] = 'WPA3-PSK'
        elif self.config.network_type == NetworkType.WPA2_PSK:
            status['network_type'] = 'WPA2-PSK'
        
        # Connection details if connected
        if self.connected:
            ifconfig = self.nic.ifconfig()
            status['ip'] = ifconfig[0]
            status['subnet'] = ifconfig[1]
            status['gateway'] = ifconfig[2]
            status['dns'] = ifconfig[3]
            
            if self.connection_time:
                status['uptime'] = time.time() - self.connection_time
        
        # Last error if any
        if self.last_error:
            status['last_error'] = self.last_error
        
        return status
    
    def _get_status_name(self, status_code):
        """Get human-readable status name."""
        status_names = {
            NetworkStatus.IDLE: 'IDLE',
            NetworkStatus.CONNECTING: 'CONNECTING',
            NetworkStatus.WRONG_PASSWORD: 'WRONG_PASSWORD',
            NetworkStatus.NO_AP_FOUND: 'NO_AP_FOUND',
            NetworkStatus.CONNECT_FAIL: 'CONNECT_FAIL',
            NetworkStatus.GOT_IP: 'GOT_IP'
        }
        return status_names.get(status_code, f'UNKNOWN({status_code})')
    
    def scan(self):
        """
        Scan for available networks.
        
        Returns:
            list: List of (ssid, bssid, channel, RSSI, security, hidden) tuples
        """
        if not self.nic:
            self._init_interface()
        
        self._log("[WiFi] Scanning for networks...")
        networks = self.nic.scan()
        
        self._log(f"[WiFi] Found {len(networks)} networks")
        
        return networks
    
    def print_scan_results(self):
        """Scan and print network list."""
        networks = self.scan()
        
        print("\nAvailable Networks:")
        print("-" * 60)
        print(f"{'SSID':<32} {'RSSI':<6} {'Channel':<8} {'Security'}")
        print("-" * 60)
        
        for ssid, bssid, channel, rssi, security, hidden in networks:
            ssid_str = ssid.decode() if isinstance(ssid, bytes) else ssid
            security_str = f"0x{security:02x}"
            print(f"{ssid_str:<32} {rssi:<6} {channel:<8} {security_str}")
        
        print("-" * 60)