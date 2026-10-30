# Webhook Delivery Service

API para registrar eventos, enviar webhooks por HTTPS a destinos autorizados e consultar o resultado das tentativas.

Exemplo: uma loja registra `pedido.pago` com o ID de um pedido, solicita o envio ao receptor e consulta o resultado.

**Fluxo:** cadastrar destino no admin → registrar evento → solicitar envio → consultar tentativa.

**Stack:** Python 3.14, Django 5.2, DRF, PostgreSQL 17, Docker Compose e pytest. Uma aplicação Django, com execução síncrona e uma tentativa por evento.

**Em desenvolvimento:** a interface para visitantes está implementada; sua ativação e verificação na hospedagem estão pendentes. O fluxo do operador já foi verificado por HTTPS com resultados salvos no Neon. Envios e receptores de demonstração ficam desabilitados por padrão.

[Saúde da base publicada](https://webhook-delivery-api.vercel.app/api/saude/) — confirma que a aplicação responde; não verifica banco ou entrega de webhooks.

## Executar localmente

Requisito: Docker com Compose. Comandos para Bash, na raiz do repositório após cloná-lo:

```bash
cp .env.example .env
```

Antes da primeira inicialização, preencha `DJANGO_SECRET_KEY` e `POSTGRES_PASSWORD` no seu `.env` com valores próprios. Para gerar dois valores aleatórios:

```bash
docker run --rm python:3.14-slim-bookworm python -c \
"import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(50)); print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32))"
```

Copie os valores gerados para o `.env` e mantenha as demais opções locais de [.env.example](.env.example). Depois:

```bash
docker compose up -d --build --wait --wait-timeout 60
docker compose exec api python manage.py migrate
docker compose exec api python manage.py createsuperuser
```

Acesse a [página inicial](http://127.0.0.1:8000/), a [saúde local](http://127.0.0.1:8000/api/saude/) e o [admin local](http://127.0.0.1:8000/admin/). Somente o admin exige o usuário criado. O Compose inicia API e PostgreSQL; não é necessário ativar uma `.venv` nesse fluxo.

Após alterar código ou `.env`, repita `docker compose up -d --build --wait --wait-timeout 60`; o código fica copiado na imagem. `docker compose down` para os containers e preserva o volume do banco. Alterar a senha no `.env` não altera a senha de um banco já inicializado.

<details>
<summary>Alternativa: Django na .venv, com Python 3.14 instalado</summary>

Use somente o banco no Compose neste modo. Com o `.env` local preparado:

```bash
docker compose stop api
docker compose up -d --wait --wait-timeout 60 db
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
set -a
source .env
set +a
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Se o banco já foi preparado pelo Docker, reaproveite as migrations e o usuário existentes. O Django lê as variáveis do ambiente; não carrega `.env` automaticamente. Carregue apenas seu próprio arquivo.

</details>

## Demonstração no navegador

Em `/`, escolha **Testar sucesso** ou **Testar falha**. Cada clique aceito cria um evento `pedido.pago` com `{"pedido_id": "PEDIDO-001"}`, solicita uma tentativa síncrona e mostra seu resultado. **Consultar resultado** busca o registro salvo, sem outro envio. Abrir a página não inicia chamadas de entrega.

- `POST /demonstracao/enviar/`: aceita somente `{"scenario": "success"}` ou `{"scenario": "failure"}`, como JSON de até 1 KiB; excesso retorna `413`. Retorna `200` com a tentativa concluída, inclusive quando o receptor respondeu `500`.
- `GET /demonstracao/eventos/<uuid>/`: consulta a tentativa pelo ID do evento e pelo cookie do visitante; sem esse vínculo, retorna `404`.

Não exige login. O navegador recebe um cookie assinado, HttpOnly, com validade de 24 horas; POST exige CSRF mesmo sem autenticação. A aba lembra o último ID e avisa quando não há confirmação do envio. Não há reenvio automático. Apagar o cookie pode renovar o limite do visitante; o orçamento global continua valendo.

Para habilitar, o operador deve:

1. Conferir migrations, cotas gratuitas e configurar proteção contra abuso na hospedagem antes de expor envios ao público.
2. Autorizar as duas URLs HTTPS públicas dos receptores em `WEBHOOK_ALLOWED_URLS`, cadastrar os destinos com tipo `pedido.pago` e preencher seus UUIDs em `WEBHOOK_DEMO_SUCCESS_ENDPOINT_ID` e `WEBHOOK_DEMO_FAILURE_ENDPOINT_ID`.
3. Ativar `WEBHOOK_DEMO_ENABLED=True` e `WEBHOOK_DEMO_RECEIVER_ENABLED=True`, com orçamento global positivo em `WEBHOOK_MAX_DELIVERIES_PER_DAY`. O padrão por visitante é 3 envios em 60 segundos e 4 em 24 horas, configurados em `WEBHOOK_MAX_DELIVERIES_PER_MINUTE` e `WEBHOOK_DEMO_MAX_DELIVERIES_PER_DAY`. As opções padrão de [.env.example](.env.example) mantêm os envios desabilitados.

## Usar a API

As operações de eventos e tentativas exigem sessão Django, usuário ativo da equipe e a permissão indicada. POST autenticado exige CSRF. Use a interface DRF no navegador após entrar no admin ou o Bruno API Client com cookies de sessão e CSRF; envie os corpos como JSON.

| Método | Rota | Resultado | Permissão |
| --- | --- | --- | --- |
| GET | `/api/saude/` | `200`, `{"status": "ok"}` | Pública |
| POST | `/api/eventos/` | `201`, evento `pending` | `webhooks.add_event` |
| GET | `/api/eventos/<uuid>/` | `200`, evento | `webhooks.view_event` |
| POST | `/api/eventos/<uuid>/enviar/` | `200`, resultado persistido | `webhooks.send_event` |
| GET | `/api/eventos/<uuid>/tentativa/` | `200`, tentativa | `webhooks.view_deliveryattempt` |

As permissões são por modelo: um operador autorizado pode consultar registros de outros operadores. Não há listagem, edição ou exclusão de eventos pela API.

1. **Cadastre o destino:** inclua sua URL HTTPS exata em `WEBHOOK_ALLOWED_URLS` e cadastre URL e tipo em **Admin → Webhooks → Destinos de webhook**. O par URL/tipo é único. O admin permite cadastro e consulta, sem edição ou exclusão; usuários da equipe precisam das permissões `add_webhookendpoint` e `view_webhookendpoint`.
2. **Registre o evento:** substitua `endpoint_id` pelo UUID do destino cadastrado e envie este JSON ao POST de eventos:

```json
{
  "endpoint_id": "11111111-1111-4111-8111-111111111111",
  "event_type": "pedido.pago",
  "payload": {"pedido_id": "PEDIDO-001"}
}
```

3. **Solicite o envio:** use o UUID do evento no POST `/enviar/`, com corpo vazio ou `{}`. O envio usa os dados já registrados; campos adicionais são rejeitados.
4. **Consulte a tentativa:** use o mesmo UUID no GET `/tentativa/`. Sem tentativa registrada, a resposta é `404`.

Cadastrar um destino ou evento não dispara envio. A solicitação de envio aguarda a chamada externa e a gravação do resultado.

| Estado | Significado |
| --- | --- |
| `pending` | Evento registrado, sem envio iniciado pelo serviço. |
| `sending` | Início registrado, sem resultado final persistido. |
| `delivered` | Receptor respondeu HTTP 200–299. |
| `failed` | Tentativa terminou sem resposta HTTP de sucesso. |

A tentativa apresenta `id`, `event_id`, `event_status`, `started_at`, `finished_at`, `http_status` e `error_code`. As categorias de falha são `http_error`, `network_error`, `timeout` e `destination_rejected`; sucesso usa texto vazio.

**HTTP 200 da nossa API significa resultado registrado:** pode conter `http_status: 500` e estado `failed`, pois esse código veio do receptor. Uma resposta 2xx do receptor não comprova seu processamento interno. Timeout ou interrupção não comprovam ausência de recebimento.

| Erro da API | Situação |
| --- | --- |
| `400` / `415` | Dados inválidos ou formato de corpo não suportado. |
| `403` | Falta de acesso ou CSRF. |
| `404` | Evento ou tentativa não encontrado. |
| `409` | Envio já iniciado; não há reenvio automático. |
| `429` | Cota atingida, com `Retry-After` em segundos. |
| `503` | Envio desabilitado ou reserva temporariamente ocupada. |
| `500` | Falha interna; consulte a tentativa antes de outra ação. |

## Receptores de demonstração

Representam o destinatário dentro da mesma aplicação Django. `WEBHOOK_DEMO_RECEIVER_ENABLED=True` habilita ambos, sem login ou CSRF; desativados retornam `404`.

| Método | Rota | Resposta com envelope válido |
| --- | --- | --- |
| POST | `/demonstracao/receptor/sucesso/` | `200`, `status: received` |
| POST | `/demonstracao/receptor/falha/` | `500` controlado, `status: rejected` |

Exigem envelope JSON UTF-8 com `id` UUID, `created_at` válido, `event_type: pedido.pago` e `payload: {"pedido_id": "PEDIDO-001"}`, sem campos extras. A resposta inclui `event_id`; o corpo é limitado a 17 KiB. Dados inválidos retornam `400`, outros métodos `405`, excesso de tamanho `413` e formato incompatível `415`.

Os receptores não gravam dados nem fazem envios. Sua ativação não libera envios nem altera as permissões das rotas do operador. Chamadas pela internet consomem recursos da hospedagem.

**Verificação real em Vercel/Neon:** dois eventos fictícios foram enviados por HTTPS aos receptores desta aplicação. O sucesso ficou `delivered`/HTTP `200`; a falha controlada ficou `failed`/HTTP `500`/`http_error`. Os resultados foram persistidos e consultados pela API com `200`. Os receptores foram desativados após a verificação.

## Segurança e limites

- **Destinos controlados:** `WEBHOOK_ALLOWED_URLS` contém URLs HTTPS completas e exatas, separadas por vírgulas. Vazia bloqueia cadastro e envio. Só use receptores que controla ou tem autorização para usar.
- **Proteção de saída:** revalidação de URL e DNS/IP, conexão ao IP validado e certificado TLS para o domínio original. IPs internos são bloqueados; sem redirecionamentos, proxy do ambiente ou encaminhamento de credenciais recebidas.
- **Dados:** payload como objeto JSON de até 16 KiB; envelope enviado de até 17 KiB. Corpo e headers da resposta externa não são persistidos.
- **Tempo:** TCP/TLS compartilham 3 s; escrita tem 5 s por operação; leitura tem prazo de 5 s e cota de 16 KiB. DNS não possui prazo total próprio na aplicação.
- **Orçamento:** limite por minuto separado por operador ou visitante; visitantes têm também cota de 24 horas. `WEBHOOK_MAX_DELIVERIES_PER_DAY=0` desabilita envio por padrão; um valor positivo, como `20`, limita o total de inícios nas últimas 24 horas, somando todos os visitantes e operadores. Zero em uma cota aplicável bloqueia o envio.
- **Concorrência:** o PostgreSQL coordena início único e reserva de orçamento. Tentativas iniciadas consomem cota mesmo quando falham; a chamada externa ocorre sem transação aberta.

`DJANGO_ALLOWED_HOSTS` configura os domínios da nossa API; `WEBHOOK_ALLOWED_URLS` controla os receptores. Uma interrupção pode deixar `sending` e tentativa incompleta, que exige avaliação manual e não permite reenvio automático. Os limites de envio não substituem proteção contra DDoS.

## Testes

Com os serviços locais iniciados:

```bash
docker compose exec api python manage.py check
docker compose exec api python -m pytest -q
```

A suíte cobre validações, permissões, CSRF, persistência, falhas, concorrência, cotas, retenção, receptores e rotas da demonstração. Usa PostgreSQL local em banco separado e transporte/DNS/TLS simulados; não execute com a configuração do Neon.

Com Node.js disponível na máquina, execute também `node tests/test_demo_ui.mjs`. Esses testes usam DOM e HTTP simulados em memória para verificar os botões, as mensagens e a consulta. Node não é necessário para executar a API. As suítes não substituem a verificação de entrega real nem a revisão visual no navegador.

## Retenção de dados

A limpeza é manual. Este comando apenas mostra os registros elegíveis:

```bash
docker compose exec api python manage.py purge_webhook_data
```

A opção `--apply` exclui eventos pendentes sem tentativa e entregas concluídas com mais de sete dias, considerando as datas do evento e da tentativa. Incompletos, dados recentes, destinos e usuários são preservados. Cada lote de até 100 eventos é confirmado separadamente; uma falha desfaz somente o lote atual. Registros ocupados ficam para outra execução. Não há agendamento automático.

## Publicação

Vercel Hobby executa o Django; Neon Free fornece o PostgreSQL. [vercel.json](vercel.json) permite deploy automático somente da `main`, sem Preview automático.

Configure em Production: chave Django própria, `DJANGO_DEBUG=False`, domínio específico em `DJANGO_ALLOWED_HOSTS`, `DJANGO_SECURE_COOKIES=True` e `POSTGRES_*` do Neon com `POSTGRES_SSLMODE=require`. O `disable` de [.env.example](.env.example) é exclusivo do PostgreSQL local sem TLS.

**Aplique as migrations no Neon antes de publicar o código correspondente.** Migrar localmente ou fazer merge não atualiza o banco hospedado. Para operações pelo terminal, mantenha as opções do Neon em `.env.neon`, separadas do `.env` local. Credenciais, arquivos `.env` locais e `.venv/` ficam fora do Git; `.env.example` é o modelo público.

## Fora do MVP

Retry automático, assinatura HMAC, chave de idempotência, filas e processamento em segundo plano ficam para evolução. Operações genéricas de cadastro e envio permanecem restritas ao operador; visitantes usam somente os dois cenários fixos da demonstração.

## Licença

[MIT](LICENSE).
