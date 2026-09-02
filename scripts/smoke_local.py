from fastapi.testclient import TestClient
from main import app

with TestClient(app) as c:
    print('health', c.get('/health').status_code, c.get('/health').json())

    r = c.post('/auth/register', json={'email': 'teste@teste.com', 'password': 'segredo123'})
    print('register', r.status_code, r.json().get('email'), r.json().get('is_premium'))
    tok = r.json()['access_token']
    h = {'Authorization': 'Bearer ' + tok}

    r = c.post('/notebooks', json={'name': 'Meu Estudo'}, headers=h)
    print('create nb', r.status_code)
    nb = r.json()
    print('  nb id', nb['id'], 'pages', len(nb['pages']))

    r = c.get('/notebooks', headers=h)
    print('list nb', r.status_code, 'count', len(r.json()))

    pid = nb['pages'][0]['id']
    r = c.put('/notebooks/{}/pages/{}'.format(nb['id'], pid), json={'text': '<p>Ola mundo</p>'}, headers=h)
    print('update page', r.status_code, r.json()['text'])

    r = c.post('/auth/login', json={'email': 'teste@teste.com', 'password': 'segredo123'})
    print('login', r.status_code, bool(r.json().get('access_token')))

    r = c.post('/notebooks', json={'name': 'Segundo'}, headers=h)
    print('segundo caderno (gratis deveria 402):', r.status_code)
