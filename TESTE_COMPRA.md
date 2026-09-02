# Plano de Teste E2E — Compra Premium (Mercado Pago)

> Objetivo: validar de ponta a ponta a compra da assinatura Premium (R$4,90/mês) via
> Checkout Pro / preapproval, desde o clique no front até o backend ativar o premium.
> Siga os passos **na ordem**. Este documento é o roteiro manual do humano.

---

## 0. URLs e dados de referência

| Item | Valor |
|---|---|
| Backend API | `https://caderno-app.onrender.com` |
| Frontend | `https://caderno-fy36.onrender.com` |
| Healthcheck | `GET https://caderno-app.onrender.com/health` → espera `{"status":"ok"}` |
| Painel MP | `https://www.mercadopago.com.br/developers/panel/app` |
| Render (env vars / deploys) | `https://dashboard.render.com/web/srv-da9mlhegekts738lantg` |

Endpoints envolvidos:
- `POST /auth/register` e `POST /auth/login` → retornam `access_token`
- `POST /notebooks` (criar caderno; gratuito = no máx. 1)
- `POST /billing/premium` (autenticado) → `{ init_point, preapproval_id }`
- `POST /billing/webhook` (chamado pelo MP)
- `GET /billing/status` (autenticado) → `{ is_premium, premium_until }`

---

## 1. Pré-requisitos / itens manuais obrigatórios (configurar ANTES de testar)

### 1.1. Webhook no painel Mercado Pago (configuração manual — essencial)
1. Acesse o painel MP: **https://www.mercadopago.com.br/developers/panel/app**
2. Abra o app **"Caderno de estudo"**.
3. Vá em **Webhooks** / **Configuração de Webhooks** (ou *Notificações / Webhooks*).
4. Cadastre a URL de notificação:
   `https://caderno-app.onrender.com/billing/webhook`
5. **Eventos**: habilite ao menos **`payment`** e **`preapproval`** (assinaturas).

> ⚠️ **Importante (Webhook Secret):**
> - O backend valida a assinatura (`x-signature`) através de `MP_WEBHOOK_SECRET`
>   (ver `config.py` e `billing.py:verify_signature`).
> - Se a env var **não estiver setada**, o backend é **fail-closed**: `POST /billing/webhook`
>   responde **`503`** (não aceita webhook sem assinatura).
> - Com o segredo setado mas assinatura inválida → **`401 Assinatura invalida`**.
> - **A env var `MP_WEBHOOK_SECRET` já existe no serviço Render** (confirmado em `ROTACAO_MP.md`),
>   e precisa conter o mesmo valor informado no painel MP
>   (Configuração do webhook → campo "Segredo"/"Secret" ou "chave de assinatura").
> - **Ação manual do humano:** conferir/coletar o segredo no painel e garantir que ele está
>   igual à env var `MP_WEBHOOK_SECRET` no Render.

### 1.2. Credenciais de produção
- `MP_ACCESS_TOKEN` e `MP_PUBLIC_KEY` devem ser de **produção** (não `TEST-`).
- Se os valores precisarem ser renovados, siga `ROTACAO_MP.md`.

---

## 2. Fluxo do Webhook (para entendimento / validação manual)

O Mercado Pago envia `POST /billing/webhook` com:
- Form field **`type`** → `preapproval` **ou** `payment`
- Form field **`data.id`** → id da assinatura / pagamento
- Header **`x-signature`** (ex.: `ts=1692...,v1=abc...`)

Comportamento (`main.py` -> rota `POST /billing/webhook`):
1. **Fail-closed**: se `MP_WEBHOOK_SECRET` não estiver setado → `503` (recusa).
   Com segredo, valida `x-signature` via HMAC-SHA256; falha → `401`.
2. `type == "preapproval"` → busca o preapproval na MP, e se `status` for
   `authorized`/`active` **e** `external_reference` começar com `caderno:` → extrai o
   `user_id` e chama `activate_user_premium` (`+30 dias`). Se `status` for
   `cancelled`/`paused` → chama `deactivate_user_premium` (premium cai para gratuito).
3. `type == "payment"` → `status == "approved"` ativa; `refunded`/`chargeback`/`cancelled`
   desativam via `deactivate_user_premium`.

