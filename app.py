import os
import re
import threading
import traceback
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file
from flask_cors import CORS

import db
from config import AuditConfig, CrawlConfig
from crawler import crawl_site
from reporting import make_report_basename, write_html_report, write_json_report

BASE_DIR = Path(__file__).resolve().parent
UI_FILE  = BASE_DIR / "headings_app.html"
JS_FILE  = BASE_DIR / "headings_app.js"
REPORTS_DIR = BASE_DIR / "relatorios_headings"

MAX_JOBS = 20
MAX_LOGS = 120
WORKERS  = 4

app = Flask(__name__)
CORS(app)


class JobManager:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def _snapshot(self, job):
        return {
            "id":               job["id"],
            "type":             job["type"],
            "title":            job["title"],
            "state":            job["state"],
            "created_at":       job["created_at"],
            "started_at":       job.get("started_at"),
            "finished_at":      job.get("finished_at"),
            "cancel_requested": job["cancel_requested"],
            "progress":         dict(job["progress"]),
            "logs":             list(job["logs"]),
            "result":           job.get("result"),
            "error":            job.get("error"),
            "run_id":           job.get("run_id"),
        }

    def create(self, job_type, title):
        job_id = uuid.uuid4().hex[:10]
        job = {
            "id":               job_id,
            "type":             job_type,
            "title":            title,
            "state":            "pendente",
            "created_at":       datetime.utcnow().isoformat() + "Z",
            "started_at":       None,
            "finished_at":      None,
            "cancel_requested": False,
            "progress":         {"phase": "queue", "message": "Na fila", "percentage": 0},
            "logs":             deque(maxlen=MAX_LOGS),
            "result":           None,
            "error":            None,
            "run_id":           None,
        }
        with self._lock:
            active = sum(1 for j in self._jobs.values() if j["state"] in {"pendente", "a_correr"})
            if active >= MAX_JOBS:
                raise ValueError(f"Limite de tarefas ativas atingido ({MAX_JOBS}).")
            self._jobs[job_id] = job
        return self._snapshot(job)

    def list_all(self):
        with self._lock:
            items = [self._snapshot(j) for j in self._jobs.values()]
        items.sort(key=lambda j: j["created_at"], reverse=True)
        return items

    def get(self, job_id):
        with self._lock:
            j = self._jobs.get(job_id)
            return self._snapshot(j) if j else None

    def delete(self, job_id):
        with self._lock:
            removed = self._jobs.pop(job_id, None)
        return removed is not None

    def cancel(self, job_id):
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return None
            j["cancel_requested"] = True
            j["logs"].append("Cancelamento pedido pelo utilizador.")
            if j["state"] == "pendente":
                j["state"]       = "cancelada"
                j["finished_at"] = datetime.utcnow().isoformat() + "Z"
                j["progress"]    = {
                    "phase":      "cancelada",
                    "message":    "Tarefa cancelada antes de arrancar.",
                    "percentage": j["progress"].get("percentage", 0),
                }
            return self._snapshot(j)

    def _mark_running(self, job_id):
        with self._lock:
            j = self._jobs[job_id]
            if j["cancel_requested"] or j["state"] == "cancelada":
                return False
            j["state"]      = "a_correr"
            j["started_at"] = datetime.utcnow().isoformat() + "Z"
            j["logs"].append("Tarefa iniciada.")
            return True

    def add_log(self, job_id, message):
        with self._lock:
            j = self._jobs.get(job_id)
            if j:
                j["logs"].append(message)

    def update_progress(self, job_id, payload):
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return
            p = dict(j["progress"])
            p.update(payload)
            total = p.get("total")
            if total:
                p["percentage"] = int(max(0, min(100, (p.get("current") or 0) * 100 / total)))
            j["progress"] = p
            if payload.get("message"):
                j["logs"].append(payload["message"])

    def mark_done(self, job_id, result, run_id=None):
        with self._lock:
            j = self._jobs[job_id]
            j["state"]       = "concluida"
            j["finished_at"] = datetime.utcnow().isoformat() + "Z"
            j["result"]      = result
            j["run_id"]      = run_id
            j["progress"]    = {**j["progress"], "phase": "concluida", "message": "Auditoria concluída.", "percentage": 100}
            j["logs"].append("Auditoria concluída.")

    def mark_failed(self, job_id, error):
        with self._lock:
            j = self._jobs[job_id]
            state            = "cancelada" if j["cancel_requested"] else "erro"
            j["state"]       = state
            j["finished_at"] = datetime.utcnow().isoformat() + "Z"
            j["error"]       = error
            j["progress"]    = {**j["progress"], "phase": state, "message": error}
            j["logs"].append(error)


