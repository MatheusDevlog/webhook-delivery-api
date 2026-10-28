import socket
import ssl
from dataclasses import dataclass
from http.client import HTTPSConnection
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from time import monotonic
from urllib.parse import quote, urlsplit

from django.core.exceptions import ValidationError

from .validators import validate_webhook_url


PUBLIC_IPV6_NETWORK = ip_network('2000::/3')
CONNECT_TIMEOUT_SECONDS = 3
IO_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class ResolvedDestination:
    hostname: str
    path: str
    addresses: tuple[IPv4Address | IPv6Address, ...]


class DeliveryError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('Não foi possível concluir a comunicação com o destino.')


def _validate_public_ip(value):
    try:
        address = ip_address(value)
    except ValueError:
        raise ValidationError(
            'O destino não retornou um endereço IP válido.',
            code='invalid_destination_address',
        ) from None

    allowed = address.is_global and not address.is_multicast and not address.is_reserved
    if isinstance(address, IPv6Address):
        allowed = allowed and (
            address in PUBLIC_IPV6_NETWORK
            and address.scope_id is None
            and address.ipv4_mapped is None
            and address.sixtofour is None
            and address.teredo is None
        )
    if not allowed:
        raise ValidationError(
            'O destino deve resolver somente para endereços IP públicos permitidos.',
            code='non_public_destination',
        )
    return address


def resolve_destination(url):
    """Validate every resolved address; no connection or database write is made.

    DNS uses the system resolver, without an application-level total deadline.
    The future HTTPS connection must use a returned IP, not resolve the host again.
    """
    validate_webhook_url(url)
    parsed = urlsplit(url)
    hostname = parsed.hostname.rstrip('.').encode('idna').decode('ascii')

    try:
        ip_address(hostname)
    except ValueError:
        try:
            results = socket.getaddrinfo(
                hostname + '.', 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
            )
        except OSError:
            raise DeliveryError('network_error') from None
        if not results:
            raise DeliveryError('network_error')
        addresses = tuple(dict.fromkeys(
            _validate_public_ip(result[4][0]) for result in results
        ))
    else:
        addresses = (_validate_public_ip(hostname),)

    return ResolvedDestination(
        hostname=hostname,
        path=quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~"),
        addresses=addresses,
    )


def _remaining_timeout(deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError('O tempo limite da conexão foi excedido.')
    return remaining


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, destination):
        # Prefer IPv4 when available; never retry another IP automatically.
        address = next(
            (ip for ip in destination.addresses if isinstance(ip, IPv4Address)),
            destination.addresses[0],
        )
        self.address = _validate_public_ip(address)
        self.tls_context = ssl.create_default_context()
        self.tls_context.set_alpn_protocols(['http/1.1'])
        super().__init__(
            destination.hostname, 443,
            timeout=IO_TIMEOUT_SECONDS, context=self.tls_context,
        )

    def connect(self):
        deadline = monotonic() + CONNECT_TIMEOUT_SECONDS
        family = socket.AF_INET if self.address.version == 4 else socket.AF_INET6
        target = (str(self.address), 443)
        if family == socket.AF_INET6:
            target = (*target, 0, 0)
        raw_socket = socket.socket(family, socket.SOCK_STREAM)
        tls_socket = None
        try:
            raw_socket.settimeout(_remaining_timeout(deadline))
            raw_socket.connect(target)
            raw_socket.settimeout(_remaining_timeout(deadline))
            tls_socket = self.tls_context.wrap_socket(
                raw_socket, server_hostname=self.host,
            )
            tls_socket.settimeout(IO_TIMEOUT_SECONDS)
        except BaseException:
            # Cleanup also applies to interruptions; the original error is raised.
            if tls_socket is not None:
                tls_socket.close()
            else:
                raw_socket.close()
            raise
        self.sock = tls_socket
