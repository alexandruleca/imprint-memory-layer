"""Network and browser boundaries for local Imprint services."""
from ipaddress import ip_address
from urllib.parse import urlsplit


def loopback_address(address: str) -> bool:
    """Check a literal peer/bind IP without trusting DNS or forwarded headers."""
    try:
        ip = ip_address(address)
        mapped = getattr(ip, 'ipv4_mapped', None)
        return (mapped or ip).is_loopback
    except ValueError:
        return False


def local_origin(origin: str) -> bool:
    try:
        url = urlsplit(origin)
        # Accessing .port also rejects malformed port strings.
        _ = url.port
        return (url.scheme == 'http' and url.hostname in {'127.0.0.1', 'localhost', '::1'}
                and not url.username and not url.password and url.path in {'', '/'}
                and not url.query and not url.fragment)
    except ValueError:
        return False


def local_host(host: str) -> bool:
    """Reject rebinding hosts even when a browser omits Origin on a GET."""
    if not host or any(character in host for character in '/?#@\\'):
        return False
    return local_origin('http://' + host)
