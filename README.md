# Webhook Delivery Service

Projeto de backend para registrar eventos, enviar webhooks a destinos autorizados e consultar o resultado das tentativas de entrega.

## Estado atual

A estrutura padrão do Django foi gerada e configurada para PostgreSQL local em Docker Compose. O Django REST Framework está registrado e existe uma rota de saúde com testes automatizados. A base está publicada na Vercel; os endpoints de webhooks e a demonstração interativa ainda não foram implementados.

A conexão ao PostgreSQL no Neon foi verificada pelo terminal com criptografia exigida e as migrations padrão do Django foram aplicadas nesse banco. A conexão da aplicação executada na Vercel ao Neon ainda precisa ser verificada. A preparação do banco hospedado está documentada abaixo.

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

A publicação usa variáveis configuradas na Vercel em **Production**: chave Django própria, `DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS=webhook-delivery-api.vercel.app` e as opções `POSTGRES_*` do Neon, com `POSTGRES_SSLMODE=require`. Senhas e chaves não são versionadas. O ambiente **Preview** ainda não foi preparado.

A revisão das configurações de segurança da publicação e a verificação da conexão Vercel–Neon estão em andamento. O sucesso da saúde não conclui essas verificações.

## Preparação local da base

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

O Compose também lê o `.env` da raiz para preencher sua configuração. Inicie o banco e aguarde a verificação de disponibilidade:

```bash
docker compose up -d --wait --wait-timeout 60
docker compose ps
```

A porta do banco é publicada apenas em `127.0.0.1`. Se a porta 5432 estiver ocupada por outro projeto, escolha outra em `POSTGRES_PORT` no `.env`, recarregue o ambiente e repita a inicialização. A porta dentro do container permanece 5432.

Verifique o Django e aplique as migrations padrão:

```bash
python manage.py check
python manage.py migrate
python manage.py showmigrations
```

`check` verifica a configuração; `migrate` conecta ao PostgreSQL e cria as tabelas dos componentes padrão do Django; `showmigrations` lista quais migrations estão aplicadas, com `[X]`. Ainda não há tabelas de webhooks; os testes de comportamento atuais cobrem somente a rota de saúde.

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

O Django configura os atributos dos cookies de sessão e CSRF conforme `DJANGO_SECURE_COOKIES`. Essa alteração passa a valer na Vercel quando o código atualizado for publicado; a verificação dos cookies na publicação ainda está pendente. A configuração não cria um login próprio da API nem muda o acesso público à saúde.

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

Com `STATIC_ROOT` configurado, a Vercel executa a coleta automaticamente durante a preparação da publicação e distribui os arquivos em `STATIC_URL`. [Suporte oficial a arquivos estáticos do Django](https://vercel.com/docs/frameworks/full-stack/django#serving-static-assets). A coleta local foi verificada e a página publicada do DRF foi visualizada com estilos. A inspeção dos logs da coleta e das respostas HTTP dos arquivos individuais ainda está pendente.

## Ferramentas de testes

`pytest.ini` indica `config.settings` para inicializar Django e procura arquivos chamados `test_*.py`. `tests/test_health.py` verifica GET sem autenticação, resposta JSON e rejeição de POST na rota de saúde. Também verifica, com `DEBUG = False` e uma lista explícita de hosts, a aceitação de um host autorizado e a rejeição de outro com HTTP `400`. Essa rejeição é feita pelo Django e não precisa ter corpo JSON, mesmo quando a requisição pede JSON. Novos testes serão escritos conforme os comportamentos da API forem implementados.

Após instalar as dependências de desenvolvimento, confirme a instalação:

```bash
python -m pip check
python -m pytest --version
```

Execute com a `.venv` ativa e as variáveis do `.env` carregadas:

```bash
python -m pytest -q
```

Os testes atuais usam o cliente de testes do Django, sem servidor HTTP ativo e sem acesso ao banco. pytest-django bloqueia acesso ao banco nesses testes, que não solicitam essa permissão. Quando forem acrescentados testes que usam dados, mantenha PostgreSQL disponível: pytest-django usará um banco de testes separado, criado no PostgreSQL local. Nenhum teste encontrado (código 5) não significa aprovação.

Os exemplos dos endpoints de webhooks serão acrescentados conforme a implementação avançar.

## Licença

[MIT](LICENSE).
