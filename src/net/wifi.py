import network
import time

from .models import WiFiConfig, NetworkType, NetworkStatus, EAPMethod
from . import clock

# nic.connect() hands ESP-IDF a pointer into each cert/key str without a copy.
# ESP-IDF reads it again on every EAP handshake (including auto-reconnects),
# so the strings must stay alive at module level, not in a local.
_held = []


def _hold(*values):
    _held[:] = values


def _release():
    _held.clear()


# A failure code fails the wait only after this many polls in a row: the
# driver keeps retrying and can recover from a brief NO_AP_FOUND or auth fail.
FAIL_POLLS = 3

# wlan.status() codes that mean the attempt has failed, and what to tell the user.
_FAILURE_MESSAGES = {
    NetworkStatus.BEACON_TIMEOUT: "Lost the access point (beacon timeout)",
    NetworkStatus.NO_AP_FOUND: "Access point not found",
    NetworkStatus.WRONG_PASSWORD: "Authentication failed (wrong password or rejected login)",
    NetworkStatus.ASSOC_FAIL: "Association with the access point failed",
    NetworkStatus.HANDSHAKE_TIMEOUT: "Handshake timed out (often a wrong password)",
    NetworkStatus.NO_AP_FOUND_W_COMPATIBLE_SECURITY: "No access point with compatible security found",
    NetworkStatus.NO_AP_FOUND_IN_AUTHMODE_THRESHOLD: "No access point within the auth mode threshold found",
    NetworkStatus.NO_AP_FOUND_IN_RSSI_THRESHOLD: "No access point with a strong enough signal found",
}

_STATUS_NAMES = {
    NetworkStatus.IDLE: 'IDLE',
    NetworkStatus.CONNECTING: 'CONNECTING',
    NetworkStatus.GOT_IP: 'GOT_IP',
    NetworkStatus.BEACON_TIMEOUT: 'BEACON_TIMEOUT',
    NetworkStatus.NO_AP_FOUND: 'NO_AP_FOUND',
    NetworkStatus.WRONG_PASSWORD: 'WRONG_PASSWORD',
    NetworkStatus.CONNECT_FAIL: 'CONNECT_FAIL',   # same code as ASSOC_FAIL
    NetworkStatus.HANDSHAKE_TIMEOUT: 'HANDSHAKE_TIMEOUT',
    NetworkStatus.NO_AP_FOUND_W_COMPATIBLE_SECURITY: 'NO_AP_FOUND_W_COMPATIBLE_SECURITY',
    NetworkStatus.NO_AP_FOUND_IN_AUTHMODE_THRESHOLD: 'NO_AP_FOUND_IN_AUTHMODE_THRESHOLD',
    NetworkStatus.NO_AP_FOUND_IN_RSSI_THRESHOLD: 'NO_AP_FOUND_IN_RSSI_THRESHOLD',
}


