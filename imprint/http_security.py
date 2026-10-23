"""Origin checks for the local dashboard's filesystem and command API."""
from urllib.parse import urlsplit


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
