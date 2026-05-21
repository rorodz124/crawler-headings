# Auditor de Headings

Estrutura simples em terminal:

- `crawler.py`: navega no dominio, segue links internos, controla visitados e chama a extracao
- `heading_extractor.py`: recolhe `h1` a `h6` do DOM com Playwright
- `heading_rules.py`: valida as regras de hierarquia e `h1`
- `main.py`: CLI e arranque
- `reporting.py`: resumo terminal e JSON

O crawler percorre um dominio, extrai `h1` a `h6` com Playwright e valida a hierarquia.

## Preparacao

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

## Execucao

```powershell
python main.py https://exemplo.pt --max-pages 30
```

## Opcoes uteis

```powershell
python main.py https://exemplo.pt --max-pages 30 --workers 4 --timeout-ms 30000
python main.py https://exemplo.pt --all-headings
python main.py https://exemplo.pt --no-report
python main.py https://exemplo.pt --include-subdomains
```

## Regras validadas

- Exatamente um `h1` por pagina
- Sem saltos de hierarquia (`h1 -> h3`, `h2 -> h4`, etc.)
- A estrutura nao pode comecar diretamente em niveis profundos
- Headings vazios sao invalidos