import socket
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from urllib.parse import quote, urlsplit

from django.core.exceptions import ValidationError

from .validators import validate_webhook_url


PUBLIC_IPV6_NETWORK = ip_network('2000::/3')


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
