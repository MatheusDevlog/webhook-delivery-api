import json
import socket
import ssl
from dataclasses import dataclass
from http.client import HTTPException, HTTPResponse, HTTPSConnection
from io import BufferedReader, RawIOBase
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from time import monotonic
from urllib.parse import quote, urlsplit

from django.core.exceptions import ValidationError

from .validators import MAX_EVENT_PAYLOAD_BYTES, validate_webhook_url


PUBLIC_IPV6_NETWORK = ip_network('2000::/3')
CONNECT_TIMEOUT_SECONDS = 3
IO_TIMEOUT_SECONDS = 5
MAX_REQUEST_BYTES = MAX_EVENT_PAYLOAD_BYTES + 1024
MAX_RESPONSE_BYTES = 16 * 1024


@dataclass(frozen=True)
class ResolvedDestination:
    hostname: str
    path: str
    addresses: tuple[IPv4Address | IPv6Address, ...]


@dataclass(frozen=True)
class DeliveryResult:
    http_status: int | None
    error_code: str


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
    validate_webhook_url(url)
    parsed = urlsplit(url)
    try:
        hostname = parsed.hostname.rstrip('.').encode('idna').decode('ascii')
    except UnicodeError:
        raise ValidationError(
            'O domínio do destino não pode ser convertido para DNS.',
            code='invalid_destination_hostname',
        ) from None

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


class _BoundedResponseReader(RawIOBase):
    def __init__(self, sock):
        super().__init__()
        self.sock = sock
        self.deadline = monotonic() + IO_TIMEOUT_SECONDS
        self.remaining = MAX_RESPONSE_BYTES

    def readable(self):
        return True

    def makefile(self, mode):
        return BufferedReader(self)

    def readinto(self, buffer):
        if self.remaining <= 0:
            raise HTTPException('O limite de leitura da resposta foi excedido.')
        self.sock.settimeout(_remaining_timeout(self.deadline))
        received = self.sock.recv_into(memoryview(buffer)[:self.remaining])
        self.remaining -= received
        return received


class _BoundedHTTPResponse(HTTPResponse):
    def __init__(self, sock, *args, **kwargs):
        super().__init__(_BoundedResponseReader(sock), *args, **kwargs)


class _PinnedHTTPSConnection(HTTPSConnection):
    response_class = _BoundedHTTPResponse

    def __init__(self, destination):
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
            if tls_socket is not None:
                tls_socket.close()
            else:
                raw_socket.close()
            raise
        self.sock = tls_socket


def post_json(url, data):
    try:
        if not isinstance(data, dict):
            raise TypeError
        body = json.dumps(
            data, ensure_ascii=False, allow_nan=False, separators=(',', ':'),
        ).encode('utf-8')
    except (TypeError, ValueError, RecursionError):
        raise ValidationError(
            'O corpo do envio deve ser um objeto JSON válido.',
            code='invalid_delivery_body',
        ) from None
    if len(body) > MAX_REQUEST_BYTES:
        raise ValidationError(
            'O corpo do envio não pode exceder 17 KiB em UTF-8.',
            code='delivery_body_too_large',
        )

    connection = None
    try:
        destination = resolve_destination(url)
        connection = _PinnedHTTPSConnection(destination)
        connection.request('POST', destination.path, body=body, headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'WebhookDeliveryService/0.1',
            'Connection': 'close',
        })
        with connection.getresponse() as response:
            if not 200 <= response.status <= 599:
                raise HTTPException('Código HTTP final não suportado.')
            error_code = '' if response.status < 300 else 'http_error'
            return DeliveryResult(response.status, error_code)
    except DeliveryError as error:
        return DeliveryResult(None, error.code)
    except TimeoutError:
        return DeliveryResult(None, 'timeout')
    except (OSError, HTTPException):
        return DeliveryResult(None, 'network_error')
    finally:
        if connection is not None:
            connection.close()
