# Guia técnico para agentes de desenvolvimento

## Fontes e escopo

- Este repositório implementa o Webhook Delivery Service, terceiro projeto de portfólio. Há base Django em manage.py/config, saúde com testes e app webhooks com cadastro/consulta de WebhookEndpoint no admin. Não há endpoints de eventos, envio externo ou MVP concluído. Confira os arquivos antes de afirmar o que existe ou foi publicado.
- O README.md é a referência pública do comportamento implementado e dos comandos de execução. Planos não são funcionalidades entregues.
- GET /api/saude/ é a rota de saúde da aplicação em config/views.py: pública, sem autenticação, retorna status ok e não consulta banco nem serviços externos. Não interpretar seu sucesso como confirmação de disponibilidade do banco ou entrega de webhooks.
- Consulte docs/CONTEXTO_DESENVOLVIMENTO.md para preferências privadas e docs/ESTADO_WEBHOOK_DELIVERY.md para o estado recente, quando disponíveis. Não copie esses documentos para commits ou PRs.
- Inspecione o código e o estado do Git antes de presumir branches, testes, configuração ou publicação. Mantenha mudanças pequenas e relacionadas ao pedido.

## Direção técnica planejada

- Base atual: Python 3.14, Django 5.2, DRF 3.18 e psycopg 3.3, com dependências declaradas em requirements.txt. requirements-dev.txt inclui essas dependências e acrescenta pytest 9 e pytest-django 4; instalação conforme README.
- Dockerfile define imagem de desenvolvimento Python 3.14 com dependências de requirements-dev.txt, usuário app sem privilégios de administrador e comando padrão runserver. A pasta /app deve pertencer ao usuário app para permitir arquivos de execução, como cache do pytest; COPY --chown dos arquivos não substitui o ajuste da pasta de trabalho existente. .dockerignore exclui segredos, docs privados, .venv e arquivos gerados da construção. A imagem foi construída e Python/pip check foram verificados por Matheus; não inferir funcionamento do Django/banco por esses comandos.
- compose.yaml define api e PostgreSQL 17 local, portas publicadas no loopback e volume postgres_data preservado. api lê .env via env_file, sobrescreve POSTGRES_HOST=db, POSTGRES_PORT=5432 e POSTGRES_SSLMODE=disable, e depende de db saudável. O código fica copiado na imagem, sem bind mount; reconstruir depois de alterações. Execução conjunta, saúde HTTP e consulta às migrations pelo serviço api foram verificadas. Django na .venv continua alternativa, iniciando somente db pelo Compose. settings.py lê POSTGRES_* do ambiente e repassa sslmode ao driver, com require por padrão; disable é apenas para este banco local sem TLS, require para Neon. Não presumir serviço iniciado ou migrations aplicadas sem evidência.
- A base está publicada na Vercel Hobby em https://webhook-delivery-api.vercel.app/api/saude/; resposta 200/status ok e interface DRF estilizada foram verificadas no navegador. Logs de build confirmam seleção Python 3.14, Django 5.2.18 e execução de collectstatic com publicação concluída. Neon Free fornece o PostgreSQL hospedado, consultado e migrado pelo terminal. Em 17/10/2026, uma tentativa de login no admin publicado com dados fictícios retornou a mensagem padrão de credenciais inválidas; com o backend de autenticação padrão presente, isso verifica a consulta ao banco pela execução hospedada. Não inferir essa conexão pela saúde, nem interpretar a recusa como login autorizado ou verificação de gravação. A demonstração interativa de webhooks ainda não existe. Revalidar cotas e configuração dos planos gratuitos ao ampliar o uso.
- A demonstração deve executar requisições HTTP reais e persistir resultados, com dados fictícios e receptores controlados. Manter a interface pequena e servir a API e a interface no mesmo projeto quando possível.
- MVP proposto: cadastrar destinos autorizados e seu tipo de evento, registrar um evento para um único destino, solicitar uma tentativa síncrona e consultar estado e tentativa. Não pressupor envio automático, fila ou múltiplos destinatários.
- No envio síncrono, a API responde à solicitação de envio depois de registrar o resultado da tentativa HTTP externa. Registrar um evento não dispara o envio. Uma eventual execução em segundo plano exigirá mecanismo supervisionado e contrato de aceitação/consulta próprio.
- WebhookEndpoint existe: UUID, URL, event_type, created_at e unicidade URL/tipo no banco. Admin permite cadastro/consulta a usuários ativos da equipe com permissões add/view, bloqueando edição/exclusão inclusive para superusuários. Event e DeliveryAttempt continuam propostas; contrato HTTP será definido durante a implementação e documentado no README.
- Retry, assinatura HMAC e seu secret, idempotency key, Redis e Celery ficam para evolução. Não adicionar dependências ou infraestrutura apenas por aparecerem no contexto original.