job_manager = JobManager()


def _error_response(msg, status=400):
    return jsonify({"error": msg}), status


def _resolve_report_path(name):
    base   = REPORTS_DIR.resolve()
    target = (REPORTS_DIR / name).resolve()
    if not str(target).startswith(str(base) + os.sep) or target.suffix.lower() != ".html":
        abort(404)
    return target


def _launch_job(job_type, title, runner):
    job    = job_manager.create(job_type, title)
    job_id = job["id"]

    def work():
        if not job_manager._mark_running(job_id):
            return

        def on_progress(current, total, url, issue_count, timings, pages_with_issues=0):
            issue_pages_message = (
                f"{pages_with_issues}/{total} páginas com problemas"
                if total > 0
                else f"{pages_with_issues} páginas com problemas"
            )
            progress_message = (
                f"[{current}/{total}] {url} — {issue_count} problema(s) — {issue_pages_message}"
                if total > 0
                else f"[{current}] {url} — {issue_count} problema(s) — {issue_pages_message}"
            )
            job_manager.update_progress(job_id, {
                "phase":             "analise",
                "message":           progress_message,
                "current":           current,
                "total":             total,
                "pages_with_issues": pages_with_issues,
            })

        def should_cancel():
            j = job_manager.get(job_id)
            return bool(j and j["cancel_requested"])

        try:
            result, run_id = runner(on_progress, should_cancel)
            if should_cancel():
                job_manager.mark_failed(job_id, "Tarefa cancelada pelo utilizador.")
            else:
                job_manager.mark_done(job_id, result, run_id=run_id)
        except Exception as exc:
            if should_cancel():
                job_manager.mark_failed(job_id, "Tarefa cancelada pelo utilizador.")
            else:
                job_manager.add_log(job_id, traceback.format_exc())
                job_manager.mark_failed(job_id, str(exc))

    threading.Thread(target=work, daemon=True).start()
    return job


def _job_response(factory):
    try:
        job = factory()
    except ValueError as exc:
        return _error_response(str(exc), 400)
    return jsonify(job), 202


