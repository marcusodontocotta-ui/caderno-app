"""Suíte de testes automatizados (pytest + TestClient) do Caderno de Estudos API.

Rode com:  pytest -q
O ambiente DATABASE_URL (se ausente) aponta para um SQLite temporário isolado,
para nunca encostar no banco de produção/destino.
"""

import os
import tempfile

_TEST_DB = os.path.join(tempfile.gettempdir(), "caderno_test_api.db")
if os.path.exists(_TEST_DB):
    os.remove(_TEST_DB)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB}")

import pytest
from fastapi.testclient import TestClient

import main as main_mod
from main import app, hash_password, verify_password
from config import settings
from coupons import aplicar_desconto
from database import SessionLocal, User


def test_legacy_passlib_hash_still_verifies():
    """BAIXA-5: hash gerado com passlib|bcrypt 4.0.1 (produção) valida no
    novo código (bcrypt direto). Garante que logins antigos não quebram."""
    legacy_hash = "$2b$12$nvXSVLHO4PgWVEBU0wySHu/aRC30zpdbv3wWblvBS9rRNb9lqdBTa"
    assert verify_password("SenhaLegada123", legacy_hash) is True
    assert verify_password("SenhaErrada", legacy_hash) is False


@pytest.fixture(autouse=True)
def _clean_state():
    """Limpa o rate-limit em memória (MÉDIA-1) entre os testes."""
    main_mod._login_state.clear()
    yield
    main_mod._login_state.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _register(c, email, password="Segredo123"):
    return c.post("/auth/register", json={"email": email, "password": password})


def _auth_headers(c, email, password="Segredo123"):
    r = c.post("/auth/login", json={"email": email, "password": password})
    if r.status_code == 401:
        r = c.post("/auth/register", json={"email": email, "password": password})
    if r.status_code not in (200, 201):
        raise AssertionError(r.text)
    return {"Authorization": "Bearer " + r.json()["access_token"]}


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["db"] in ("ok", "error")


def test_health_db_separate(client):
    # /health/db não deve derrubar liveness: 200 quando o banco responde
    r = client.get("/health/db")
    assert r.status_code == 200
    assert r.json() == {"db": "ok"}


# --------------------------------------------------------------------------
# Registro — politico de senha (MÉDIA-1)
# --------------------------------------------------------------------------
def test_register_success_sets_httponly_cookie(client):
    r = _register(client, "novo@teste.com")
    assert r.status_code in (200, 201)
    body = r.json()
    assert body.get("access_token")
    assert body.get("email") == "novo@teste.com"
    assert body.get("is_premium") is False
    # cookie httpOnly setado (MÉDIA-4)
    cookie = client.cookies.get(settings.cookie_name)
    assert cookie == body["access_token"]


def test_register_rejects_weak_password(client):
    for pw in ("a1", "abcdefgh", "12345678", "minha-senha-sem-numero"):
        r = _register(client, f"weak-{abs(hash(pw))}@teste.com", password=pw)
        assert r.status_code == 422, f"senha {pw!r} deveria ser rejeitada"


def test_register_accepts_strong_password(client):
    r = _register(client, "forte@teste.com", password="senhaNova123")
    assert r.status_code in (200, 201)


def test_register_duplicate_email(client):
    _register(client, "dupe@teste.com")
    r = _register(client, "dupe@teste.com")
    assert r.status_code == 400


# --------------------------------------------------------------------------
# Login — rate-limit / lockout (MÉDIA-1)
# --------------------------------------------------------------------------
def test_login_success(client):
    _register(client, "login-ok@teste.com")
    r = client.post("/auth/login", json={"email": "login-ok@teste.com", "password": "Segredo123"})
    assert r.status_code == 200
    assert r.json().get("access_token")


def test_login_wrong_password(client):
    _register(client, "login-erro@teste.com")
    r = client.post("/auth/login", json={"email": "login-erro@teste.com", "password": "Errada123"})
    assert r.status_code == 401


def test_login_lockout_after_max_failures(client):
    _register(client, "lockout@teste.com")
    for _ in range(main_mod.LOGIN_MAX_FAILURES):
        r = client.post("/auth/login", json={"email": "lockout@teste.com", "password": "Errada123"})
        assert r.status_code == 401
    # limite atingido -> 429 (bloqueio do e-mail; IP também fica bloqueado)
    r = client.post("/auth/login", json={"email": "lockout@teste.com", "password": "Segredo123"})
    assert r.status_code == 429


def test_logout_revoca_token(client):
    r = _register(client, "logout@teste.com")
    tok = r.json()["access_token"]
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + tok}).status_code == 200
    r = client.post("/auth/logout", headers={"Authorization": "Bearer " + tok})
    assert r.status_code == 200
    # token antigo invalido (token_version aumentou - MÉDIA-3)
    r = client.get("/auth/me", headers={"Authorization": "Bearer " + tok})
    assert r.status_code == 401


