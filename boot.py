# boot.py
# Runs on boot to initialize WiFi connection

import sys
import time
import machine

print("\n" + "="*60)
print("PortalBox Firmware - Boot Sequence")
print("="*60)
print(f"MicroPython: {sys.version}")
print(f"Platform: {sys.platform}")
print("="*60)

# Import WiFi manager
try:
    from core.wifi import WiFiConfig, WifiManager
    print("[BOOT] WiFi module loaded")
except ImportError as e:
    print(f"[BOOT] ERROR: Failed to import WiFi module: {e}")
    sys.exit(1)

# Load WiFi configuration
print("[BOOT] Loading WiFi configuration...")
config = WiFiConfig()

if not config.load():
    print("[BOOT] ERROR: Failed to load WiFi configuration")
    print("[BOOT] Please configure WiFi settings in /config/wifi_config.json")
    sys.exit(1)

print(f"[BOOT] Configuration loaded: {config.ssid}")

# Get decrypted password from config
print("[BOOT] Decrypting password...")
password = config.get_decrypted_password()

if not password:
    print("[BOOT] ERROR: Failed to decrypt password from configuration")
    sys.exit(1)

print("[BOOT] Password successfully decrypted")

# Create WiFi manager and connect
print("[BOOT] Initializing WiFi manager...")
wifi = WifiManager(config=config, verbose=True)

# Attempt connection with retry
print("[BOOT] Connecting to network...")
if wifi.connect_with_retry(password):
    print("\n" + "="*60)
    print("WIFI CONNECTION SUCCESSFUL")
    print("="*60)
    
    # Display connection status
    status = wifi.get_status()
    print(f"SSID: {status['ssid']}")
    print(f"Network Type: {status['network_type']}")
    print(f"IP Address: {status['ip']}")
    print(f"Gateway: {status['gateway']}")
    print(f"DNS: {status['dns']}")
    print("="*60)
    
    # Test HTTP connection
    print("\n[BOOT] Testing HTTP connectivity...")
    try:
        import urequests
        response = urequests.get('http://httpbin.org/get', timeout=5)
        print(f"[BOOT] HTTP test successful (Status: {response.status_code})")
        response.close()
    except Exception as e:
        print(f"[BOOT] HTTP test failed: {e}")
    
    print("\n[BOOT] Boot sequence complete. Ready for operation.")
    
else:
    print("\n" + "="*60)
    print("WIFI CONNECTION FAILED")
    print("="*60)
    
    status = wifi.get_status()
    if 'last_error' in status:
        print(f"Error: {status['last_error']}")
    
    print("\n[BOOT] Boot sequence failed. System halted.")
    print("="*60)
    sys.exit(1)
