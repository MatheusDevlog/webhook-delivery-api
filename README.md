# Webhook Delivery Service

Projeto de backend para registrar eventos, enviar webhooks a destinos autorizados e consultar o resultado das tentativas de entrega.

## Estado atual

A estrutura padrão do Django foi gerada e configurada para PostgreSQL local em Docker Compose. O Django REST Framework está registrado e as ferramentas de testes estão declaradas. Os endpoints de webhooks e os testes ainda não foram implementados; a demonstração ainda não foi publicada.

O MVP planejado terá uma tentativa síncrona por evento, registro do resultado HTTP e consulta do estado. A demonstração usará dados fictícios e receptores controlados.

## Stack planejada

- Python, Django e Django REST Framework.
- PostgreSQL, com Docker Compose no desenvolvimento local.
- pytest e pytest-django para testes.
- Vercel Hobby para a aplicação e Neon Free para o banco da demonstração.

## Preparação local da base

A base foi gerada com Python 3.14 e Django 5.2.18. O banco local usa a imagem oficial PostgreSQL 17. Tenha Docker com Compose disponível e execute os comandos na raiz do repositório.

Crie e ative o ambiente virtual, depois instale as dependências de desenvolvimento:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

`requirements.txt` declara Django, DRF e psycopg. `requirements-dev.txt` inclui esse arquivo e acrescenta pytest e pytest-django. Para executar a aplicação sem ferramentas de testes, instale somente `requirements.txt`.

O arquivo `.env.example` mostra a variável necessária, sem uma chave real. Para criar um `.env` local com uma chave aleatória, execute uma única vez, quando o arquivo ainda não existir:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print('DJANGO_SECRET_KEY=' + repr(get_random_secret_key()))" > .env
```

O comando grava a chave no `.env`, que fica fora do Git. Não compartilhe esse arquivo. A chave usada na hospedagem será gerada separadamente.

Acrescente ao `.env` as variáveis de PostgreSQL indicadas em `.env.example`, preservando sua chave Django. Substitua a senha de exemplo por uma senha local própria antes de iniciar o banco. `POSTGRES_HOST=127.0.0.1` é o endereço usado pelo Django que roda na máquina; `db` é o nome do serviço dentro do Compose.

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

`check` verifica a configuração; `migrate` conecta ao PostgreSQL e cria as tabelas dos componentes padrão do Django; `showmigrations` lista quais migrations estão aplicadas, com `[X]`. Ainda não há tabelas de webhooks nem testes de comportamento. `DEBUG = True` é a configuração atual para desenvolvimento local e precisa ser ajustada antes da publicação.

Os dados ficam no volume `postgres_data`, gerenciado pelo Docker e separado dos arquivos versionados. Para parar e remover os containers preservando o volume:

```bash
docker compose down
```

As credenciais e o banco definidos na imagem oficial são criados na primeira inicialização de um volume vazio. Alterar a senha no `.env` depois não altera a senha de um banco existente. A conta inicial tem privilégios de administrador para este ambiente local; a hospedagem terá configuração própria.

## Ferramentas de testes

`pytest.ini` indica `config.settings` para inicializar Django e procura arquivos chamados `test_*.py`. Os testes serão escritos conforme os comportamentos da API forem implementados.

Após instalar as dependências de desenvolvimento, confirme a instalação:

```bash
python -m pip check
python -m pytest --version
```

Quando existirem testes, execute com a `.venv` ativa, as variáveis do `.env` carregadas e o PostgreSQL disponível:

```bash
python -m pytest -q
```

Ainda não há testes para executar; nesse estado, pytest informa que nenhum teste foi encontrado e encerra com código 5. Isso não é uma suíte aprovada. Nos testes que acessarem dados, pytest-django usará um banco de testes separado, criado no PostgreSQL local.

As instruções para iniciar a API e seus exemplos de uso serão acrescentados conforme a implementação avançar.

## Licença

[MIT](LICENSE).
