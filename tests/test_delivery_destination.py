import socket
from ipaddress import ip_address
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError

from webhooks import delivery


ALLOWED_URL = 'https://receiver.example/webhook'
PUBLIC_IPV4 = '93.184.216.34'
PUBLIC_IPV6 = '2606:4700:4700::1111'


def dns_answer(value):
    family = socket.AF_INET if ip_address(value).version == 4 else socket.AF_INET6
    sockaddr = (value, 443) if family == socket.AF_INET else (value, 443, 0, 0)
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr


@pytest.fixture
def resolver(monkeypatch, settings):
    settings.WEBHOOK_ALLOWED_URLS = [ALLOWED_URL]
    resolver = Mock(return_value=[dns_answer(PUBLIC_IPV4)])
    monkeypatch.setattr(delivery.socket, 'getaddrinfo', resolver)
    return resolver


def test_resolves_public_ipv4_ipv6_and_removes_duplicate_answers(resolver):
    resolver.return_value = [dns_answer(ip) for ip in [PUBLIC_IPV4, PUBLIC_IPV6, PUBLIC_IPV4]]

    destination = delivery.resolve_destination(ALLOWED_URL)

    assert destination.hostname == 'receiver.example'
    assert destination.path == '/webhook'
    assert destination.addresses == (ip_address(PUBLIC_IPV4), ip_address(PUBLIC_IPV6))
    resolver.assert_called_once_with(
        'receiver.example.', 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )


@pytest.mark.parametrize('address', [
    '127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.0.1', '169.254.169.254',
    '100.64.0.1', '0.0.0.0', '192.0.2.1', '224.0.0.1', '240.0.0.1',
    '::', '::1', 'fc00::1', 'fe80::1', 'ff02::1',
    '::ffff:127.0.0.1', '::ffff:8.8.8.8', '64:ff9b::7f00:1',
    '2002:0808:0808::1', '2001:0000:4136:e378:8000:63bf:3fff:fdd2',
    '2606:4700:4700::1111%eth0',
])
def test_rejects_non_public_multicast_and_transition_addresses(resolver, address):
    resolver.return_value = [dns_answer(address)]

    with pytest.raises(ValidationError) as error:
        delivery.resolve_destination(ALLOWED_URL)

    assert error.value.code == 'non_public_destination'


@pytest.mark.parametrize('addresses', [
    [PUBLIC_IPV4, '10.0.0.1'], ['10.0.0.1', PUBLIC_IPV4],
])
def test_rejects_entire_destination_if_any_answer_is_private(resolver, addresses):
    resolver.return_value = [dns_answer(ip) for ip in addresses]

    with pytest.raises(ValidationError) as error:
        delivery.resolve_destination(ALLOWED_URL)

    assert error.value.code == 'non_public_destination'


@pytest.mark.parametrize('url', [
    'http://receiver.example/webhook',
    'https://user:password@receiver.example/webhook',
    'https://localhost/webhook',
])
def test_checks_url_safety_before_dns_even_if_allowlisted(resolver, settings, url):
    settings.WEBHOOK_ALLOWED_URLS = [url]

    with pytest.raises(ValidationError):
        delivery.resolve_destination(url)

    resolver.assert_not_called()


def test_rechecks_allowlist_instead_of_trusting_previous_approval(resolver, settings):
    delivery.resolve_destination(ALLOWED_URL)
    settings.WEBHOOK_ALLOWED_URLS = []

    with pytest.raises(ValidationError) as error:
        delivery.resolve_destination(ALLOWED_URL)

    assert error.value.code == 'unapproved_destination'
    assert resolver.call_count == 1


@pytest.mark.parametrize('address', [PUBLIC_IPV4, PUBLIC_IPV6])
def test_accepts_allowlisted_public_ip_literal_without_dns(resolver, settings, address):
    host = f'[{address}]' if ':' in address else address
    url = f'https://{host}/webhook'
    settings.WEBHOOK_ALLOWED_URLS = [url]

    destination = delivery.resolve_destination(url)

    assert destination.hostname == address
    assert destination.addresses == (ip_address(address),)
    resolver.assert_not_called()


@pytest.mark.parametrize(('path', 'expected'), [
    ('', '/'), ('/aviso/café', '/aviso/caf%C3%A9'),
    ('/a%2Fb', '/a%2Fb'), ('/aviso:tipo;versao=1', '/aviso:tipo;versao=1'),
])
def test_prepares_request_path_without_losing_escaped_characters(
    resolver, settings, path, expected,
):
    url = f'https://receiver.example{path}'
    settings.WEBHOOK_ALLOWED_URLS = [url]

    assert delivery.resolve_destination(url).path == expected


def test_uses_ascii_hostname_for_dns_and_future_tls(resolver, settings):
    url = 'https://münich.example/webhook'
    settings.WEBHOOK_ALLOWED_URLS = [url]

    destination = delivery.resolve_destination(url)

    assert destination.hostname == 'xn--mnich-kva.example'
    resolver.assert_called_once_with(
        'xn--mnich-kva.example.', 443,
        type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )


@pytest.mark.parametrize('failure', [
    socket.gaierror('internal resolver details'), OSError('internal network details'),
])
def test_reports_dns_failure_without_exposing_resolver_details(resolver, failure):
    resolver.side_effect = failure

    with pytest.raises(delivery.DeliveryError) as error:
        delivery.resolve_destination(ALLOWED_URL)

    assert error.value.code == 'network_error'
    assert 'internal' not in str(error.value)


def test_rejects_empty_dns_answer(resolver):
    resolver.return_value = []

    with pytest.raises(delivery.DeliveryError) as error:
        delivery.resolve_destination(ALLOWED_URL)

    assert error.value.code == 'network_error'


def test_rejects_malformed_resolved_ip(resolver):
    resolver.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('not-an-ip', 443)),
    ]

    with pytest.raises(ValidationError) as error:
        delivery.resolve_destination(ALLOWED_URL)

    assert error.value.code == 'invalid_destination_address'


def test_checks_dns_again_instead_of_caching_public_approval(resolver):
    resolver.side_effect = [[dns_answer(PUBLIC_IPV4)], [dns_answer('10.0.0.1')]]
    delivery.resolve_destination(ALLOWED_URL)

    with pytest.raises(ValidationError) as error:
        delivery.resolve_destination(ALLOWED_URL)

    assert error.value.code == 'non_public_destination'
    assert resolver.call_count == 2
