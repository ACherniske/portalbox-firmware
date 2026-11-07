"""Minimal MicroPython helper to create a device WiFi config.

Copy this to the ESP32 and run; call one of the helpers to write
`/config/wifi_config.json` using the existing `net.models.WiFiConfig`.
"""
import json
from net.models import WiFiConfig

def configure_wifi_psk(ssid, psk, bssid=None, wpa3=False):
    c = WiFiConfig()
    if wpa3:
        c.set_wpa3_psk(ssid, psk, bssid=bssid)
    else:
        c.set_wpa2_psk(ssid, psk, bssid=bssid)
    c.save()

def configure_wifi_enterprise(ssid, username, password, identity=None, eap='PEAP', ca='ca-cert.pem'):
    if identity is None:
        identity = username

    c = WiFiConfig()
    if eap == 'PEAP':
        c.set_eap_peap(ssid, username, password, identity=identity, ca_cert_file=ca)
    else:
        c.set_wpa2_enterprise(ssid, username, password)
    c.save()
