# Webhook Delivery Service

Projeto de backend para registrar eventos, enviar webhooks a destinos autorizados e consultar o resultado das tentativas de entrega.

## Estado atual

A aplicação usa Django e DRF, com PostgreSQL e execução local por Docker Compose. Existe uma rota de saúde com testes automatizados e cadastro de destinos autorizados pelo admin Django. O registro de eventos, o envio de webhooks e a demonstração interativa ainda não foram implementados.

A base está publicada na Vercel. A conexão ao PostgreSQL no Neon foi verificada pelo terminal com criptografia exigida, e a consulta pela aplicação hospedada foi verificada pelo fluxo de autenticação do admin. Cada ambiente precisa das migrations correspondentes ao código publicado; não presuma que gerar ou aplicar uma migration localmente atualiza o Neon.

O MVP planejado terá uma tentativa síncrona por evento, registro do resultado HTTP e consulta do estado. A demonstração usará dados fictícios e receptores controlados.

## Tecnologias

- Python, Django e Django REST Framework.
- PostgreSQL, com Docker Compose no desenvolvimento local.
- pytest e pytest-django para testes.
- Neon Free para o banco hospedado e Vercel Hobby para a publicação inicial da aplicação.

## Base publicada

Acesse a [rota de saúde publicada](https://webhook-delivery-api.vercel.app/api/saude/). No navegador, a interface padrão do DRF apresenta a resposta HTTP `200` com o conteúdo:

```json
{"status": "ok"}
```

Para consultar pelo Bruno API Client, ferramenta para testar APIs, use `GET` nessa mesma URL, sem autenticação e sem corpo, com o header `Accept: application/json`. A resposta será JSON. `POST` nessa rota retorna `405`.

A saúde pública foi verificada no navegador. Ela confirma que a aplicação responde, sem consultar o banco nem realizar envios externos. O endereço `/` ainda não possui página inicial. Este link permite explorar a base publicada; o fluxo de webhooks ainda está em desenvolvimento.

A publicação usa variáveis configuradas na Vercel em **Production**: chave Django própria, `DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS=webhook-delivery-api.vercel.app` e as opções `POSTGRES_*` do Neon, com `POSTGRES_SSLMODE=require`. Senhas e chaves não são versionadas. O projeto adota publicação automática somente da `main`, conforme a configuração abaixo.

Em 17/10/2026, uma tentativa de login no admin publicado com dados fictícios retornou a mensagem padrão de credenciais inválidas. Na configuração padrão usada aqui, essa resposta ocorre após consultar o usuário no banco: confirma uma leitura pela aplicação hospedada, sem comprovar login autorizado ou gravação de dados. Apenas abrir o formulário não seria suficiente.

Após a publicação da PR #3, a saúde e o login do admin retornaram `200`, e a resposta do login apresentou o cookie `csrftoken` com o atributo `Secure`. O cookie de sessão ainda não foi observado em acesso autenticado. O sucesso da saúde não substitui verificações de segurança nem de acesso ao banco.

### Publicação automática somente da main

O arquivo `vercel.json`, na raiz, configura `git.deploymentEnabled` com `"**": false` e `"main": true`. A primeira regra desativa deploys automáticos para todas as branches, incluindo nomes com `/`; a exceção mantém o deploy automático da `main`. Quando mais de uma regra corresponde à branch, uma regra com `true` permite a publicação. [Configuração Git da Vercel](https://vercel.com/docs/project-configuration/git-configuration#git.deploymentenabled).

Branches de trabalho são verificadas localmente antes da PR. Depois do merge na `main`, a integração Git da Vercel publica a aplicação em Production. Não é mantido um ambiente Preview nem um banco adicional para ele. Essa escolha reduz a configuração do MVP; problemas específicos da hospedagem podem aparecer somente após o merge, por isso a publicação precisa ser conferida a cada entrega.

A regra controla os deploys automáticos disparados pela integração Git. Ela não apaga publicações antigas nem impede deploys manuais. Ao enviar essa configuração, confirme que o push da branch de trabalho não gera um novo Preview; após o merge, confira o deploy da `main` em Production e a resposta da saúde. Essas verificações na plataforma não são substituídas pela validação local do JSON.

## Cadastro de destinos no admin

O aplicativo `webhooks` possui o modelo `WebhookEndpoint`. Após aplicar a migration `webhooks/0001_initial`, cada registro guarda:

| Campo | Finalidade |
| --- | --- |
| `id` | UUID gerado pela aplicação, sem edição no formulário. |
| `url` | URL HTTPS autorizada do receptor, limitada a 2048 bytes em UTF-8. |
| `event_type` | Tipo de evento com até 100 caracteres, como `pedido.pago`. |
| `created_at` | Data e hora do cadastro, preenchidas automaticamente. |

Uma mesma URL pode atender a tipos de evento diferentes. A combinação `url` + `event_type` é única, com restrição no PostgreSQL. O tipo deve ter duas ou mais partes separadas por pontos; cada parte começa com uma letra minúscula ASCII e pode conter letras minúsculas, números e `_`.

O operador define URLs exatas permitidas em `WEBHOOK_ALLOWED_URLS`, separadas por vírgulas. Vazio ou ausente bloqueia novos cadastros. No `.env` local, um exemplo fictício para explorar somente o cadastro é:

```ini
WEBHOOK_ALLOWED_URLS=https://receiver.example/webhook
```

`receiver.example` é ilustrativo, não um receptor funcional. Para uso real, configure somente receptores que você controla ou tem autorização para usar. Os valores são URLs completas, não domínios ou padrões: uma URL permitida não autoriza outros caminhos, subdomínios ou variações. Uma vírgula literal na URL deve ser codificada como `%2C` para não ser interpretada como separador da configuração.

A validação exige HTTPS e porta padrão (`443`, explícita ou implícita), sem credenciais, query string ou fragmento. Rejeita nomes locais/internos conhecidos e IPs literais não públicos em IPv4/IPv6, mesmo que apareçam na lista permitida. O formulário do admin assume HTTPS se o protocolo for omitido; o valor final ainda precisa corresponder exatamente à lista.

O cadastro não resolve DNS nem faz chamada de teste ao receptor. Essas regras não constituem proteção completa contra SSRF: DNS e destino efetivo da conexão precisarão ser verificados novamente antes de existir envio real. Retirar uma URL da configuração não apaga registros existentes. [Referência de segurança OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).

Com o `.env` preparado, inicialize os serviços e aplique as migrations locais:

```bash
docker compose up -d --build --wait --wait-timeout 60
docker compose exec api python manage.py migrate
```

Para criar seu acesso administrativo local, use o comando interativo oficial:

```bash
docker compose exec api python manage.py createsuperuser
```

Escolha credenciais próprias e não as versione nem reutilize na hospedagem. Abra `http://127.0.0.1:8000/admin/`, faça login e entre em **Webhooks → Destinos de webhook → Adicionar**. A lista fica em `/admin/webhooks/webhookendpoint/` e o formulário em `/admin/webhooks/webhookendpoint/add/`. Preencha a URL autorizada e `pedido.pago` e salve; o registro aparecerá na lista, com UUID e data. Não há disparo de evento ou envio HTTP nesse processo.

O admin exige usuário ativo da equipe (`is_staff`) e a permissão correspondente: `add_webhookendpoint` para cadastrar e `view_webhookendpoint` para consultar. Um superusuário tem essas permissões; um usuário comum ou da equipe sem a permissão necessária não ganha acesso. Edição e exclusão ficam bloqueadas no admin, inclusive para superusuários; não há ações em lote. Listagem, pesquisa, filtro por tipo e detalhe para consulta estão disponíveis.

O formulário usa as validações do modelo. Em scripts que escrevam diretamente pelo ORM, chame `full_clean()` antes de `save()`; o Django não faz essa validação automaticamente no `save()`. A restrição de duplicidade continua sendo aplicada pelo banco. Não há endpoint REST de cadastro nem política geral de acesso definida para as futuras operações de eventos/envio. [Validação de modelos no Django](https://docs.djangoproject.com/en/5.2/ref/models/instances/#validating-objects).

Na Vercel, aplique a migration no banco Neon e configure `WEBHOOK_ALLOWED_URLS` em Production antes de usar o cadastro. A configuração local e a criação de um superusuário local não alteram o ambiente hospedado. O fluxo de publicação automática usa somente a `main`; não há banco de Preview neste projeto.

## Imagem Docker para desenvolvimento

O `Dockerfile` descreve uma imagem local com Python 3.14, código da aplicação e dependências de `requirements-dev.txt`, incluindo as ferramentas de testes. A base é a imagem oficial `python:3.14-slim-bookworm`, com Debian e Python; `slim` indica uma variante menor, com menos pacotes do sistema. Não é necessário copiar a `.venv` da máquina.

Na raiz do repositório, com Docker disponível, construa a imagem:

```bash
docker build -t webhook-delivery-api:dev .
```

`-t` dá nome e etiqueta à imagem; `.` indica a pasta atual como origem dos arquivos da construção. O Docker lê o `Dockerfile` e aplica o `.dockerignore` antes de enviar esses arquivos à construção. A primeira execução precisa baixar a imagem base e as dependências.

O `.dockerignore` exclui `.env`, `.env.neon`, `.venv/`, `docs/`, histórico Git e arquivos gerados. `.env.example` continua permitido por conter somente exemplos públicos. Configurações e credenciais reais deverão ser fornecidas ao executar o container, sem serem gravadas na imagem. O `.gitignore` e o `.dockerignore` controlam ferramentas diferentes; ignorar um arquivo no Git não o exclui automaticamente da construção Docker.

Para conferir o Python e as dependências instaladas, execute containers temporários:

```bash
docker run --rm webhook-delivery-api:dev python --version
docker run --rm webhook-delivery-api:dev python -m pip check
```

Esses comandos substituem o comando padrão da imagem e não carregam as configurações do Django nem consultam o banco. `--rm` remove o container ao terminar, preservando a imagem. Construção e verificações devem ser executadas no ambiente local; a presença dos arquivos no repositório não comprova sua execução.

O comando padrão da imagem é o servidor de desenvolvimento Django, executado pelo usuário `app` sem privilégios de administrador. A pasta de trabalho `/app` também pertence a esse usuário, permitindo criar o cache do pytest durante os testes. Ele escuta em `0.0.0.0:8000` dentro do container; isso não publica uma porta na máquina por si só. Não é um servidor para produção. Construir a imagem não inicia a API. A publicação permanece no suporte Django da Vercel com PostgreSQL no Neon.

## API e PostgreSQL pelo Docker Compose

O `compose.yaml` define dois serviços locais: `api`, construído pelo `Dockerfile`, e `db`, com PostgreSQL 17. Antes de iniciar, prepare o `.env` local conforme a seção abaixo, com credenciais próprias, `DJANGO_DEBUG=True`, hosts `127.0.0.1,localhost` e `DJANGO_SECURE_COOKIES=False` para HTTP local.

O Compose fornece as variáveis do `.env` ao container da API por `env_file`. O arquivo não é copiado para a imagem; os valores são fornecidos na execução. Dentro da API, o Compose substitui `POSTGRES_HOST` por `db`, `POSTGRES_PORT` por `5432` e `POSTGRES_SSLMODE` por `disable`, pois esse banco local não configura TLS. A `.env.neon` não participa dessa execução. Para esse fluxo não é necessário ativar a `.venv` nem executar `source .env`; use as configurações locais, sem exportações do Neon no terminal.

Os containers usam a rede criada pelo Compose. O Django encontra o PostgreSQL pelo nome do serviço, `db`; `127.0.0.1` dentro do container da API apontaria para a própria API. A porta interna do banco continua `5432`, mesmo que sua porta publicada na máquina seja outra. `depends_on` com `service_healthy` aguarda o healthcheck do PostgreSQL antes de iniciar a API; não executa migrations nem garante disponibilidade contínua.

Na raiz do projeto, valide a configuração sem exibir seus valores e inicie os serviços:

```bash
docker compose config -q
docker compose up -d --build --wait --wait-timeout 60
docker compose ps
```

Se estiver usando o servidor Django na `.venv`, encerre-o antes com `Ctrl+C` para liberar a porta `8000`. A API é publicada somente em `127.0.0.1:8000`. Acesse `http://127.0.0.1:8000/api/saude/` no navegador ou no Bruno API Client com `GET`, sem autenticação ou corpo; espere `200` e `{"status": "ok"}`. O banco usa o volume `postgres_data` já definido; os comandos acima não o apagam. A opção `--wait` verifica o healthcheck do banco e a execução do container da API, que ainda não possui healthcheck próprio; confirme a resposta HTTP separadamente.

Consulte os logs e execute os comandos Django dentro do serviço `api`:

```bash
docker compose logs --tail=50 api
docker compose exec api python manage.py check
docker compose exec api python manage.py showmigrations
docker compose exec api python manage.py migrate
docker compose exec api python -m pytest -q
```

`exec api` executa um comando no container da API que já está em execução. `migrate` aplica somente migrations pendentes no PostgreSQL local; não migra o Neon. Os testes atuais de saúde continuam sem acessar o banco. Para parar os serviços e remover os containers preservando os dados, use `docker compose down`.

O código desta etapa fica copiado na imagem, sem montagem da pasta do projeto. Depois de alterar código, testes, dependências ou Dockerfile, repita `docker compose up -d --build --wait --wait-timeout 60`. Depois de mudar somente o `.env`, use `docker compose up -d --wait --wait-timeout 60` para o Compose atualizar o container. Arquivos privados do projeto não são montados dentro da API.

## Preparação local da base e alternativa com .venv

A base foi gerada com Python 3.14 e Django 5.2.18. O banco local usa a imagem oficial PostgreSQL 17. Tenha Docker com Compose disponível e execute os comandos na raiz do repositório.

`.python-version`, na raiz, declara Python 3.14 para a Vercel usar essa mesma versão principal e secundária na publicação. O arquivo não instala Python nem modifica uma `.venv` existente; confira a versão local com `python --version` após ativar o ambiente. [Configuração oficial da versão Python na Vercel](https://vercel.com/docs/functions/runtimes/python/python-version).

Crie e ative o ambiente virtual, depois instale as dependências de desenvolvimento:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

`requirements.txt` declara Django, DRF e psycopg. `requirements-dev.txt` inclui esse arquivo e acrescenta pytest e pytest-django. Para executar a aplicação sem ferramentas de testes, instale somente `requirements.txt`.

O arquivo `.env.example` mostra as variáveis de configuração, sem segredos reais. Para criar um `.env` local com uma chave aleatória, execute uma única vez, quando o arquivo ainda não existir:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print('DJANGO_SECRET_KEY=' + repr(get_random_secret_key()))" > .env
```

O comando grava a chave no `.env`, que fica fora do Git. Não compartilhe esse arquivo. A chave usada na hospedagem será gerada separadamente.

Acrescente também estas opções ao `.env`, preservando as linhas existentes:

```ini
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DJANGO_SECURE_COOKIES=False
```

`DJANGO_DEBUG` ativa o modo de desenvolvimento somente com o texto exato `True`; sem a variável, o modo fica desativado. Na hospedagem, use `False`, pois páginas detalhadas de erro podem expor informações internas. `DJANGO_ALLOWED_HOSTS` contém os nomes de domínio ou IPs pelos quais esta aplicação aceita requisições, separados por vírgulas e sem protocolo, porta ou caminho. Sem a variável, a lista fica vazia. Na hospedagem, configure os domínios específicos da aplicação; não use `*`. Essa lista não autoriza destinos de webhooks.

`DJANGO_SECURE_COOKIES` controla `SESSION_COOKIE_SECURE` e `CSRF_COOKIE_SECURE`. Por padrão, ambos ficam ativados: os cookies recebem o atributo `Secure`, restringindo seu envio a HTTPS. Somente o texto exato `False` desativa essa opção, como no desenvolvimento local por HTTP. Na hospedagem HTTPS, mantenha `True` ou omita a variável. A escolha é independente de `DEBUG`; não copie a opção local `False` para a Vercel.

Acrescente ao `.env` as variáveis de PostgreSQL indicadas em `.env.example`, preservando sua chave Django. Substitua a senha de exemplo por uma senha local própria antes de iniciar o banco. `POSTGRES_HOST=127.0.0.1` é o endereço usado pelo Django que roda na máquina; `db` é o nome do serviço dentro do Compose.

Inclua `POSTGRES_SSLMODE=disable` para conectar ao PostgreSQL local deste Compose, que não tem SSL/TLS configurado. A opção controla a conexão Django–PostgreSQL. Sem a variável, a aplicação usa `require`, que exige uma conexão criptografada; use esse valor para o Neon, cuja conexão usa TLS. SSL é o antecessor do TLS; o nome `sslmode` foi mantido na configuração do PostgreSQL. Não use `disable` para o banco hospedado. Essa opção não configura o HTTPS da API nem das entregas de webhooks.

Para gerar essa senha local:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copie o resultado para `POSTGRES_PASSWORD` no seu `.env`, mantendo as aspas simples. Gere e configure a senha antes da primeira inicialização do banco.

Em cada terminal novo, ative a `.venv` e carregue as variáveis locais:

```bash
source .venv/bin/activate
set -a
source .env
set +a
```

Carregue somente seu próprio `.env`: `source` interpreta seu conteúdo como comandos do shell. O Django lê `DJANGO_SECRET_KEY` do ambiente; ele não carrega o arquivo automaticamente. Sem essa variável, a inicialização falha com `KeyError: 'DJANGO_SECRET_KEY'`.

O Compose também lê o `.env` da raiz para preencher sua configuração. Para executar Django na `.venv` como alternativa ao container, inicie somente o banco e aguarde a verificação de disponibilidade:

```bash
docker compose up -d --wait --wait-timeout 60 db
docker compose ps
```

A porta do banco é publicada apenas em `127.0.0.1`. Se a porta 5432 estiver ocupada por outro projeto, escolha outra em `POSTGRES_PORT` no `.env`, recarregue o ambiente e repita a inicialização. A porta dentro do container permanece 5432.

Verifique o Django e aplique as migrations padrão:

```bash
python manage.py check
python manage.py migrate
python manage.py showmigrations
```

`check` verifica a configuração; `migrate` conecta ao PostgreSQL e aplica as migrations, incluindo a tabela de destinos; `showmigrations` lista quais migrations estão aplicadas, com `[X]`. Ainda não existem tabelas de eventos ou tentativas de entrega.

Os dados ficam no volume `postgres_data`, gerenciado pelo Docker e separado dos arquivos versionados. Para parar e remover os containers preservando o volume:

```bash
docker compose down
```

As credenciais e o banco definidos na imagem oficial são criados na primeira inicialização de um volume vazio. Alterar a senha no `.env` depois não altera a senha de um banco existente. A conta inicial tem privilégios de administrador para este ambiente local; a hospedagem terá configuração própria.

## Preparação do banco hospedado no Neon

Crie um projeto no Neon Free com PostgreSQL 17. A preparação foi feita na região AWS US East (N. Virginia). No painel, abra **Connect**, selecione o banco e o **Role** (usuário do PostgreSQL) da branch desejada e desative **Connection pooling** para obter a conexão direta usada nesta etapa. [Instruções oficiais de conexão](https://neon.com/docs/connect/connect-from-any-app).

Mantenha o `.env` local e crie um arquivo separado `.env.neon`, contendo somente as opções do banco hospedado:

```ini
POSTGRES_DB='nome-do-seu-banco'
POSTGRES_USER='usuario-do-seu-banco'
POSTGRES_PASSWORD='senha-do-seu-banco'
POSTGRES_HOST='host-direto-do-seu-banco'
POSTGRES_PORT=5432
POSTGRES_SSLMODE=require
```

Substitua os exemplos pelos valores exatos do painel. Hífen e sublinhado não são equivalentes no nome do usuário. O host deve ficar em uma linha, sem protocolo, porta, caminho ou parâmetros da URL. A senha é a do usuário do banco, não a da conta no site Neon. `.env.neon` é ignorado pelo Git e não deve ser compartilhado.

Na raiz do projeto, com a `.venv` ativa e o `.env` local já preparado, consulte o banco, aplique as migrations e confira o resultado:

```bash
(
    set -e
    set -a
    source .env
    source .env.neon
    set +a
    POSTGRES_SSLMODE=require python manage.py showmigrations
    POSTGRES_SSLMODE=require python manage.py migrate
    POSTGRES_SSLMODE=require python manage.py showmigrations
)
```

O `.env` fornece a chave e as opções do Django; `.env.neon` sobrescreve as opções do banco nesse bloco. Os parênteses criam um subshell: ao terminar, essas substituições não permanecem no terminal principal. `set -e` interrompe o bloco se houver falha. Carregue somente arquivos que você próprio preparou, pois `source` interpreta seu conteúdo como comandos do shell.

Em um banco novo, as migrations aparecem com `[ ]` antes da aplicação e `[X]` depois. Cada banco mantém seu próprio registro; migrar localmente não migra o Neon. Os mesmos arquivos de migration são aplicados em cada ambiente, e `migrate` executa apenas os pendentes. A aplicação de migrations no Neon é manual nesta etapa; ainda não há automação de CI/CD.

Use `.env` para desenvolvimento, Compose e testes locais. A publicação na Vercel usa uma chave Django própria, diferente da local. O sucesso dessa preparação verifica a conexão do terminal ao banco hospedado e suas migrations; não verifica a conexão da execução na Vercel ao Neon.

## Execução local e rota de saúde

Com a `.venv` ativa, as variáveis do `.env` carregadas e o PostgreSQL iniciado, execute:

```bash
python manage.py check
python manage.py runserver 127.0.0.1:8000
```

No Bruno API Client, ferramenta para testar APIs, faça esta requisição:

- Método: `GET`.
- URL: `http://127.0.0.1:8000/api/saude/`.
- Autenticação: nenhuma.
- Header: `Accept: application/json`.
- Corpo: nenhum.
- Resposta: HTTP `200` com `{"status": "ok"}`.

Essa rota confirma que a aplicação atende à requisição. Não consulta o banco, não grava dados e não faz chamadas externas; não verifica a disponibilidade do PostgreSQL nem a capacidade de entregar webhooks. A view de saúde é deliberadamente pública e desabilita a autenticação. Isso não define a política de acesso dos futuros endpoints de webhooks, que ainda será implementada. Um `POST` para a mesma URL retorna HTTP `405`, com a explicação no campo `detail`.

O servidor desse comando é destinado ao desenvolvimento local. Encerre com `Ctrl+C`.

## HTTPS e cookies na publicação

A Vercel fornece o redirecionamento HTTP para HTTPS e o HSTS do domínio `.vercel.app`. Foram observados o status `308` com `Location` apontando à mesma rota de saúde em HTTPS e o header `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`. Esses controles ficam na hospedagem; não são ativados novamente no Django nesta configuração. [Documentação da Vercel](https://vercel.com/docs/cdn-security/encryption).

O Django configura os atributos dos cookies de sessão e CSRF conforme `DJANGO_SECURE_COOKIES`. A configuração foi publicada na PR #3 e o atributo `Secure` do cookie CSRF foi observado na resposta do login do admin. O cookie de sessão será verificado em acesso autenticado; ainda não foi observado. A configuração não cria um login próprio da API nem muda o acesso público à saúde.

O comando `python manage.py check --deploy` verifica configurações locais do Django. Os avisos `security.W004` e `security.W008` podem continuar porque ele não inspeciona o HSTS e o redirecionamento fornecidos pela Vercel. Nesta hospedagem, esses comportamentos devem ser verificados nas respostas públicas. Ao trocar de hospedagem, reavalie onde esses controles serão aplicados. Não há avisos silenciados nesta configuração.

## Arquivos estáticos

`STATIC_URL = '/static/'` define o caminho de acesso aos arquivos estáticos, como CSS, JavaScript e imagens. `STATIC_ROOT` aponta para `staticfiles/`, na raiz, onde o comando oficial `collectstatic` reúne cópias dos arquivos das aplicações instaladas, incluindo Django admin e DRF. Essa pasta é gerada e ignorada pelo Git; os arquivos de origem permanecem nas aplicações.

Na raiz do projeto, com a `.venv` ativa, carregue a configuração local e verifique a coleta:

```bash
set -a
source .env
set +a
python manage.py check
python manage.py collectstatic --noinput
```

O comando deve informar a quantidade de arquivos coletados ou já presentes. Confira, por exemplo, `staticfiles/admin/css/base.css` e `staticfiles/rest_framework/css/bootstrap.min.css`. Essa preparação não consulta o banco nem aplica migrations.

Com `STATIC_ROOT` configurado, a Vercel executa a coleta automaticamente durante a preparação da publicação e distribui os arquivos em `STATIC_URL`. [Suporte oficial a arquivos estáticos do Django](https://vercel.com/docs/frameworks/full-stack/django#serving-static-assets). A coleta local foi verificada, os logs de build confirmam a execução de `collectstatic` e as páginas publicadas do DRF e do admin foram visualizadas com estilos. As respostas HTTP dos arquivos individuais ainda não foram inspecionadas.

## Ferramentas de testes

`pytest.ini` indica `config.settings` para inicializar Django e procura arquivos chamados `test_*.py`. `tests/test_health.py` verifica a saúde pública, a rejeição de POST e, com `DEBUG=False`, a aceitação do host permitido e a rejeição de outro com `400`. Esse erro de host é produzido pelo Django e não precisa ter corpo JSON.

`tests/test_webhook_endpoints.py` cobre URLs permitidas/proibidas, componentes inseguros, IPs locais, formato do tipo, campos obrigatórios, persistência e duplicidade, inclusive a restrição no PostgreSQL. `tests/test_webhook_admin.py` cobre cadastro e consulta pelo operador, rejeição de entradas inválidas, acesso por perfil/permissão, proteção CSRF e bloqueio de edição/exclusão. Os testes de autorização usam sessões criadas por `force_login` para isolar as permissões; isso não simula digitação de senha ou verifica login/cookies na hospedagem.

Após instalar as dependências de desenvolvimento, confirme a instalação:

```bash
python -m pip check
python -m pytest --version
```

Execute com a `.venv` ativa e as variáveis do `.env` carregadas:

```bash
python -m pytest -q
```

Os testes usam o cliente interno do Django, sem servidor HTTP externo. A saúde e as validações isoladas não acessam o banco; persistência e admin usam `pytest-django` em um banco separado, com prefixo `test_`, no PostgreSQL local. Execute-os com configurações locais, nunca apontando a suíte ao Neon. Os receptores fictícios são valores de teste: nenhum HTTP ou DNS externo é executado para validá-los. Nenhum teste encontrado (código 5) não significa aprovação.

Os exemplos dos endpoints de webhooks serão acrescentados conforme a implementação avançar.

## Licença

[MIT](LICENSE).