class WifiManager:
    """
    WiFi connection manager.

    Handles WPA2/WPA3 PSK and WPA2 Enterprise connections using the shared
    configuration and crypto helpers in the net package.
    """

    def __init__(self, config=None, verbose=True):
        self.config = config or WiFiConfig()
        self.verbose = verbose
        self.nic = None
        self.last_error = None
        self.connected = False
        self.connection_time = None
        self.status = NetworkStatus.IDLE
        # wlan.status() just before nic.connect(). The radio keeps the last
        # disconnect reason until a new one arrives, so a code equal to this
        # may be left over from the previous attempt.
        self._pre_status = None

        print("[WiFi] Manager initialized")

    def _log(self, message):
        if self.verbose:
            print(message)

    def _get_sta_interface(self):
        if hasattr(network, "STA_IF"):
            return network.WLAN(network.STA_IF)

        if hasattr(network.WLAN, "IF_STA"):
            return network.WLAN(network.WLAN.IF_STA)

        return network.WLAN(0)

    def _init_interface(self):
        self._log("[WiFi] Initializing interface...")
        self.nic = self._get_sta_interface()
        self._log("[WiFi] Deactivating interface...")
        self.nic.active(False)
        time.sleep(1)
        self._log("[WiFi] Activating interface...")
        self.nic.active(True)
        time.sleep(1)
        self.status = NetworkStatus.IDLE
        return True

    def _sync_time(self):
        try:
            import ntptime

            ntptime.settime()
            self._log("[WiFi] Time synchronized with NTP")
            clock.save_now()
        except Exception as e:
            self._log(f"[WiFi] NTP sync failed: {e}")

    def _load_ca_cert(self):
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
        if self.config.eap_method == EAPMethod.PWD:
            return self._connect_eap_pwd(password)
        if self.config.eap_method == EAPMethod.PEAP:
            return self._connect_eap_peap(password)
        if self.config.eap_method == EAPMethod.TTLS:
            return self._connect_eap_ttls(password)
        if self.config.eap_method == EAPMethod.TLS:
            return self._connect_eap_tls(private_key_password)

        self._log(f"[WiFi] ERROR: Unknown EAP method: {self.config.eap_method}")
        self.last_error = "Unknown EAP method"
        return False

    def _connect_eap_pwd(self, password):
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
                'password': password,
            }

            if self.config.bssid:
                connect_params['bssid'] = self.config.bssid
                self._log(f"[WiFi] Restricting to BSSID: {self.config.bssid}")

            self.nic.connect(self.config.ssid, **connect_params)
            self._log("[WiFi] Connection initiated")
            self.status = NetworkStatus.CONNECTING
            return True
        except Exception as e:
            self._log(f"[WiFi] Connection error: {e}")
            self.last_error = str(e)
            self.status = NetworkStatus.CONNECT_FAIL
            return False

    def _connect_eap_peap(self, password):
        if not self.config.verify_password(password):
            self._log("[WiFi] ERROR: Password verification failed")
            self.last_error = "Invalid password"
            return False

        cert_content = self._load_ca_cert()
        if not cert_content:
            return False

        self._log(f"[WiFi] Connecting to {self.config.ssid} (EAP-PEAP)...")
        self._log(f"[WiFi] Username: {self.config.username}")
        self._log(f"[WiFi] Identity: {self.config.identity}")

        try:
            connect_params = {
                'eap_method': network.WLAN.EAP_PEAP,
                'username': self.config.username,
                'password': password,
                'identity': self.config.identity,
                'ca_cert': cert_content,
            }

            if self.config.bssid:
                connect_params['bssid'] = self.config.bssid

            _hold(cert_content)
            self.nic.connect(self.config.ssid, **connect_params)
            self._log("[WiFi] Connection initiated")
            self.status = NetworkStatus.CONNECTING
            return True
        except Exception as e:
            self._stop_and_release()
            self._log(f"[WiFi] Connection error: {e}")
            self.last_error = str(e)
            self.status = NetworkStatus.CONNECT_FAIL
            return False

    def _connect_eap_ttls(self, password):
        if not self.config.verify_password(password):
            self._log("[WiFi] ERROR: Password verification failed")
            self.last_error = "Invalid password"
            return False

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
                'ttls_phase2_method': self.config.ttls_phase2_method,
            }

            if self.config.bssid:
                connect_params['bssid'] = self.config.bssid

            _hold(cert_content)
            self.nic.connect(self.config.ssid, **connect_params)
            self._log("[WiFi] Connection initiated")
            self.status = NetworkStatus.CONNECTING
            return True
        except Exception as e:
            self._stop_and_release()
            self._log(f"[WiFi] Connection error: {e}")
            self.last_error = str(e)
            self.status = NetworkStatus.CONNECT_FAIL
            return False

    def _connect_eap_tls(self, private_key_password=None):
        self._log("[WiFi] WARNING: EAP-TLS is EXPERIMENTAL and UNTESTED")

        if self.config.private_key_password_hash and private_key_password:
            if not self.config.verify_password(private_key_password, 'private_key'):
                self._log("[WiFi] ERROR: Private key password verification failed")
                self.last_error = "Invalid private key password"
                return False

        try:
            with open(self.config.client_cert_file, 'r') as f:
                client_cert = f.read()
            self._log(f"[WiFi] Loaded client certificate: {self.config.client_cert_file}")
        except Exception as e:
            self._log(f"[WiFi] Error loading client certificate: {e}")
            self.last_error = f"Certificate load failed: {e}"
            return False

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
                'disable_time_check': self.config.disable_time_check,
            }

            if private_key_password:
                connect_params['private_key_password'] = private_key_password

            if self.config.bssid:
                connect_params['bssid'] = self.config.bssid

            _hold(client_cert, private_key, private_key_password)
            self.nic.connect(self.config.ssid, **connect_params)
            self._log("[WiFi] Connection initiated")
            self.status = NetworkStatus.CONNECTING
            return True
        except Exception as e:
            self._stop_and_release()
            self._log(f"[WiFi] Connection error: {e}")
            self.last_error = str(e)
            self.status = NetworkStatus.CONNECT_FAIL
            return False

    def _connect_wpa2_psk(self, password):
        if not self.config.verify_password(password):
            self._log("[WiFi] ERROR: Password verification failed")
            self.last_error = "Invalid password"
            return False

        net_type = "WPA3-PSK" if self.config.wpa3 else "WPA2-PSK"
        self._log(f"[WiFi] Connecting to {self.config.ssid} ({net_type})...")

        try:
            connect_params = {'key': password}

            if self.config.bssid:
                connect_params['bssid'] = self.config.bssid
                self._log(f"[WiFi] Restricting to BSSID: {self.config.bssid}")

            if self.config.wpa3:
                connect_params['wpa3'] = True
                self._log("[WiFi] Enforcing WPA3 authentication")

            self.nic.connect(self.config.ssid, **connect_params)
            self._log("[WiFi] Connection initiated")
            self.status = NetworkStatus.CONNECTING
            return True
        except Exception as e:
            self._log(f"[WiFi] Connection error: {e}")
            self.last_error = str(e)
            self.status = NetworkStatus.CONNECT_FAIL
            return False

    def _wait_for_connection(self):
        self._log("[WiFi] Waiting for connection...")
        self.status = NetworkStatus.CONNECTING

        start_time = time.time()
        last_status = None
        seen_connecting = False
        fail_count = 0

        while not self.nic.isconnected():
            elapsed = time.time() - start_time
            status = self.nic.status()

            if status != last_status:
                self._log(f"[WiFi] [{elapsed:.1f}s] Status: {self._get_status_name(status)}")
                last_status = status
                fail_count = 0

            if status == NetworkStatus.CONNECTING:
                seen_connecting = True

            # A failure code equal to the pre-connect one counts only once the
            # radio has shown CONNECTING since; before that it may be stale.
            message = _FAILURE_MESSAGES.get(status)
            if message and (seen_connecting or status != self._pre_status):
                fail_count += 1
                if fail_count >= FAIL_POLLS:
                    self.last_error = message
                    self.status = status
                    self._log(f"[WiFi] FAILED: {self.last_error}")
                    return False

            if elapsed > self.config.timeout:
                if message:
                    # Still the best explanation, even if it looked stale.
                    self.last_error = message
                    self.status = status
                elif self.config.network_type == NetworkType.WPA2_ENTERPRISE:
                    self.last_error = (
                        f"Timed out during login (EAP) after {self.config.timeout}s: "
                        "check the board clock, CA certificate, and credentials")
                else:
                    self.last_error = f"Timeout after {self.config.timeout}s"
                if not message:
                    self.status = NetworkStatus.CONNECT_FAIL
                self._log(f"[WiFi] FAILED: {self.last_error}")
                return False

            time.sleep(1)

        self.connected = True
        self.status = NetworkStatus.GOT_IP
        self.connection_time = time.time()

        ifconfig = self.nic.ifconfig()
        self._log("[WiFi] Connected successfully!")
        self._log(f"[WiFi] IP: {ifconfig[0]}")
        self._log(f"[WiFi] Gateway: {ifconfig[2]}")
        # Network is up; attempt to synchronize system time via NTP
        try:
            self._sync_time()
        except Exception:
            # _sync_time logs its own errors; don't let NTP failures break connection
            pass
        clock.start_autosave()
        return True

    def connect(self, password, private_key_password=None, skip_init=False):
        if not self.config.ssid:
            self._log("[WiFi] ERROR: No SSID configured")
            self.last_error = "No SSID configured"
            return False

        if self.config.network_type is None:
            self._log("[WiFi] ERROR: No network type configured")
            self.last_error = "No network type configured"
            return False

        self.last_error = None
        self.connected = False

        if not skip_init:
            if not self._init_interface():
                return False

        # TLS checks certificate dates against the RTC, which reads 2000-01-01
        # after power-on. Move it up to the floor before any login attempt.
        if clock.apply_floor():
            self._log(f"[WiFi] Clock was behind; set to floor {time.localtime()[:3]}")

        self._pre_status = self.nic.status()

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

        return self._wait_for_connection()

    def connect_with_retry(self, password, private_key_password=None):
        for attempt in range(self.config.retry_attempts):
            self._log(f"[WiFi] Attempt {attempt + 1}/{self.config.retry_attempts}")
            
            if not self._init_interface():
                self.last_error = "Interface reset failed"

            elif self.connect(password, private_key_password, skip_init=True):
                return True

            if attempt < self.config.retry_attempts - 1:
                self._log(f"[WiFi] Retrying in {self.config.retry_delay}s...")
                time.sleep(self.config.retry_delay)

        self._log("[WiFi] All retry attempts failed")
        return False

    def _stop_and_release(self):
        # The driver may still be retrying a login and reading the held certs,
        # so stop it before freeing them, connected or not.
        if self.nic:
            try:
                self.nic.disconnect()
            except Exception:
                pass   # raises when the driver is already stopped
        _release()

    def disconnect(self):
        was_connected = self.nic is not None and self.nic.isconnected()
        if was_connected:
            self._log("[WiFi] Disconnecting...")
        self._stop_and_release()
        if was_connected:
            self.connected = False
            self.connection_time = None
            self.status = NetworkStatus.IDLE
            self._log("[WiFi] Disconnected")
            return True
        return False

    def is_connected(self):
        if self.nic:
            self.connected = self.nic.isconnected()
            return self.connected
        return False

    def get_status(self):
        if not self.nic:
            return {
                'connected': False,
                'error': 'Interface not initialized',
            }

        status = {
            'connected': self.is_connected(),
            'ssid': self.config.ssid,
            'status': self._get_status_name(self.status),
        }

        if self.config.network_type == NetworkType.WPA2_ENTERPRISE:
            status['network_type'] = f'WPA2-Enterprise ({self.config.eap_method})'
        elif self.config.network_type == NetworkType.WPA3_PSK:
            status['network_type'] = 'WPA3-PSK'
        elif self.config.network_type == NetworkType.WPA2_PSK:
            status['network_type'] = 'WPA2-PSK'

        if self.connected:
            ifconfig = self.nic.ifconfig()
            status['ip'] = ifconfig[0]
            status['subnet'] = ifconfig[1]
            status['gateway'] = ifconfig[2]
            status['dns'] = ifconfig[3]

            if self.connection_time:
                status['uptime'] = time.time() - self.connection_time

        if self.last_error:
            status['last_error'] = self.last_error

        return status

    def _get_status_name(self, status_code):
        return _STATUS_NAMES.get(status_code, f'UNKNOWN({status_code})')

    def scan(self):
        if not self.nic:
            self._init_interface()

        self._log("[WiFi] Scanning for networks...")
        networks = self.nic.scan()
        self._log(f"[WiFi] Found {len(networks)} networks")
        return networks

    def print_scan_results(self):
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
