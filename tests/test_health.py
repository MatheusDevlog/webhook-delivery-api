def test_health_returns_ok_without_authentication(client):
    response = client.get('/api/saude/', HTTP_ACCEPT='application/json')

    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'application/json'
    assert response.json() == {'status': 'ok'}


def test_health_rejects_post(client):
    response = client.post('/api/saude/', HTTP_ACCEPT='application/json')

    assert response.status_code == 405
    assert response.json()['detail']
