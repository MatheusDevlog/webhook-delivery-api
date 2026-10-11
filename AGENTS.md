# Guia técnico para agentes de desenvolvimento

## Fontes e escopo

- Este repositório inicia o Webhook Delivery Service, terceiro projeto de portfólio. A estrutura padrão do Django foi gerada em manage.py e config/; ainda não há endpoints de webhooks, testes ou MVP concluído. Confira os arquivos antes de afirmar o que existe.
- O README.md é a referência pública do comportamento implementado e dos comandos de execução. Planos não são funcionalidades entregues.
- Consulte docs/CONTEXTO_DESENVOLVIMENTO.md para preferências privadas e docs/ESTADO_WEBHOOK_DELIVERY.md para o estado recente, quando disponíveis. Não copie esses documentos para commits ou PRs.
- Inspecione o código e o estado do Git antes de presumir branches, testes, configuração ou publicação. Mantenha mudanças pequenas e relacionadas ao pedido.

## Direção técnica planejada

- Base atual: Python 3.14 e Django 5.2, com dependência declarada em requirements.txt. Django REST Framework e PostgreSQL ainda serão configurados.
- Planeja-se PostgreSQL em Docker Compose, API e testes no Python local da .venv. Ainda não existem serviços configurados nem comandos de execução validados.
- Hospedagem escolhida para a demonstração: Vercel Hobby para Django/DRF e interface simples, Neon Free para PostgreSQL. Validar um deploy mínimo e a conexão ao banco cedo; ainda não houve deploy. Verificar cotas e configuração dos planos gratuitos antes de publicar.
- A demonstração deve executar requisições HTTP reais e persistir resultados, com dados fictícios e receptores controlados. Manter a interface pequena e servir a API e a interface no mesmo projeto quando possível.
- MVP proposto: cadastrar destinos autorizados e seu tipo de evento, registrar um evento para um único destino, solicitar uma tentativa síncrona e consultar estado e tentativa. Não pressupor envio automático, fila ou múltiplos destinatários.
- No envio síncrono, a API responde à solicitação de envio depois de registrar o resultado da tentativa HTTP externa. Registrar um evento não dispara o envio. Uma eventual execução em segundo plano exigirá mecanismo supervisionado e contrato de aceitação/consulta próprio.
- Entidades propostas: WebhookEndpoint, Event e DeliveryAttempt. O contrato HTTP será definido durante a implementação e documentado no README.
- Retry, assinatura HMAC e seu secret, idempotency key, Redis e Celery ficam para evolução. Não adicionar dependências ou infraestrutura apenas por aparecerem no contexto original.

## Convenções de implementação

- Prefira recursos do Django e do DRF. Validação de entrada no serializer, coordenação HTTP na view, persistência nos models e envio externo em funções próprias.
- Use comandos oficiais para estruturas padrão e migrations. Gere migrations quando o esquema mudar e revise antes de aplicar.
- Use inglês claro nos identificadores Python, aspas simples por padrão e português nas mensagens ao usuário e na documentação. Preserve nomes exigidos pelo framework e contratos já publicados.
- Atualize o README quando o contrato HTTP, a configuração ou a execução real mudar. Não documente planos como comportamento disponível.

## Segurança e dados locais

- Desenvolver localmente e preparar uma demonstração pública restrita, com dados fictícios e destinos previamente autorizados pelo operador. Não apresentar a demonstração como serviço de envio para URLs arbitrárias ou plataforma multiusuário de produção.
- Antes da publicação, restringir a administração dos destinos, definir acesso à demonstração, limites de requisições, orçamento global de envios e retenção de dados. O visitante só pode usar receptores controlados; proteger essas regras no backend, não apenas na interface.
- URLs fornecidas por usuários podem causar SSRF: acesso indevido à máquina, à rede interna ou a metadados de nuvem. Validar sintaxe não basta.
- Antes de habilitar envio real, definir lista explícita de destinos confiáveis, HTTPS, política de DNS/IP, bloqueio de redirecionamentos e limites de tempo e dados. Revalidar o destino no envio. Considerar IPv4, IPv6 e mudança de DNS; uma resolução prévia isolada não garante a segurança da conexão seguinte.
- Não aceitar método ou headers arbitrários do usuário, encaminhar credenciais recebidas nem desabilitar a verificação TLS. Falhas externas devem ser registradas sem expor segredos ou detalhes internos.
- Persistir a tentativa antes da chamada externa e seu resultado depois. Evitar transação de banco aberta durante a chamada; impedir duas solicitações concorrentes de iniciar o mesmo evento.
- Timeout não prova que o destinatário deixou de receber o evento. Não prometer entrega exatamente uma vez nem recuperar automaticamente envios de resultado incerto.
- Mantenha docs/, .env, variantes locais de .env e .venv/ fora do versionamento. .env.example deve conter somente exemplos sem segredos.
- A configuração atual lê DJANGO_SECRET_KEY do ambiente e usa DEBUG = True para desenvolvimento local. Carregue o .env no terminal conforme o README; o Django não o lê automaticamente. Ajustar as configurações antes de publicar.

## Verificação

- Planejar pytest e pytest-django com PostgreSQL. O comando previsto é python -m pytest -q, após configurar o ambiente; ainda não foi executado neste projeto.
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
