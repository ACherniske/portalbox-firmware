# net/models.py
import network
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

    PWD = "PWD"
    PEAP = "PEAP"
    TTLS = "TTLS"
    TLS = "TLS"


class TTLSPhase2:
    """TTLS Phase 2 authentication methods."""

    PWD = const(0)
    MSCHAPV2 = const(1)
    MSCHAP = const(2)
    PAP = const(3)
    CHAP = const(4)


class NetworkStatus:
    """Connection states, using the codes the ESP32's wlan.status() returns.
    The numbers are fallbacks for a build that lacks a STAT_* constant."""

    IDLE = getattr(network, "STAT_IDLE", 1000)
    CONNECTING = getattr(network, "STAT_CONNECTING", 1001)
    GOT_IP = getattr(network, "STAT_GOT_IP", 1010)
    BEACON_TIMEOUT = getattr(network, "STAT_BEACON_TIMEOUT", 200)
    NO_AP_FOUND = getattr(network, "STAT_NO_AP_FOUND", 201)
    WRONG_PASSWORD = getattr(network, "STAT_WRONG_PASSWORD", 202)
    ASSOC_FAIL = getattr(network, "STAT_ASSOC_FAIL", 203)
    HANDSHAKE_TIMEOUT = getattr(network, "STAT_HANDSHAKE_TIMEOUT", 204)
    NO_AP_FOUND_W_COMPATIBLE_SECURITY = getattr(
        network, "STAT_NO_AP_FOUND_W_COMPATIBLE_SECURITY", 210
    )
    NO_AP_FOUND_IN_AUTHMODE_THRESHOLD = getattr(
        network, "STAT_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD", 211
    )
    NO_AP_FOUND_IN_RSSI_THRESHOLD = getattr(
        network, "STAT_NO_AP_FOUND_IN_RSSI_THRESHOLD", 212
    )
    # The manager's own "attempt failed" state (timeout, connect() raised).
    CONNECT_FAIL = ASSOC_FAIL


class WiFiConfig:
    """Configuration model for WiFi credentials and settings."""

    CONFIG_FILE = "/config/wifi_config.json"

    def __init__(self):
        self.ssid = None
        self.network_type = NetworkType.WPA2_PSK
        self.bssid = None
        self.wpa3 = False

        self.psk_encrypted = None
        self.psk_hash = None

        self.eap_method = None
        self.username = None
        self.password_encrypted = None
        self.password_hash = None
        self.identity = None

        self.ca_cert_file = None
        self.client_cert_file = None
        self.private_key_file = None
        self.private_key_password_hash = None

        self.ttls_phase2_method = TTLSPhase2.MSCHAPV2
        self.disable_time_check = False

        self.timeout = 30
        self.retry_attempts = 3
        self.retry_delay = 5
        self.auto_connect = True

    def set_wpa2_psk(self, ssid, password, bssid=None):
        self.ssid = ssid
        self.network_type = NetworkType.WPA2_PSK
        self.bssid = bssid
        self.wpa3 = False
        self.eap_method = None
        self.password_hash = None
        self.password_encrypted = None
        self.psk_hash = crypto.hash_password(password)
        self.psk_encrypted = crypto.encrypt_password(password)

    def set_wpa3_psk(self, ssid, password, bssid=None):
        self.ssid = ssid
        self.network_type = NetworkType.WPA3_PSK
        self.bssid = bssid
        self.wpa3 = True
        self.eap_method = None
        self.password_hash = crypto.hash_password(password)
        self.password_encrypted = crypto.encrypt_password(password)
        self.psk_hash = self.password_hash
        self.psk_encrypted = self.password_encrypted

    def set_wpa2_enterprise(self, ssid, username, password, eap_method=EAPMethod.PEAP):
        self.ssid = ssid
        self.network_type = NetworkType.WPA2_ENTERPRISE
        self.username = username
        self.eap_method = eap_method
        self.password_hash = crypto.hash_password(password)
        self.password_encrypted = crypto.encrypt_password(password)

    def set_eap_pwd(self, ssid, username, password):
        self.set_wpa2_enterprise(ssid, username, password, eap_method=EAPMethod.PWD)

    def set_eap_peap(
        self, ssid, username, password, identity=None, ca_cert_file="ca-cert.pem"
    ):
        self.set_wpa2_enterprise(ssid, username, password, eap_method=EAPMethod.PEAP)
        self.identity = identity or username
        self.ca_cert_file = ca_cert_file

    def set_eap_ttls(
        self,
        ssid,
        username,
        password,
        identity=None,
        ca_cert_file="ca-cert.pem",
        phase2_method=TTLSPhase2.MSCHAPV2,
    ):
        self.set_wpa2_enterprise(ssid, username, password, eap_method=EAPMethod.TTLS)
        self.identity = identity or username
        self.ca_cert_file = ca_cert_file
        self.ttls_phase2_method = phase2_method

    def set_eap_tls(
        self,
        ssid,
        client_cert_file,
        private_key_file,
        private_key_password=None,
        disable_time_check=False,
    ):
        self.ssid = ssid
        self.network_type = NetworkType.WPA2_ENTERPRISE
        self.eap_method = EAPMethod.TLS
        self.client_cert_file = client_cert_file
        self.private_key_file = private_key_file
        if private_key_password:
            self.private_key_password_hash = crypto.hash_password(private_key_password)
        self.disable_time_check = disable_time_check

    def verify_password(self, password, password_type="main"):
        if password_type == "private_key":
            target_hash = self.private_key_password_hash
        else:
            target_hash = (
                self.psk_hash
                if self.network_type != NetworkType.WPA2_ENTERPRISE
                else self.password_hash
            )

        if not target_hash:
            return False
        return crypto.hash_password(password) == target_hash

    def get_decrypted_password(self):
        encrypted = (
            self.psk_encrypted
            if self.network_type != NetworkType.WPA2_ENTERPRISE
            else self.password_encrypted
        )
        return crypto.decrypt_password(encrypted)

    def save(self):
        try:
            clean_data = {
                key: value
                for key, value in self.__dict__.items()
                if isinstance(value, (str, int, float, bool, list, dict))
                or value is None
            }

            filename = self.CONFIG_FILE
            if not filename.startswith("/"):
                filename = "/" + filename

            with open(filename, "w") as f:
                json.dump(clean_data, f)
                f.flush()
                try:
                    import os

                    os.sync()
                except Exception:
                    pass

            print(f"[Config] Successfully wrote {filename}")
            return True
        except Exception as e:
            print(f"[Config] Save failed: {e}")
            return False

    def load(self):
        try:
            filename = self.CONFIG_FILE
            with open(filename, "r") as f:
                data = json.load(f)
                for key, value in data.items():
                    setattr(self, key, value)
            return True
        except Exception as e:
            print(f"[Config] Load failed: {e}")
            return False

    def clear(self):
        try:
            import os

            os.remove(self.CONFIG_FILE)
            print("[Config] Configuration cleared")
            return True
        except Exception as e:
            print(f"[Config] Error clearing: {e}")
            return False
