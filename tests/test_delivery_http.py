import json
import socket
import ssl
from io import BytesIO
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


@pytest.fixture
def http_receiver(network, settings):
    _, _, tls, _, resolver = network
    settings.WEBHOOK_ALLOWED_URLS = ['https://receiver.example/webhook']
    resolver.side_effect = None
    resolver.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (PUBLIC_IPV4, 443)),
    ]

    def reply(data):
        stream = BytesIO(data)
        tls.recv_into.side_effect = stream.readinto
        return stream

    reply(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n')
    return reply


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


@pytest.mark.parametrize('status', [200, 201, 204, 299, 300, 400, 500, 599])
def test_returns_http_result_using_the_real_parser_with_simulated_network(http_receiver, status):
    http_receiver(f'HTTP/1.1 {status} Result\r\nContent-Length: 0\r\n\r\n'.encode())

    result = delivery.post_json('https://receiver.example/webhook', {'pedido_id': 123})

    assert result == delivery.DeliveryResult(status, '' if status < 300 else 'http_error')


def test_posts_utf8_json_with_fixed_headers_and_without_another_dns_lookup(
    http_receiver, network, monkeypatch,
):
    context, raw, tls, factory, resolver = network
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9999')
    data = {'pedido_id': 123, 'mensagem': 'Pagamento ✅'}

    result = delivery.post_json('https://receiver.example/webhook', data)

    wire = b''.join(item.args[0] for item in tls.sendall.call_args_list)
    headers, body = wire.split(b'\r\n\r\n', 1)
    assert headers.startswith(b'POST /webhook HTTP/1.1\r\n')
    assert b'\r\nHost: receiver.example\r\n' in headers
    assert b'\r\nContent-Type: application/json\r\n' in headers
    assert f'Content-Length: {len(body)}'.encode() in headers
    assert b'Authorization:' not in headers
    assert b'Cookie:' not in headers
    assert json.loads(body) == data
    assert 'Pagamento ✅'.encode() in body
    raw.connect.assert_called_once_with((PUBLIC_IPV4, 443))
    context.wrap_socket.assert_called_once_with(raw, server_hostname='receiver.example')
    assert resolver.call_count == factory.call_count == 1
    assert result == delivery.DeliveryResult(200, '')
    tls.close.assert_called_once_with()


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_reports_redirect_without_following_its_location(http_receiver, network, status):
    _, raw, _, factory, resolver = network
    http_receiver(
        (f'HTTP/1.1 {status} Redirect\r\nLocation: https://127.0.0.1/private\r\n'
         'Content-Length: 0\r\n\r\n').encode()
    )

    result = delivery.post_json('https://receiver.example/webhook', {})

    assert result == delivery.DeliveryResult(status, 'http_error')
    assert resolver.call_count == factory.call_count == 1
    raw.connect.assert_called_once_with((PUBLIC_IPV4, 443))


@pytest.mark.parametrize('data', [
    None, [], {'value': float('nan')}, {'value': object()}, {'value': '\ud800'},
])
def test_rejects_invalid_json_body_before_dns_or_connection(http_receiver, network, data):
    _, _, _, factory, resolver = network

    with pytest.raises(ValidationError) as error:
        delivery.post_json('https://receiver.example/webhook', data)

    assert error.value.code == 'invalid_delivery_body'
    resolver.assert_not_called()
    factory.assert_not_called()


def test_accepts_body_at_the_17_kib_boundary(http_receiver, network):
    _, _, tls, _, _ = network
    data = {'value': 'x' * (delivery.MAX_REQUEST_BYTES - 12)}

    result = delivery.post_json('https://receiver.example/webhook', data)

    wire = b''.join(item.args[0] for item in tls.sendall.call_args_list)
    assert len(wire.split(b'\r\n\r\n', 1)[1]) == delivery.MAX_REQUEST_BYTES
    assert result.http_status == 200


def test_counts_utf8_bytes_when_rejecting_an_oversized_body(http_receiver, network):
    _, _, _, factory, resolver = network
    data = {'value': 'á' * (delivery.MAX_REQUEST_BYTES // 2)}

    with pytest.raises(ValidationError) as error:
        delivery.post_json('https://receiver.example/webhook', data)

    assert error.value.code == 'delivery_body_too_large'
    resolver.assert_not_called()
    factory.assert_not_called()


@pytest.mark.parametrize(('stage', 'failure', 'code'), [
    ('tcp', TimeoutError('internal timeout details'), 'timeout'),
    ('tcp', ConnectionRefusedError('internal connection details'), 'network_error'),
    ('tls', ssl.SSLCertVerificationError('internal certificate details'), 'network_error'),
    ('write', TimeoutError('internal timeout details'), 'timeout'),
    ('write', BrokenPipeError('internal write details'), 'network_error'),
    ('read', TimeoutError('internal timeout details'), 'timeout'),
    ('read', OSError('internal read details'), 'network_error'),
])
def test_categorizes_failures_without_retry_or_internal_details(
    http_receiver, network, stage, failure, code,
):
    context, raw, tls, factory, resolver = network
    operations = {
        'tcp': raw.connect, 'tls': context.wrap_socket,
        'write': tls.sendall, 'read': tls.recv_into,
    }
    operations[stage].side_effect = failure

    result = delivery.post_json('https://receiver.example/webhook', {})

    assert result == delivery.DeliveryResult(None, code)
    assert 'internal' not in str(result)
    assert resolver.call_count == factory.call_count == 1
    assert raw.close.called or tls.close.called


def test_returns_dns_failure_without_opening_a_connection(http_receiver, network):
    _, _, _, factory, resolver = network
    resolver.side_effect = socket.gaierror('internal DNS details')

    result = delivery.post_json('https://receiver.example/webhook', {})

    assert result == delivery.DeliveryResult(None, 'network_error')
    factory.assert_not_called()


def test_blocks_post_after_url_is_removed_from_allowlist(http_receiver, network, settings):
    _, _, _, factory, resolver = network
    settings.WEBHOOK_ALLOWED_URLS = []

    with pytest.raises(ValidationError) as error:
        delivery.post_json('https://receiver.example/webhook', {})

    assert error.value.code == 'unapproved_destination'
    resolver.assert_not_called()
    factory.assert_not_called()


def test_blocks_post_when_allowed_domain_resolves_to_private_ip(http_receiver, network):
    _, _, _, factory, resolver = network
    resolver.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('10.0.0.1', 443)),
    ]

    with pytest.raises(ValidationError) as error:
        delivery.post_json('https://receiver.example/webhook', {})

    assert error.value.code == 'non_public_destination'
    factory.assert_not_called()


@pytest.mark.parametrize('host', ['\u200d.example', 'á' * 63 + '.example'])
def test_rejects_hostname_that_cannot_be_encoded_for_dns(
    http_receiver, network, settings, host,
):
    _, _, _, factory, resolver = network
    url = f'https://{host}/webhook'
    settings.WEBHOOK_ALLOWED_URLS = [url]

    with pytest.raises(ValidationError) as error:
        delivery.post_json(url, {})

    assert error.value.code == 'invalid_destination_hostname'
    resolver.assert_not_called()
    factory.assert_not_called()


def test_caps_response_bytes_when_headers_are_too_large(http_receiver, network):
    _, _, tls, _, _ = network
    stream = http_receiver(
        b'HTTP/1.1 200 OK\r\nX-Large: ' + b'x' * delivery.MAX_RESPONSE_BYTES + b'\r\n\r\n',
    )

    result = delivery.post_json('https://receiver.example/webhook', {})

    assert result == delivery.DeliveryResult(None, 'network_error')
    assert stream.tell() == delivery.MAX_RESPONSE_BYTES
    tls.close.assert_called_once_with()


def test_does_not_consume_the_full_response_body(http_receiver):
    stream = http_receiver(
        b'HTTP/1.1 200 OK\r\nContent-Length: 10000000\r\n\r\n'
        + b'x' * (2 * delivery.MAX_RESPONSE_BYTES),
    )

    result = delivery.post_json('https://receiver.example/webhook', {})

    assert result == delivery.DeliveryResult(200, '')
    assert stream.tell() <= delivery.MAX_RESPONSE_BYTES
    assert stream.tell() < len(stream.getvalue())


def test_response_deadline_applies_even_when_bytes_keep_arriving(
    http_receiver, network, monkeypatch,
):
    _, _, tls, _, _ = network
    clock = [100]
    monkeypatch.setattr(delivery, 'monotonic', lambda: clock[0])

    def drip(buffer):
        buffer[0] = ord('H')
        clock[0] += 2
        return 1

    tls.recv_into.side_effect = drip

    result = delivery.post_json('https://receiver.example/webhook', {})

    assert result == delivery.DeliveryResult(None, 'timeout')
    assert tls.recv_into.call_count == 3
    tls.close.assert_called_once_with()


@pytest.mark.parametrize('reply', [
    b'', b'not-http\r\n\r\n', b'HTTP/1.1 101 Switching Protocols\r\n\r\n',
    b'HTTP/1.1 600 Unsupported\r\n\r\n',
])
def test_rejects_invalid_or_unsupported_http_responses(http_receiver, reply):
    http_receiver(reply)

    assert delivery.post_json('https://receiver.example/webhook', {}) == (
        delivery.DeliveryResult(None, 'network_error')
    )


def test_handles_100_continue_before_the_final_response(http_receiver):
    http_receiver(
        b'HTTP/1.1 100 Continue\r\n\r\n'
        b'HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n',
    )

    assert delivery.post_json('https://receiver.example/webhook', {}) == (
        delivery.DeliveryResult(204, '')
    )
