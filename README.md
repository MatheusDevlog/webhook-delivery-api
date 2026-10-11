# Webhook Delivery Service

Projeto de backend para registrar eventos, enviar webhooks a destinos autorizados e consultar o resultado das tentativas de entrega.

## Estado atual

A estrutura padrão do Django foi gerada. Os endpoints de webhooks e os testes ainda não foram implementados; a demonstração ainda não foi publicada.

O MVP planejado terá uma tentativa síncrona por evento, registro do resultado HTTP e consulta do estado. A demonstração usará dados fictícios e receptores controlados.

## Stack planejada

- Python, Django e Django REST Framework.
- PostgreSQL, com Docker Compose no desenvolvimento local.
- pytest e pytest-django para testes.
- Vercel Hobby para a aplicação e Neon Free para o banco da demonstração.

## Preparação local da base

A base foi gerada com Python 3.14 e Django 5.2.18. Execute os comandos na raiz do repositório.

Crie e ative o ambiente virtual, depois instale as dependências:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

O arquivo `.env.example` mostra a variável necessária, sem uma chave real. Para criar um `.env` local com uma chave aleatória, execute uma única vez, quando o arquivo ainda não existir:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print('DJANGO_SECRET_KEY=' + repr(get_random_secret_key()))" > .env
```

O comando grava a chave no `.env`, que fica fora do Git. Não compartilhe esse arquivo. A chave usada na hospedagem será gerada separadamente.

Em cada terminal novo, ative a `.venv` e carregue as variáveis locais:

```bash
source .venv/bin/activate
set -a
source .env
set +a
python manage.py check
```

Carregue somente seu próprio `.env`: `source` interpreta seu conteúdo como comandos do shell. O Django lê `DJANGO_SECRET_KEY` do ambiente; ele não carrega o arquivo automaticamente. Sem essa variável, a inicialização falha com `KeyError: 'DJANGO_SECRET_KEY'`.

`manage.py check` verifica a configuração, sem substituir testes de comportamento. PostgreSQL ainda será configurado. O esqueleto mantém temporariamente a configuração padrão de SQLite; não execute `migrate` nesta etapa. `DEBUG = True` é a configuração atual para desenvolvimento local e precisa ser ajustada antes da publicação.

As instruções para iniciar a API e seus exemplos de uso serão acrescentados conforme a implementação avançar.

## Licença

[MIT](LICENSE).