def _run_audit(tipo, url, max_pages, on_progress, should_cancel):
    """Runs crawl_site, persists to DB, returns (result, run_id)."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    crawl_config = CrawlConfig(
        max_pages=max_pages,
        crawler_workers=WORKERS,
    )
    audit_config = AuditConfig(
        validate_visible_only=True,
        report_dir=str(REPORTS_DIR),
    )
    iniciado_em = datetime.utcnow().isoformat() + "Z"
    result = crawl_site(
        url,
        crawl_config,
        audit_config,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )

    if should_cancel():
        return result, None

    try:
        report_basename = make_report_basename(result)
        write_html_report(result, str(REPORTS_DIR), basename=report_basename)
        write_json_report(result, str(REPORTS_DIR), basename=report_basename)
    except Exception:
        pass

    run_id = None
    try:
        run_id = db.guardar_run(tipo=tipo, url=url, resultado=result, iniciado_em=iniciado_em)
    except Exception as exc:
        print(f"[DB] Não foi possível guardar histórico: {exc}")

    return result, run_id


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_file(UI_FILE)


@app.get("/headings_app.js")
def serve_js():
    return send_file(JS_FILE, mimetype="application/javascript; charset=utf-8")


# ---------------------------------------------------------------------------
# HTML report file endpoints
# ---------------------------------------------------------------------------

@app.get("/relatorios/<path:name>")
def open_report(name):
    target = _resolve_report_path(name)
    if not target.is_file():
        abort(404)
    return send_file(target)


@app.get("/api/relatorios")
def list_reports():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(REPORTS_DIR.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            stat = f.stat()
            items.append({
                "name":        f.name,
                "url":         f"/relatorios/{f.name}",
                "size":        stat.st_size,
                "modified_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
            })
        except OSError:
            pass
    return jsonify({"reports": items})


@app.delete("/api/relatorios")
def delete_all_reports():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    deleted = 0
    for f in REPORTS_DIR.glob("*.html"):
        try:
            json_pair = f.with_suffix(".json")
            f.unlink()
            deleted += 1
            if json_pair.is_file():
                json_pair.unlink()
        except OSError:
            pass
    for f in REPORTS_DIR.glob("*.json"):
        try:
            f.unlink()
        except OSError:
            pass
    return jsonify({"deleted": deleted})


@app.patch("/api/relatorios/<path:name>")
def rename_report(name):
    target = _resolve_report_path(name)
    if not target.is_file():
        abort(404)
    data     = request.get_json(silent=True) or {}
    new_name = (data.get("new_name") or "").strip()
    if not new_name:
        return _error_response("Indica o novo nome.")
    new_name = re.sub(r"[^\w\-]", "_", new_name.removesuffix(".html")).strip("_") + ".html"
    if not new_name or new_name == ".html":
        return _error_response("Nome inválido.")
    dest = REPORTS_DIR / new_name
    if dest.exists() and dest.resolve() != target.resolve():
        return _error_response("Já existe um relatório com esse nome.", 409)
    json_pair = target.with_suffix(".json")
    json_dest = dest.with_suffix(".json")
    target.rename(dest)
    if json_pair.is_file() and not json_dest.exists():
        json_pair.rename(json_dest)
    return jsonify({"name": new_name, "url": f"/relatorios/{new_name}"})


@app.delete("/api/relatorios/<path:name>")
def delete_report(name):
    target = _resolve_report_path(name)
    if not target.is_file():
        abort(404)
    json_pair = target.with_suffix(".json")
    target.unlink()
    if json_pair.is_file():
        json_pair.unlink()
    return jsonify({"deleted": name})


# ---------------------------------------------------------------------------
# History (DB) endpoints
# ---------------------------------------------------------------------------

@app.get("/api/historico")
def list_historico():
    """Returns the list of past runs stored in the DB."""
    try:
        limite = min(int(request.args.get("limite", 100)), 500)
        runs   = db.listar_runs(limite=limite)
        return jsonify({"runs": runs})
    except Exception as exc:
        return _error_response(str(exc), 500)


@app.get("/api/historico/<run_id>")
def get_historico(run_id):
    """Returns full detail of a run: pages + issues."""
    run = db.obter_run(run_id)
    if not run:
        return _error_response("Run não encontrada.", 404)
    return jsonify(run)


@app.delete("/api/historico/<run_id>")
def delete_historico(run_id):
    """Deletes a single run and all associated data."""
    run = db.obter_run(run_id)
    if not run:
        return _error_response("Run não encontrada.", 404)
    db.apagar_run(run_id)
    return jsonify({"deleted": run_id})


@app.delete("/api/historico")
def delete_all_historico():
    """Deletes the entire history."""
    db.apagar_todos_runs()
    return jsonify({"deleted": "all"})


# ---------------------------------------------------------------------------
# Health and job endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/jobs")
def list_jobs():
    return jsonify({"jobs": job_manager.list_all()})


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    job = job_manager.get(job_id)
    if not job:
        return _error_response("Tarefa não encontrada.", 404)
    return jsonify(job)


@app.delete("/api/jobs/<job_id>")
def delete_job(job_id):
    if not job_manager.delete(job_id):
        return _error_response("Tarefa não encontrada.", 404)
    return jsonify({"deleted": job_id})


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id):
    job = job_manager.cancel(job_id)
    if not job:
        return _error_response("Tarefa não encontrada.", 404)
    return jsonify(job)


# ---------------------------------------------------------------------------
# Audit job creation endpoints
# ---------------------------------------------------------------------------

@app.post("/api/jobs/headings/pagina")
def create_page_job():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()
    if not url:
        return _error_response("Indica a URL da página.")
    return _job_response(lambda: _launch_job(
        "pagina", f"Página: {url}",
        lambda p, sc: _run_audit("pagina", url, max_pages=1, on_progress=p, should_cancel=sc),
    ))


@app.post("/api/jobs/headings/site")
def create_site_job():
    data      = request.get_json(silent=True) or {}
    url       = (data.get("url") or "").strip()
    max_pages = int(data.get("max_paginas") or 0)
    if not url:
        return _error_response("Indica a URL base do site.")
    return _job_response(lambda: _launch_job(
        "site", f"Site: {url}",
        lambda p, sc: _run_audit("site", url, max_pages=max_pages, on_progress=p, should_cancel=sc),
    ))


if __name__ == "__main__":
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    print("[headings] Running at http://localhost:5001")
    app.run(debug=False, port=5001, threaded=True)