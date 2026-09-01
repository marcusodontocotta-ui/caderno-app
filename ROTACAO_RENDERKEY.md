# Rotação da Render API Key (EXPOSTA — ação manual obrigatória)

> **URGENTE:** a Render API key (`rnd_...`, aqui referida como **`CHAVE_EXPOSTA`**) esteve em texto claro
> no arquivo `rotate_mp_credentials.ps1` (não versionado) e deve ser considerada **EXPOSTA / COMPROMETIDA**.
> Enquanto não for rotacionada, qualquer pessoa que possua essa chave pode gerenciar seus serviços
> no Render (ler/alterar env vars, disparar deploys, etc.).

## Passo manual (você, humano) — no painel do Render

1. Acesse [https://dashboard.render.com](https://dashboard.render.com) com a conta dona do serviço.
2. Clique no ícone de **conta** (canto inferior esquerdo) → **Account Settings**.
3. Vá na aba **API Keys** → **Generate API Key**.
4. Dê um nome (ex.: `caderno-2026`) e copie a **nova chave** gerada (**`rnd_...`**). Ela só é exibida uma vez.
5. **Revogue a chave antiga exposta** (`CHAVE_EXPOSTA` — a `rnd_...` que estava no script) — clique em **Revoke** na mesma lista.
6. Guarde a nova chave com segurança (gerenciador de senhas) — **não** a coloque em nenhum arquivo do repositório.

## Uso seguro da nova chave

A nova chave deve ser fornecida **somente em tempo de execução**, nunca versionada:

```powershell
# opção 1: env var (não fica no histórico do comando)
$env:RENDER_API_KEY = "rnd_NOVA_CHAVE_AQUI"   # defina no seu perfil/terminal, não no script
.\rotate_mp_credentials.ps1 -NewAccessToken "APP_USR-..." -NewPublicKey "APP_PUB-..."

# opção 2: argumento (fica no histórico do shell — evite se possivel)
.\rotate_mp_credentials.ps1 -NewAccessToken "APP_USR-..." -NewPublicKey "APP_PUB-..." -RenderApiKey "rnd_NOVA_CHAVE_AQUI"

# opção 3: interativo (mascarado) — omita -RenderApiKey e o script pergunta
.\rotate_mp_credentials.ps1 -NewAccessToken "APP_USR-..." -NewPublicKey "APP_PUB-..."
```

O `ServiceId` do backend é `srv-da9mlhegekts738lantg` (informe via `-ServiceId` ou `$env:RENDER_SERVICE_ID`).

## Verificação após a rotação

- [ ] Nova API key funciona (`GET /v1/services` retorna 200 com o Bearer novo).
- [ ] Chave antiga `CHAVE_EXPOSTA` (`rnd_...` que estava no script) revogada no painel e **rejeitada** pela API (401).
- [ ] Nenhuma ocorrência de `rnd_...` em arquivos versionados:
      `git grep -i rnd_` não retorna nada no backend nem no frontend.
- [ ] O arquivo `rotate_mp_credentials.ps1` **não** contém a chave (verificado), e está coberto pelo `.gitignore`.

## Notas

- A rotação da Render API Key **não** afeta o runtime do app (ela só é usada para chamadas administrativas).
- Nenhum redeploy é necessário apenas por rotacionar a chave; porém, se você também alterou env vars
  (por ex. `MP_WEBHOOK_SECRET`), dispare um redeploy do backend conforme o relatório de auditoria.