def test_me_requires_auth(client):
    assert client.get("/auth/me").status_code == 401


# --------------------------------------------------------------------------
# Cadernos — limite do plano gratuito e IDs
# --------------------------------------------------------------------------
def test_free_plan_can_create_only_one_notebook(client):
    h = _auth_headers(client, "freeuser@teste.com")
    r = client.post("/notebooks", json={"name": "Só Um"}, headers=h)
    assert r.status_code in (200, 201)
    r = client.post("/notebooks", json={"name": "Segundo"}, headers=h)
    assert r.status_code == 402


def test_premium_user_can_create_more_than_one(client):
    _register(client, "premium@teste.com")
    r = client.post("/auth/login", json={"email": "premium@teste.com", "password": "Segredo123"})
    tok = r.json()["access_token"]
    with SessionLocal() as s:
        u = s.query(User).filter(User.email == "premium@teste.com").first()
        u.is_premium = True
        from datetime import datetime, timedelta
        u.premium_until = datetime.utcnow() + timedelta(days=30)
        s.commit()
    h = {"Authorization": "Bearer " + tok}
    for i in range(3):
        r = client.post("/notebooks", json={"name": f"Premium {i}"}, headers=h)
        assert r.status_code in (200, 201)


def test_pages_crud(client):
    h = _auth_headers(client, "pages@teste.com")
    nb = client.post("/notebooks", json={"name": "Páginas"}, headers=h).json()
    pid = nb["pages"][0]["id"]
    r = client.put(f"/notebooks/{nb['id']}/pages/{pid}", json={"text": "<p>oi</p>"}, headers=h)
    assert r.status_code == 200 and r.json()["text"] == "<p>oi</p>"
    r = client.post(f"/notebooks/{nb['id']}/pages", json={"text": "<p>nova</p>"}, headers=h)
    assert r.status_code in (200, 201)


def test_pages_idor_returns_404(client):
    """B não consegue tocar na página de A (IDOR bloqueado -> 404)."""
    h_a = _auth_headers(client, "idor-a@teste.com")
    nb_a = client.post("/notebooks", json={"name": "A"}, headers=h_a).json()
    pid_a = nb_a["pages"][0]["id"]
    h_b = _auth_headers(client, "idor-b@teste.com")
    r = client.put(
        f"/notebooks/{nb_a['id']}/pages/{pid_a}",
        json={"text": "<p>invadido</p>"},
        headers=h_b,
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------
# Cupons (MÉDIA-9 / fluxos de desconto)
# --------------------------------------------------------------------------
def test_cupom_validar_ok(client):
    _register(client, "cupom@teste.com")
    r = client.get("/billing/cupom/validar?codigo=CADERNO50")
    assert r.status_code == 200
    body = r.json()
    assert body["percentual"] == 50
    assert abs(body["valor_final"] - 2.45) < 0.01


def test_cupom_invalido(client):
    _register(client, "cupom2@teste.com")
    assert client.get("/billing/cupom/validar?codigo=NAO_EXISTE").status_code == 404


def test_aplicar_desconto_bloqueia_zerar_valor():
    with pytest.raises(Exception) as ex:
        aplicar_desconto(4.90, 100)
    assert getattr(ex.value, "status_code", 400) == 400


# --------------------------------------------------------------------------
# Billing — sem Mercado Pago configurado deve falhar de forma segura
# --------------------------------------------------------------------------
def test_billing_premium_sem_mp_configurado(client):
    h = _auth_headers(client, "billing@teste.com")
    r = client.post("/billing/premium", json={}, headers=h)
    assert r.status_code == 503


# --------------------------------------------------------------------------
# Webhook — fail-closed (ALTA, nao pode ser revertido) + MÉDIA-7
# --------------------------------------------------------------------------
def test_webhook_fail_closed_sem_secret(client):
    old = settings.mp_webhook_secret
    settings.mp_webhook_secret = ""
    try:
        r = client.post("/billing/webhook", data={"type": "preapproval", "data.id": "x"})
        assert r.status_code == 503
    finally:
        settings.mp_webhook_secret = old


def test_webhook_rejeita_assinatura_invalida(client):
    old = settings.mp_webhook_secret
    settings.mp_webhook_secret = "segredo-teste"
    try:
        r = client.post(
            "/billing/webhook",
            data={"type": "preapproval", "data.id": "123"},
            headers={"x-signature": "ts=1,v1=invalido"},
        )
        assert r.status_code == 401
    finally:
        settings.mp_webhook_secret = old