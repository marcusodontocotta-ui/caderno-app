# Rotação de credenciais Mercado Pago — preparação (troca instantânea)

> Documento de **preparação**. A rotação em si **só pode ser feita no painel do Mercado Pago**;
> este script/guia automatiza a **aplicação imediata** do novo par no Render, para o checkout
> voltar a funcionar sem depender de cópia manual de variáveis.

---

## 1. Credenciais envolvidas (nomes EXATOS)

| Env var (Render) | Campo no backend | Onde é usada |
|---|---|---|
| `MP_ACCESS_TOKEN` | `settings.mp_access_token` (`config.py:9`) | Bearer token de todas as chamadas à API MP em `billing.py` (`_headers()`, `billing.py:18`) e checagem de configurado (`billing.py:25`) |
| `MP_PUBLIC_KEY` | `settings.mp_public_key` (`config.py:10`) | Lida pelo `Settings`; mantenha sincronizada com o token (o painel renova o **par inteiro**). |

Leitura feita via `pydantic-settings` (`config.py`) + arquivo `.env` local; no Render, via env vars do serviço.

**Status da verificação via API Render (GET `/env-vars`, só leitura — 29/08/2026):**
- As 5 env vars existentes no serviço `srv-da9mlhegekts738lantg`: `DATABASE_URL`, `MP_WEBHOOK_SECRET`, `SECRET_KEY`, `MP_PUBLIC_KEY`, `MP_ACCESS_TOKEN`. **[OK]** Padrão confirmado — nenhuma sobrescrita de outras variáveis é feita (o PUT abaixo atualiza **apenas** a chave indicada).

> A `MP_ACCESS_TOKEN` atual **não** é exibida neste documento.

---

## 2. Passo manual — feito NO PAINEL do Mercado Pago (você, humano)

1. Acesse **[https://www.mercadopago.com.br/developers/panel/app](https://www.mercadopago.com.br/developers/panel/app)**.
2. Abra o app **"Caderno de estudo"**.
3. Vá em **Credenciais de produção** → **Renovar** (acessar e/ou renovar).
4. **ATENÇÃO:** renovar **invalida o par anterior** (Public Key + Access Token) — o checkout **quebra** no instante da renovação, até os novos valores serem aplicados no Render.
5. Copie os dois novos valores apresentados pelo painel:
   - **Public Key da produção** (ex.: `APP_PUB-xxxx…`)
   - **Access Token da produção** (ex.: `APP_USR-xxxx…`)
6. **Aplique imediatamente** os valores novos com o script da próxima seção (não feche o painel antes de rodar).

---

## 3. Como aplicar os novos valores no Render (automatizado)

Arquivo: [`rotate_mp_credentials.ps1`](rotate_mp_credentials.ps1) (mesma pasta).

### Comando

```powershell
# Na pasta caderno-app:
powershell -ExecutionPolicy Bypass -File .\rotate_mp_credentials.ps1 `
  -NewAccessToken "APP_USR-NOVO_TOKEN_COLADO_AQUI" `
  -NewPublicKey "APP_PUB-NOVA_PUBLIC_KEY_COLADA_AQUI"
```

Ou, dentro de um terminal PowerShell já aberto na pasta:

```powershell
.\rotate_mp_credentials.ps1 -NewAccessToken "APP_USR-..." -NewPublicKey "APP_PUB-..."
```

### O que o script faz (nesta ordem)

1. Valida que os valores não são vazios nem placeholders (`TEST-`, `xxxx`, `placeholder`) — aborta caso contrário.
2. Lista os **nomes** das env vars atuais do serviço (sem exibir valores).
3. `PUT /services/{id}/env-vars/MP_ACCESS_TOKEN` → só essa chave.
4. `PUT /services/{id}/env-vars/MP_PUBLIC_KEY` → só essa chave.
5. `POST /services/{id}/deploys` (`clearCache: do_not_clear`) → redeploy que aplica as variáveis.
6. Exibe apenas **valores mascarados** (`primeiros6...últimos4`), os segredos nunca são impressos.

### Segurança / observações

- O script **mascara** qualquer saída; não usa `Out-String` após `try/catch`.
- O par novo passado como argumento pode ficar no histórico do shell. Se preferir, rode e cole os valores via variável no próprio terminal.
- A Render API key **não** é mais mantida em disco no script. Informe-a via:
  - `-RenderApiKey "..."`, **ou**
  - env var `RENDER_API_KEY`, ou
  - omita o parâmetro para que o script a solicite de forma **interativa e mascarada** (`Read-Host -AsSecureString`).
- O `ServiceId` também é argumento (`-ServiceId ...`) ou lido da env var `RENDER_SERVICE_ID` (ex.: `srv-da9mlhegekts738lantg`).
- O redeploy leva alguns minutos; o app reinicia ao final do deploy (breve indisponibilidade, não quebra permanente). Verifique o status em `https://dashboard.render.com/web/srv-da9mlhegekts738lantg/deploys`.

> **IMPORTANTE:** a Render API key `rnd_...` anteriormente embutida no script deve ser considerada **EXPOSTA**.
> Rote-a (regenere) no painel do Render conforme o guia [`ROTACAO_RENDERKEY.md`](ROTACAO_RENDERKEY.md).

### Checklist pós-rotação

- [ ] Reconhecer as novas credenciais de produção no painel MP (feito no passo 2).
- [ ] Rodar o script acima **logo após** renovar (reduz a janela de checkout quebrado).
- [ ] Deploy concluído com status `live`/`succeeded` no dashboard.
- [ ] Testar um novo checkout (`POST /billing/premium` no app) atingindo o `init_point` sem erro 4xx/5xx da MP.