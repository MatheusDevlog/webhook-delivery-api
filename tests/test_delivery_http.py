import socket
import ssl
from ipaddress import ip_address
from unittest.mock import Mock, call

import pytest
from django.core.exceptions import ValidationError

from webhooks import delivery


PUBLIC_IPV4 = '93.184.216.34'
PUBLIC_IPV6 = '2606:4700:4700::1111'


def destination(*addresses, hostname='receiver.example'):
    return delivery.ResolvedDestination(
        hostname=hostname, path='/webhook',
        addresses=tuple(ip_address(value) for value in addresses),
    )


@pytest.fixture
def network(monkeypatch):
    context = ssl.create_default_context()
    raw_socket = Mock()
    tls_socket = Mock()
    context.wrap_socket = Mock(return_value=tls_socket)
    socket_factory = Mock(return_value=raw_socket)
    resolver = Mock(side_effect=AssertionError('A conexão não pode consultar DNS novamente.'))
    monkeypatch.setattr(delivery.ssl, 'create_default_context', lambda: context)
    monkeypatch.setattr(delivery.socket, 'socket', socket_factory)
    monkeypatch.setattr(delivery.socket, 'getaddrinfo', resolver)
    monkeypatch.setattr(delivery, 'monotonic', Mock(return_value=100))
    return context, raw_socket, tls_socket, socket_factory, resolver


@pytest.mark.parametrize(('address', 'hostname'), [
    (PUBLIC_IPV4, 'receiver.example'), (PUBLIC_IPV6, 'receiver.example'),
    (PUBLIC_IPV4, PUBLIC_IPV4), (PUBLIC_IPV6, PUBLIC_IPV6),
])
def test_connects_to_validated_ip_and_verifies_original_host(network, address, hostname):
    context, raw, tls, factory, resolver = network
    connection = delivery._PinnedHTTPSConnection(destination(address, hostname=hostname))

    connection.connect()

    family = socket.AF_INET if ':' not in address else socket.AF_INET6
    target = (address, 443) if family == socket.AF_INET else (address, 443, 0, 0)
    factory.assert_called_once_with(family, socket.SOCK_STREAM)
    raw.connect.assert_called_once_with(target)
    context.wrap_socket.assert_called_once_with(raw, server_hostname=hostname)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert connection.sock is tls
    tls.settimeout.assert_called_once_with(delivery.IO_TIMEOUT_SECONDS)
    resolver.assert_not_called()


def test_prefers_ipv4_without_discarding_the_original_hostname(network):
    context, raw, _, _, _ = network
    connection = delivery._PinnedHTTPSConnection(destination(PUBLIC_IPV6, PUBLIC_IPV4))

    connection.connect()

    raw.connect.assert_called_once_with((PUBLIC_IPV4, 443))
    context.wrap_socket.assert_called_once_with(raw, server_hostname='receiver.example')


@pytest.mark.parametrize('failure', [
    ssl.SSLCertVerificationError('certificado rejeitado'),
    ssl.SSLError('falha na negociação TLS'), TimeoutError('prazo TLS excedido'),
])
def test_closes_raw_socket_when_tls_fails(network, failure):
    context, raw, _, _, _ = network
    context.wrap_socket.side_effect = failure
    connection = delivery._PinnedHTTPSConnection(destination(PUBLIC_IPV4))

    with pytest.raises(type(failure)):
        connection.connect()

    raw.close.assert_called_once_with()
    assert connection.sock is None


@pytest.mark.parametrize('failure', [
    TimeoutError('prazo TCP excedido'), ConnectionRefusedError('conexão recusada'),
    OSError('falha de rede'),
])
def test_closes_raw_socket_without_tls_or_retry_when_tcp_fails(network, failure):
    context, raw, _, factory, resolver = network
    raw.connect.side_effect = failure
    connection = delivery._PinnedHTTPSConnection(destination(PUBLIC_IPV4, '8.8.8.8'))

    with pytest.raises(type(failure)):
        connection.connect()

    raw.close.assert_called_once_with()
    raw.connect.assert_called_once_with((PUBLIC_IPV4, 443))
    assert factory.call_count == 1
    context.wrap_socket.assert_not_called()
    resolver.assert_not_called()
    assert connection.sock is None


def test_tcp_and_tls_share_the_connection_timeout_budget(network, monkeypatch):
    _, raw, _, _, _ = network
    monkeypatch.setattr(delivery, 'monotonic', Mock(side_effect=[100, 100, 101]))
    connection = delivery._PinnedHTTPSConnection(destination(PUBLIC_IPV4))

    connection.connect()

    assert raw.settimeout.call_args_list == [call(3), call(2)]


def test_stops_before_tls_if_tcp_consumed_the_time_budget(network, monkeypatch):
    context, raw, _, _, _ = network
    monkeypatch.setattr(delivery, 'monotonic', Mock(side_effect=[100, 100, 103]))
    connection = delivery._PinnedHTTPSConnection(destination(PUBLIC_IPV4))

    with pytest.raises(TimeoutError):
        connection.connect()

    context.wrap_socket.assert_not_called()
    raw.close.assert_called_once_with()


def test_close_releases_the_tls_connection(network):
    _, _, tls, _, _ = network
    connection = delivery._PinnedHTTPSConnection(destination(PUBLIC_IPV4))
    connection.connect()

    connection.close()

    tls.close.assert_called_once_with()
    assert connection.sock is None


def test_closes_tls_socket_if_configuration_fails_after_handshake(network):
    _, raw, tls, _, _ = network
    tls.settimeout.side_effect = OSError('falha ao configurar conexão')
    connection = delivery._PinnedHTTPSConnection(destination(PUBLIC_IPV4))

    with pytest.raises(OSError):
        connection.connect()

    tls.close.assert_called_once_with()
    raw.close.assert_not_called()
    assert connection.sock is None


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '::ffff:127.0.0.1'])
def test_rejects_private_ip_even_if_resolved_destination_was_constructed_directly(
    network, address,
):
    _, _, _, factory, _ = network

    with pytest.raises(ValidationError):
        delivery._PinnedHTTPSConnection(destination(address))

    factory.assert_not_called()
