import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DADOS_DIR = BASE_DIR / "dados"
DB_PATH = DADOS_DIR / "headings.db"

_lock = threading.Lock()

def _agora():
    return datetime.utcnow().isoformat() + "Z"

def _ligar():
    DADOS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def inicializar():
    with _lock, _ligar() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id          TEXT PRIMARY KEY,
                tipo        TEXT NOT NULL,
                url         TEXT,
                iniciado_em TEXT,
                terminado_em TEXT,
                total_paginas    INTEGER DEFAULT 0,
                paginas_com_erros INTEGER DEFAULT 0,
                paginas_ok       INTEGER DEFAULT 0,
                total_issues     INTEGER DEFAULT 0,
                tempo_total_s    REAL    DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS paginas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                url         TEXT,
                titulo      TEXT,
                valida      INTEGER DEFAULT 1,
                total_headings       INTEGER DEFAULT 0,
                considered_headings  INTEGER DEFAULT 0,
                h1_count             INTEGER DEFAULT 0,
                issue_count          INTEGER DEFAULT 0,
                headings_json        TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            );

            CREATE TABLE IF NOT EXISTS issues (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                pagina_id   INTEGER NOT NULL,
                rule        TEXT,
                message     TEXT,
                heading_index INTEGER,
                FOREIGN KEY (run_id)    REFERENCES runs(id),
                FOREIGN KEY (pagina_id) REFERENCES paginas(id)
            );
            """
        )

# ---------------------------------------------------------------------------
# Guardar um run completo
# ---------------------------------------------------------------------------
def guardar_run(tipo, url, resultado, iniciado_em=None):
    """
    Recebe o dict devolvido por crawl_site / _run_audit e persiste-o.
    Devolve o run_id gerado.
    """
    run_id      = uuid.uuid4().hex
    iniciado    = iniciado_em or _agora()
    terminado   = _agora()
    reports     = resultado.get("reports") or []
    timings     = resultado.get("timings") or {}

    total_paginas     = resultado.get("pages_crawled") or len(reports)
    paginas_com_erros = resultado.get("pages_with_issues") or 0
    paginas_ok        = total_paginas - paginas_com_erros
    total_issues      = sum(len(r.get("issues") or []) for r in reports)
    tempo_total_s     = timings.get("total_elapsed_seconds") or 0.0

    with _lock, _ligar() as conn:
        conn.execute(
            """
            INSERT INTO runs (
                id, tipo, url, iniciado_em, terminado_em,
                total_paginas, paginas_com_erros, paginas_ok,
                total_issues, tempo_total_s
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, tipo, url, iniciado, terminado,
                total_paginas, paginas_com_erros, paginas_ok,
                total_issues, round(tempo_total_s, 3),
            ),
        )

        for report in reports:
            summary  = report.get("summary") or {}
            headings = report.get("considered_headings") or []

            cur = conn.execute(
                """
                INSERT INTO paginas (
                    run_id, url, titulo, valida,
                    total_headings, considered_headings, h1_count, issue_count,
                    headings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    report.get("url") or "",
                    report.get("title") or "",
                    1 if report.get("valid") else 0,
                    summary.get("total_headings") or 0,
                    summary.get("considered_headings") or 0,
                    summary.get("h1_count") or 0,
                    summary.get("issue_count") or 0,
                    json.dumps(headings, ensure_ascii=False),
                ),
            )
            pagina_id = cur.lastrowid

            for issue in (report.get("issues") or []):
                conn.execute(
                    """
                    INSERT INTO issues (run_id, pagina_id, rule, message, heading_index)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        pagina_id,
                        issue.get("rule") or "",
                        issue.get("message") or "",
                        issue.get("heading_index"),
                    ),
                )

    return run_id

# ---------------------------------------------------------------------------
# Listar histórico
# ---------------------------------------------------------------------------
def listar_runs(limite=100):
    """Devolve lista de runs ordenada da mais recente para a mais antiga."""
    with _lock, _ligar() as conn:
        rows = conn.execute(
            """
            SELECT id, tipo, url, iniciado_em, terminado_em,
                   total_paginas, paginas_com_erros, paginas_ok,
                   total_issues, tempo_total_s
            FROM runs
            ORDER BY iniciado_em DESC
            LIMIT ?
            """,
            (limite,),
        ).fetchall()
    return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# Obter detalhe de um run
# ---------------------------------------------------------------------------
def obter_run(run_id):
    """Devolve o run + todas as páginas com os seus issues."""
    with _lock, _ligar() as conn:
        run_row = conn.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not run_row:
            return None

        paginas_rows = conn.execute(
            """
            SELECT id, url, titulo, valida,
                   total_headings, considered_headings, h1_count, issue_count,
                   headings_json
            FROM paginas WHERE run_id = ? ORDER BY url
            """,
            (run_id,),
        ).fetchall()

        paginas = []
        for p in paginas_rows:
            issues_rows = conn.execute(
                "SELECT rule, message, heading_index FROM issues WHERE pagina_id = ?",
                (p["id"],),
            ).fetchall()
            paginas.append({
                **dict(p),
                "headings": json.loads(p["headings_json"] or "[]"),
                "issues":   [dict(i) for i in issues_rows],
            })

    return {**dict(run_row), "paginas": paginas}

# ---------------------------------------------------------------------------
# Apagar
# ---------------------------------------------------------------------------
def apagar_run(run_id):
    """Apaga um run e todos os dados associados."""
    with _lock, _ligar() as conn:
        paginas = conn.execute(
            "SELECT id FROM paginas WHERE run_id = ?", (run_id,)
        ).fetchall()
        for p in paginas:
            conn.execute("DELETE FROM issues WHERE pagina_id = ?", (p["id"],))
        conn.execute("DELETE FROM paginas WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))


def apagar_todos_runs():
    """Apaga todo o histórico."""
    with _lock, _ligar() as conn:
        conn.execute("DELETE FROM issues")
        conn.execute("DELETE FROM paginas")
        conn.execute("DELETE FROM runs")

inicializar()