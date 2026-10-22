"""Network info: local (LAN) IP and public IP."""
import socket
import time

import requests

PUBLIC_IP_TTL_SEC = 300

# (timestamp, ip) of the last successful public IP lookup.
_public_ip_cache = (0.0, "")


def lan_ip():
    """This machine's local network IP (for friends on the same LAN / Wi-Fi)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no real traffic, only used to pick the interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def public_ip():
    """Public IP (for friends outside the network, with the port forwarded on the router).

    Successful lookups are cached for PUBLIC_IP_TTL_SEC since the dashboard polls status.
    """
    global _public_ip_cache
    fetched_at, ip = _public_ip_cache
    if ip and time.time() - fetched_at < PUBLIC_IP_TTL_SEC:
        return ip
    try:
        ip = requests.get("https://api.ipify.org", timeout=10).text.strip()
    except requests.RequestException:
        return "unavailable"
    _public_ip_cache = (time.time(), ip)
    return ip
