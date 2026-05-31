# Crawler de Validação de Headings

Ferramenta de auditoria automática da estrutura de headings (`h1`…`h6`) em sites institucionais, com foco em acessibilidade e consistência semântica.

Desenvolvido no contexto de estágio como segunda fase de auditoria web, após o projeto de deteção de imagens com tabelas.

## Funcionalidades

- **Auditoria de página única** — valida os headings de um URL específico
- **Auditoria de site inteiro** — faz crawl de todos os links internos e valida cada página
- **Regras validadas:**
  - Existência de `h1` na página
  - Apenas um `h1` por página
  - Headings vazios
  - Saltos de hierarquia (ex.: `h2 → h4`)
  - Início de estrutura demasiado profundo (ex.: começar em `h3`)
- **Interface web** — UI para lançar auditorias, acompanhar progresso em tempo real e gerir relatórios
- **Histórico persistente** — todas as auditorias são guardadas em base de dados SQLite (`dados/headings.db`)
- **Relatórios HTML** — gerados automaticamente em `relatorios_headings/`
- **Cancelamento de tarefas** — qualquer auditoria em curso pode ser cancelada

## Execução

```bash
python app.py
```

Abre o browser em `http://localhost:5001`.

## Tecnologias
- **Playwright** — carrega páginas reais, incluindo conteúdo dinâmico
- **Flask** — backend e API REST
- **SQLite** — persistência do histórico de auditorias
- **Threading** — auditorias correm em background com workers paralelos