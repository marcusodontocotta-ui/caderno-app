# Caderno de Estudos — API (backend)

API em **FastAPI** do [Caderno de Estudos](https://caderno-fy36.onrender.com):
autenticação por conta (JWT), sincronização de cadernos/páginas e assinatura
Premium recorrente via **Mercado Pago** (preapproval + webhooks).

- **Produção:** <https://caderno-app.onrender.com>
- **Docs interativas (Swagger):** <https://caderno-app.onrender.com/docs>
- **Healthcheck:** `GET /health` → `{"status":"ok","db":"ok|error"}` (liveness: sempre `200`; o estado do banco vai no corpo — não derruba o deploy)
- **Readiness do banco:** `GET /health/db` → `200 {"db":"ok"}` ou `503` se o Postgres não responder (não é usado pelo healthcheck do Render; uso para observabilidade/testes)

---

## Setup local

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Por padrão usa **SQLite** (`./caderno.db`) — nenhuma configuração extra para
rodar local. Em produção, o Render usa **PostgreSQL** (`DATABASE_URL`).

Testes:

```bash
pytest -q
```

> O `pytest` isola o banco com um SQLite temporário (`DATABASE_URL`
> `sqlite:///<tmp>/caderno_test_api.db`) — nunca toca no banco de destino.

---

## Variáveis de ambiente

| Variável | Obrigatória | Padrão | Descrição |
|---|---|---|---|
| `SECRET_KEY` | **Sim (produção)** | `troque-esta-chave` | Chave de assinatura dos JWTs. **Fail-closed em produção**: se ausente (detecta `RENDER`), o app **não sobe**. Gere com `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `DATABASE_URL` | No (SQLite local) | `sqlite:///./caderno.db` | URL do banco (ex.: PostgreSQL do Render). |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `10080` (7 dias) | Duração do JWT/cookie de sessão. JWT nunca acima de 30 dias com o secret padrão (guarda em `config.py`). |
| `COOKIE_NAME` | No | `caderno_access_token` | Nome do cookie httpOnly de sessão. |
| `CORS_ORIGINS` | No | `https://caderno-fy36.onrender.com,http://localhost:8000` | Origens permitidas (separadas por vírgula) — **nunca `*`** com credenciais. |
| `MP_ACCESS_TOKEN` | Para Premium | `""` | Access Token de produção do Mercado Pago. |
| `MP_PUBLIC_KEY` | Para Premium | `""` | Public Key do Mercado Pago. |
| `MP_WEBHOOK_SECRET` | Para Premium | `""` | Segredo que valida `x-signature` dos webhooks. **Fail-closed**: sem ele, `POST /billing/webhook` responde `503`. |
| `MP_PREMIUM_AMOUNT` | No | `4.90` | Valor mensal do Premium (R$). |
| `MP_BACK_URL` | No | `https://caderno-fy36.onrender.com` | URL de retorno do checkout. |
| `MP_WEBHOOK_URL` | No | `https://caderno-app.onrender.com/billing/webhook` | URL de notificação enviada ao MP. |

> **Hashing de senha (BAIXA-5):** o app usa **`bcrypt` diretamente** (`bcrypt==5.0.0`); o `passlib` foi removido. Hashes antigos (gerados com passlib|crypt) são bcrypt puro `$2b$12$...` e continuam validando com `bcrypt.checkpw` (teste de compatibilidade em `tests/test_api.py`). Reproduzimos o truncamento de 72 bytes do passlib (`auth.py:_bcrypt_password_bytes`) para não alterar o comportamento de senhas longas.

---

## Endpoints

### Autenticação
- `POST /auth/register` — cria conta. Exige senha forte (mín. **8 caracteres**, com **letra e número**). Responde com JWT no corpo **e** cookie httpOnly (`SameSite=Lax`, `Secure` em HTTPS).
- `POST /auth/login` — login. **Rate-limit/lockout em memória**: 5 falhas em 5 min ⇒ bloqueio de **15 min** por e-mail e por IP (HTTP `429`).
- `POST /auth/logout` — invalida a sessão (incrementa `token_version`; qualquer JWT anterior é rejeitado) e apaga o cookie.
- `GET /auth/me` — dados da sessão atual (`email`, `is_premium`).

### Cadernos e páginas (autenticados)
- `GET /notebooks` · `POST /notebooks` (grátis = máx. **1 caderno**; Premium = ilimitado; o 2.º retorna `402`) · `PUT/POST /notebooks/{id}` etc.
- `GET/POST /notebooks/{nb}/pages` · `PUT/DELETE /notebooks/{nb}/pages/{page}` — **com checagem de posse** (página de outro usuário ⇒ `404`, sem vazar existência).

### Billing / Mercado Pago
- `POST /billing/premium` — gera o checkout (preapproval) mensal; aceita `{"cupom": "CADERNO50"}`. Erros do MP são **logados internamente** e retornados como mensagem genérica (não vaza detalhes/respostas do MP).
- `GET /billing/cupom/validar?codigo=X` — valida cupom ativo (`CADERNO50` = 50%, `CADERNO80` = 80%). Cupom que zeraria o valor é rejeitado.
- `POST /billing/webhook` — notificações `preapproval`/`payment`. **Fail-closed**: exige `MP_WEBHOOK_SECRET` e assinatura válida (`503` sem segredo; `401` assinatura inválida). `authorized/active` ⇒ ativa Premium (+30 dias); `cancelled/paused` (preapproval) e `refunded/chargeback/cancelled` (payment) ⇒ **desativa** via `deactivate_user_premium`.
- `GET /billing/status` — `{ is_premium, premium_until }`.

### Outros
- `GET /health` — liveness (sempre `200`; `db: "ok"|"error"` no corpo para observabilidade).
- `GET /health/db` — readiness do banco (`200`/`503`). Endpoint separado para não causar crash-loop em falha pontual do Postgres.

---

## Segurança (resumo)

- **Senha forte + rate-limit/lockout** no registro/login (MÉDIA-1).
- **CORS restrito** às origens conhecidas, com credenciais (MÉDIA-2).
- **JWT curto (7 dias) + revogação via `token_version`** (logout invalida tudo) e **secret exigido em produção** (fail-closed) (MÉDIA-3).
- **Sessão em cookie httpOnly** (`SameSite=Lax`, `Secure`) — token não fica no `localStorage`/leível por JS (MÉDIA-4). O header `Authorization: Bearer` ainda é aceito como fallback (compatibilidade).
- **Webhook fail-closed** (segredo + assinatura HMAC-SHA256) (ALTA) e **erros do MP não vazam detalhes** (MÉDIA-7).
- **Seed de cupons não-destrutivo** (`ON CONFLICT DO NOTHING` no Postgres) — desativação manual é preservada entre boots (MÉDIA-8).
- **Hashing sem passlib** (bcrypt 5.0.0 direto, BAIXA-5) — compatível com hashes legados `$2b$`.

## Estrutura

```
caderno-app/
  main.py        # FastAPI (rotas, CORS, auth, rate-limit, lifespan startup, health/db)
  auth.py        # JWT + cookie httpOnly, hash de senha (bcrypt direto)
  billing.py     # Mercado Pago (preapproval, webhook, cupons)
  coupons.py     # cálculo de desconto (protege contra valor <= R$ 0,01)
  database.py    # SQLAlchemy (users, notebooks, pages, subscriptions, cupons)
  config.py      # Settings por env var (fail-closed em produção)
  tests/         # pytest + TestClient
  .github/workflows/ci.yml  # CI (lint de sintaxe + pytest)
```