def test_health_returns_ok_without_authentication(client):
    response = client.get('/api/saude/', HTTP_ACCEPT='application/json')

    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'application/json'
    assert response.json() == {'status': 'ok'}


def test_health_rejects_post(client):
    response = client.post('/api/saude/', HTTP_ACCEPT='application/json')

    assert response.status_code == 405
    assert response.json()['detail']


def test_health_accepts_allowed_host(client, settings):
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ['127.0.0.1']

    response = client.get(
        '/api/saude/',
        HTTP_HOST='127.0.0.1',
        HTTP_ACCEPT='application/json',
    )

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_health_rejects_unapproved_host(client, settings):
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ['127.0.0.1']

    response = client.get(
        '/api/saude/',
        HTTP_HOST='untrusted.example',
        HTTP_ACCEPT='application/json',
    )

    assert response.status_code == 400