## Convenções de implementação

- Prefira recursos do Django e do DRF. Validação de entrada no serializer, coordenação HTTP na view, persistência nos models e envio externo em funções próprias.
- Para destinos, webhooks/validators.py centraliza URL e formato do tipo; o admin usa a validação dos campos do modelo. save() não chama full_clean(): escritas diretas pelo ORM devem validar explicitamente. Não expor uma nova entrada sem reutilizar essas regras; a restrição de unicidade no banco não valida a segurança da URL.
- Use comandos oficiais para estruturas padrão e migrations. Gere migrations quando o esquema mudar e revise antes de aplicar.
- STATIC_URL é /static/ e STATIC_ROOT aponta para staticfiles/ na raiz, pasta gerada e ignorada pelo Git. Verificar coleta com manage.py collectstatic --noinput. A Vercel coleta automaticamente quando STATIC_ROOT está definido; verificar entrega HTTP dos arquivos após deploy, sem presumir sucesso pela coleta local.
- Use inglês claro nos identificadores Python, aspas simples por padrão e português nas mensagens ao usuário e na documentação. Preserve nomes exigidos pelo framework e contratos já publicados.
- Atualize o README quando o contrato HTTP, a configuração ou a execução real mudar. Não documente planos como comportamento disponível.

## Segurança e dados locais

