# net/models.py
import ujson as json
from micropython import const
from . import crypto

class NetworkType:
    """Network authentication types."""
    WPA2_PSK = const(0)
    WPA3_PSK = const(1)
    WPA2_ENTERPRISE = const(2)

class EAPMethod:
    """EAP authentication methods for WPA Enterprise."""
    PWD = 'PWD'
    PEAP = 'PEAP'
    TTLS = 'TTLS'
    TLS = 'TLS'

class TTLSPhase2:
    """TTLS Phase 2 authentication methods."""
    PWD = const(0)
    MSCHAPV2 = const(1)
    MSCHAP = const(2)
    PAP = const(3)
    CHAP = const(4)

class NetworkStatus:
    """Standard network connection states."""
    IDLE = const(0)
    CONNECTING = const(1)
    WRONG_PASSWORD = const(-3)
    NO_AP_FOUND = const(-2)
    CONNECT_FAIL = const(-1)
    GOT_IP = const(3)

class WiFiConfig:
    """Configuration model for WiFi credentials and settings."""
    CONFIG_FILE = '/config/wifi_config.json'

    def __init__(self):
        # General Settings
        self.ssid = None
        self.network_type = NetworkType.WPA2_PSK
        self.bssid = None
        self.wpa3 = False
        
        # PSK Credentials
        self.psk_encrypted = None
        self.psk_hash = None
        
        # Enterprise Credentials
        self.eap_method = None
        self.username = None
        self.password_encrypted = None
        self.password_hash = None
        self.identity = None
        
        # Certificates and Keys
        self.ca_cert_file = None
        self.client_cert_file = None
        self.private_key_file = None
        self.private_key_password_hash = None
        
        # Protocol Settings
        self.ttls_phase2_method = TTLSPhase2.MSCHAPV2
        self.disable_time_check = False
        
        # Connection Management
        self.timeout = 30
        self.retry_attempts = 3
        self.retry_delay = 5

    def set_wpa2_psk(self, ssid, password, bssid=None):
        """Configure for standard WPA2/WPA3 PSK."""
        self.ssid = ssid
        self.network_type = NetworkType.WPA2_PSK
        self.bssid = bssid
        self.psk_hash = crypto.hash_password(password)
        self.psk_encrypted = crypto.encrypt_password(password)

    def set_wpa2_enterprise(self, ssid, username, password, eap_method=EAPMethod.PEAP):
        """Configure for WPA2 Enterprise."""
        self.ssid = ssid
        self.network_type = NetworkType.WPA2_ENTERPRISE
        self.username = username
        self.eap_method = eap_method
        self.password_hash = crypto.hash_password(password)
        self.password_encrypted = crypto.encrypt_password(password)

    def verify_password(self, password, password_type='main'):
        """Verify a plaintext password against stored hashes."""
        if password_type == 'private_key':
            target_hash = self.private_key_password_hash
        else:
            target_hash = self.psk_hash if self.network_type != NetworkType.WPA2_ENTERPRISE else self.password_hash
        
        if not target_hash:
            return False
        return crypto.hash_password(password) == target_hash

    def get_decrypted_password(self):
        """Decrypts and returns the main network password."""
        encrypted = self.psk_encrypted if self.network_type != NetworkType.WPA2_ENTERPRISE else self.password_encrypted
        return crypto.decrypt_password(encrypted)

    def save(self):
            """Save configuration to JSON file."""
            import os
            try:
                # Filter __dict__ to only include basic types (strings, ints, bools, None)
                # This prevents trying to save modules or methods
                clean_data = {k: v for k, v in self.__dict__.items() 
                             if isinstance(v, (str, int, float, bool, list, dict)) or v is None}

                filename = self.CONFIG_FILE
                if not filename.startswith('/'):
                    filename = '/' + filename

                with open(filename, 'w') as f:
                    json.dump(clean_data, f) # Save the filtered dict
                    f.flush()
                    try: os.sync()
                    except: pass
                
                print(f"[Config] Successfully wrote {filename}")
                return True
            except Exception as e:
                print(f"[Config] Save failed: {e}")
                return False

    def load(self):
        """Load configuration from JSON file."""
        try:
            filename = self.CONFIG_FILE
            with open(filename, 'r') as f:
                data = json.load(f)
                # Update only the attributes that exist in the JSON
                for key, value in data.items():
                    setattr(self, key, value)
            return True
        except Exception as e:
            print(f"[Config] Load failed: {e}") # This will now likely show a JSON error
            return False
