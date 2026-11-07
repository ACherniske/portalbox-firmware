from .wifi import WifiManager
from .models import WiFiConfig, NetworkType, EAPMethod, TTLSPhase2, NetworkStatus
# net.ota is not imported here: it pulls in the HTTPS and staging code, and
# that RAM is needed for the WiFi login at boot. Use `from net import ota`.

__version__ = '0.0.1'
