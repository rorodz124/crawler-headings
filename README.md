# Auditor de Headings em Websites

![Python](https://img.shields.io/badge/Python-3.12-blue)
![Flask](https://img.shields.io/badge/Flask-API-lightgrey)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED)

Ferramenta para fazer crawl a websites, extrair a estrutura de headings (`h1`–`h6`) e validá-la segundo regras de acessibilidade e SEO — detetando problemas como H1 em falta, múltiplos H1, headings vazios e saltos de hierarquia.

## Como Iniciar

Clone o repositório:

```bash
git clone https://github.com/rorodz124/crawler-headings.git
cd crawler-headings
```

Inicia com Docker:

```bash
docker compose up -d
```

A aplicação fica disponível em **http://localhost:5001**

## Utilização

A interface web permite submeter tarefas de crawling e auditoria de headings. Existem 2 modos de análise:

- **Página** — analisa os headings de uma única página web
- **Site** — crawl completo a um domínio, auditando todas as páginas encontradas

Os resultados são guardados como relatórios HTML acessível na interface, e o histórico de execuções é persistido numa base de dados SQLite.

## Stack

- **Backend:** Python + Flask
- **Crawling e extração:** Playwright (Chromium headless)
- **Validação de headings:** Lógica própria (regras de hierarquia e acessibilidade)
- **Base de dados:** SQLite
- **Frontend:** HTML + JavaScript (sem framework)

## API Endpoints

### Tarefas

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/api/jobs` | Lista todas as tarefas |
| `GET` | `/api/jobs/<job_id>` | Detalhes e progresso de uma tarefa |
| `DELETE` | `/api/jobs/<job_id>` | Remove uma tarefa |
| `POST` | `/api/jobs/<job_id>/cancel` | Cancela uma tarefa em execução |
| `POST` | `/api/jobs/headings/pagina` | Audita os headings de uma página |
| `POST` | `/api/jobs/headings/site` | Crawl e auditoria completa a um site |

### Relatórios

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/api/relatorios` | Lista os relatórios gerados |
| `GET` | `/relatorios/<nome>` | Abre um relatório HTML |
| `PATCH` | `/api/relatorios/<nome>` | Renomeia um relatório |
| `DELETE` | `/api/relatorios/<nome>` | Apaga um relatório |
| `DELETE` | `/api/relatorios` | Apaga todos os relatórios |

### Histórico

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/api/historico` | Lista o histórico de execuções |
| `GET` | `/api/historico/<run_id>` | Detalhes de uma execução |
| `DELETE` | `/api/historico/<run_id>` | Remove uma execução do histórico |
| `DELETE` | `/api/historico` | Limpa todo o histórico |

### Sistema

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/api/health` | Estado do servidor |