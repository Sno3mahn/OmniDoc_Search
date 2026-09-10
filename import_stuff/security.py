import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def is_safe_url(url: str) -> bool:
    """Blocks SSRF via a user-supplied or agent-extracted URL targeting internal
    infrastructure (cloud metadata endpoints, localhost, RFC1918 ranges, etc.)
    by resolving the hostname and rejecting private/loopback/link-local/reserved
    addresses.

    Caveat: this is a resolve-then-check, not a resolve-once-and-pin-the-fetch.
    A DNS answer that changes between this check and the actual HTTP request
    (DNS rebinding) would bypass it. Good enough for this project's threat
    model; a rebinding-proof fix would need every fetch path to connect to the
    IP it validated rather than re-resolving the hostname.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        return False

    try:
        addr_infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False

    for *_rest, sockaddr in addr_infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False

    return True