- Desenvolver localmente e preparar uma demonstração pública restrita, com dados fictícios e destinos previamente autorizados pelo operador. Não apresentar a demonstração como serviço de envio para URLs arbitrárias ou plataforma multiusuário de produção.
- Antes de expor operações de cadastro ou envio na demonstração, restringir a administração dos destinos, definir acesso, limites de requisições, orçamento global de envios e retenção de dados. O visitante só pode usar receptores controlados; proteger essas regras no backend, não apenas na interface. A saúde pública já publicada não habilita essas operações.
- URLs fornecidas por usuários podem causar SSRF: acesso indevido à máquina, à rede interna ou a metadados de nuvem. Validar sintaxe não basta.
- WEBHOOK_ALLOWED_URLS é uma lista de URLs completas exatas, separadas por vírgulas, fornecida pelo ambiente; vazia bloqueia novos cadastros. Validação exige HTTPS/443, sem credenciais/query/fragmento, e rejeita nomes locais/internos conhecidos e IPs literais não públicos. Cadastro não faz HTTP/DNS; domínio permitido ainda pode resolver para IP interno, portanto não descrever essas regras como proteção completa de envio. Revalidar a configuração/destino antes de futuro despacho, mesmo para registros antigos. Nenhum segredo de assinatura foi criado.
- Antes de habilitar envio real, definir lista explícita de destinos confiáveis, HTTPS, política de DNS/IP, bloqueio de redirecionamentos e limites de tempo e dados. Revalidar o destino no envio. Considerar IPv4, IPv6 e mudança de DNS; uma resolução prévia isolada não garante a segurança da conexão seguinte.
- Não aceitar método ou headers arbitrários do usuário, encaminhar credenciais recebidas nem desabilitar a verificação TLS. Falhas externas devem ser registradas sem expor segredos ou detalhes internos.
- Persistir a tentativa antes da chamada externa e seu resultado depois. Evitar transação de banco aberta durante a chamada; impedir duas solicitações concorrentes de iniciar o mesmo evento.
- Timeout não prova que o destinatário deixou de receber o evento. Não prometer entrega exatamente uma vez nem recuperar automaticamente envios de resultado incerto.
- Mantenha docs/, .env, variantes locais de .env e .venv/ fora do versionamento. .env.example deve conter somente exemplos sem segredos.
- DJANGO_SECURE_COOKIES controla SESSION_COOKIE_SECURE e CSRF_COOKIE_SECURE, independentemente de DEBUG: ativados por padrão, desativados somente pelo texto exato False. .env.example indica False apenas para desenvolvimento local por HTTP; na hospedagem HTTPS usar True ou omitir a variável. PR #3 publicada, saúde/admin 200 e cookie CSRF com Secure observados por Matheus. Cookie de sessão ainda não observado em acesso autenticado; não declarar ambos verificados por ver apenas csrftoken.
- Na Vercel atual, HSTS e redirecionamento HTTP para HTTPS são responsabilidade da plataforma: verificados no Bruno por Strict-Transport-Security e 308/Location. Não adicionar SECURE_SSL_REDIRECT, HSTS ou confiança em headers de proxy apenas para zerar W004/W008 do check --deploy local; esse comando não inspeciona a plataforma. Não silenciar esses avisos indiscriminadamente; reavaliar a decisão ao trocar de hospedagem.
- A configuração lê DJANGO_SECRET_KEY, DJANGO_DEBUG e DJANGO_ALLOWED_HOSTS do ambiente. DEBUG só é ativado pelo texto exato True e fica desativado por padrão; ALLOWED_HOSTS é uma lista separada por vírgulas, vazia por padrão. O .env.example indica True e hosts locais para desenvolvimento. Na publicação, usar False e domínios específicos, sem wildcard; essa lista não autoriza destinos externos de webhooks. O Django não lê .env automaticamente: na .venv, carregue-o no terminal conforme o README; no container, o Compose fornece os valores. Revisão de segurança acompanha cada operação, incluindo cookie de sessão antes de uso autenticado. O DRF não possui política global explícita de permissões neste projeto; definir acesso dos novos endpoints antes de expô-los, sem presumir autenticação por ter o framework instalado.

## Verificação

- pytest.ini define config.settings e descoberta de test_*.py. O comando é python -m pytest -q (ou docker compose exec api python -m pytest -q). Saúde/validações isoladas não usam banco; testes de persistência/admin usam pytest-django com PostgreSQL local em banco separado. force_login nos testes de autorização não comprova login real na hospedagem. Não presumir execução ou aprovação sem evidência; nenhum teste encontrado (código 5) não significa aprovação.
- Acrescente testes de comportamento a cada parte: sucesso, entradas inválidas, recurso inexistente, efeitos no banco, repetição e concorrência no envio, falhas HTTP/rede e restrições de destino.
- Testes automatizados devem simular HTTP externo e DNS, sem depender de serviços públicos nem enviar dados reais. Testes de concorrência devem usar transações reais do PostgreSQL quando necessário.
- manage.py check e verificações de migrations complementam os testes de comportamento.
- Use Bruno API Client para testes manuais, registrando método, URL, autenticação, JSON, status, resposta e efeitos observados.
- Diferencie verificações próprias de resultados relatados pelo usuário. Não declare testes, commits ou operações executadas sem evidência.

## Git e entrega

- main é a branch principal. Faça um primeiro commit de base cedo; depois use branches de trabalho, commits por partes lógicas revisadas e verificadas e PR antes do merge.
- Use português nos nomes de branches e mensagens de commit; prefixos como feat, fix, test, docs e chore podem permanecer em inglês.
- Confira os arquivos preparados antes de cada commit, preserve os arquivos privados e faça push antes da PR. Descreva na PR somente alterações e verificações efetivas.
- Não execute commit, push, merge ou publicação sem autorização aplicável. Respeite a divisão de trabalho combinada com o usuário para comandos e edição de código.
- Analise divergências ou conflitos antes de atualizar branches. Não presuma que o remoto está vazio ou vinculado sem conferir.