> **external_reference** gerado na criação é `caderno:<user_id>` (`billing.py`).
> **Subscription** é criada/tornada `active` com `mp_preapproval_id` e valor R$4,90.

---

## 3. Passo a passo da compra real (E2E)

### Passo A — Login / ser levado ao Checkout Pro
1. Abra **https://caderno-fy36.onrender.com**.
2. Faça **login** (ou cadastre uma conta nova).
3. Clique em **Premium** (ou botão de upgrade).
4. O front deve chamar `POST /billing/premium` e **redirecionar** para o `init_point`
   (Checkout Pro do Mercado Pago).

**Observar:** a URL deve ir para `https://www.mercadopago.com.br/checkout/v1/...`
ou similar, exibindo o plano recorrente de **R$4,90/mês**.

### Passo B — Pagar
- **Opção 1 — Sem cartão real:** o Mercado Pago oferece **cartão de teste** no **sandbox**.
  Para testes de produção sem dinheiro real, use um cartão de teste do MP
  (ex.: Mastercard `5031 7557 3453 0604` — *apenas se o checkout estiver em modo teste*
  com token `TEST-`; com token de produção o pagamento é real).
- **Opção 2 — Pagamento real:** o próprio usuário confirma o pagamento de R$4,90
  (cartão/Pix). **Esta validação exige dinheiro real, que será cobrado.**

### Passo C — Verificar notificação no painel MP
1. Acesse **https://www.mercadopago.com.br/developers/panel/app** → app → **Notificações/Webhooks**.
2. Confirme que chegaram notificações de tipo **`preapproval`** e/ou **`payment`**
   apontando para `https://caderno-app.onrender.com/billing/webhook`.
3. Confira o `status` da assinatura em **Assinaturas** (deve aparecer `active`/`authorized`).

### Passo D — Conferir que o webhook ativou o premium
Chame com o **token** do usuário logado:
```
GET https://caderno-app.onrender.com/billing/status
Authorization: Bearer <ACCESS_TOKEN>
```
**Observar:** `"is_premium": true` e `"premium_until"` com data futura (~+30 dias).

> Se `is_premium` continuar `false` após pagar, o webhook não chegou/validou:
> vá para a **Seção 5** (simulação manual) e depois revise a Seção 1.1.

### Passo E — Criar o 2º caderno (deve funcionar agora)
Com o usuário premium, crie um segundo caderno:
```
POST https://caderno-app.onrender.com/notebooks
Authorization: Bearer <ACCESS_TOKEN>
Content-Type: application/json

{ "name": "Segundo Caderno" }
```
**Observar:** HTTP **200/201** e o caderno criado (usuário gratuito receberia **402**).

### Passo F — Testar cancelamento
1. No front (ou painel MP), **cancele a assinatura** (Assinaturas → Cancelar).
2. O MP envia webhook `preapproval` com status `cancelled`/`paused`; o backend chama
   `deactivate_user_premium`, e `GET /billing/status` reflete `"is_premium": false`
   **imediatamente** (o usuário volta ao plano gratuito: criação de caderno limitada a 1).
   > Nota: a desativação depende do webhook chegar com assinatura válida. Se o premium
   > continuar ativo após cancelar, revise a Seção 1.1 (segredo/URL do webhook).

---

## 4. Comandos de API (referência para testes manuais)

### Cadastrar nova conta
```
POST https://caderno-app.onrender.com/auth/register
Content-Type: application/json

{ "email": "seu-email-de-teste-aqui@example.com", "password": "senha-forte" }
```
Resposta: `{ "access_token": "...", "email": "...", "is_premium": false }`

### Login
```
POST https://caderno-app.onrender.com/auth/login
Content-Type: application/json

{ "email": "seu-email-de-teste-aqui@example.com", "password": "senha-forte" }
```

### Gerar checkout premium (NÃO paga nada sozinho)
```
POST https://caderno-app.onrender.com/billing/premium
Authorization: Bearer <ACCESS_TOKEN>
```
Resposta: `{ "init_point": "https://www.mercadopago.com.br/...", "preapproval_id": "..." }`

### Status do billing
```
GET https://caderno-app.onrender.com/billing/status
Authorization: Bearer <ACCESS_TOKEN>
```

### Criar caderno
```
POST https://caderno-app.onrender.com/notebooks
Authorization: Bearer <ACCESS_TOKEN>
Content-Type: application/json

{ "name": "Meu Estudo" }
```

> 💡 **Exemplo PowerShell** (Windows, sem vazar token): guarde o token numa variável.
> ```powershell
> $login = Invoke-RestMethod -Method Post -Uri "https://caderno-app.onrender.com/auth/login" -ContentType "application/json" -Body '{"email":"SEU_EMAIL","password":"SUA_SENHA"}'
> $tok = $login.access_token
> $h = @{ Authorization = "Bearer $tok" }
> $prem = Invoke-RestMethod -Method Post -Uri "https://caderno-app.onrender.com/billing/premium" -Headers $h
> "init_point => " + $prem.init_point
> $st = Invoke-RestMethod -Method Get -Uri "https://caderno-app.onrender.com/billing/status" -Headers $h
> "is_premium => " + $st.is_premium
> ```

---

## 5. Simular o webhook manualmente (mock)

Útil para testar o backend **sem** depender do MP. Duas abordagens:

### 5.1. Teste do endpoint (via API)
Envie um POST com `type` e `data.id`:
```
POST https://caderno-app.onrender.com/billing/webhook
Content-Type: application/x-www-form-urlencoded

type=preapproval&data.id=PREAPPROVAL_ID_REAL
```
- **Fail-closed (comportamento atual):** sem `MP_WEBHOOK_SECRET`, o backend responde **`503`**
  (recusa). Com segredo setado, exige `x-signature` válida — enviar sem ou com valor errado
  → **`401 Assinatura invalida`**.

> Para ativar premium de verdade via mock, use o `preapproval_id` real de uma assinatura
> que já esteja `authorized`/`active`. Um `data.id` qualquer responderá `{"ok":true}` mas
> sem efeito (status não autorizado ou external_reference ausente).

### 5.2. Exemplo PowerShell (mock)
```powershell
# Sem MP_WEBHOOK_SECRET → o backend é fail-closed (503). Com segredo setado, esta chamada retorna 401:
Invoke-WebRequest -Method Post -Uri "https://caderno-app.onrender.com/billing/webhook" `
  -ContentType "application/x-www-form-urlencoded" `
  -Body "type=preapproval&data.id=PREAPPROVAL_ID_AQUI" | Select-Object StatusCode
```

> **Nota sobre assinatura (para quem for gerar `x-signature` de verdade):** a implementação
> `verify_signature` (`billing.py`) calcula HMAC-SHA256 sobre o manifest
> `id:{data_id};request-id:{ts};ts:{ts};` usando o segredo. Para o MP entregar notificação
> válida, o segredo no painel deve bater com `MP_WEBHOOK_SECRET` no Render. **A configuração
> do webhook no painel MP é manual e obrigatória** para o fluxo automático funcionar.

---

## 6. Critérios de aceite (checklist final)

- [ ] `GET /health` → 200 `{"status":"ok"}` no Render.
- [ ] `POST /billing/premium` retorna `init_point` HTTP 200 (consumidor autenticado).
- [ ] Checkout Pro abre com recorrência de R$4,90/mês.
- [ ] Pagamento concluído (real ou teste).
- [ ] Painel MP mostra notificação `preapproval`/`payment` e assinatura `active`.
- [ ] Webhook configurado em `https://caderno-app.onrender.com/billing/webhook`.
- [ ] `MP_WEBHOOK_SECRET` no Render = segredo do painel MP (para validação de assinatura).
- [ ] `GET /billing/status` → `is_premium: true`, `premium_until` futuro.
- [ ] Criação do 2º caderno funciona (não retorna 402).
- [ ] Cancelamento da assinatura registrado no painel MP (webhook `cancelled`/`paused`
      desativa o premium → `is_premium: false`).

---

## 7. Reverter / limpar teste
- Contas criadas para teste ficam no banco; podem ser ignoradas ou removidas manualmente.
- Não pague duas vezes com o mesmo cartão de produção sem cancelar a assinatura anterior.
