# Flask backend for the Headings Validation Crawler UI
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

from config import AuditConfig, CrawlConfig
from crawler import crawl_site
from reporting import write_json_report

BASE_DIR = Path(__file__).resolve().parent
UI_FILE = BASE_DIR / "headings_app.html"
JS_FILE = BASE_DIR / "headings_app.js"
REPORTS_DIR = BASE_DIR / "relatorios_headings"

MAX_JOBS = 20
MAX_LOGS = 120
WORKERS = 4 

app = Flask(__name__)
CORS(app)


class JobManager:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def _snapshot(self, job):
        # Returns a plain-dict copy safe to serialise to JSON
        return {
            "id":                  job["id"],
            "type":                job["type"],
            "title":               job["title"],
            "state":               job["state"],
            "created_at":          job["created_at"],
            "started_at":          job.get("started_at"),
            "finished_at":         job.get("finished_at"),
            "cancel_requested":    job["cancel_requested"],
            "progress":            dict(job["progress"]),
            "logs":                list(job["logs"]),
            "result":              job.get("result"),
            "error":               job.get("error"),
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
                j["state"] = "cancelada"
                j["finished_at"] = datetime.utcnow().isoformat() + "Z"
                j["progress"] = {
                    "phase":      "cancelada",
                    "message":    "Tarefa cancelada antes de arrancar.",
                    "percentage": j["progress"].get("percentage", 0),
                }
            return self._snapshot(j)

    def _mark_running(self, job_id):
        # Returns False if cancellation was already requested
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

    def mark_done(self, job_id, result):
        with self._lock:
            j = self._jobs[job_id]
            j["state"]       = "concluida"
            j["finished_at"] = datetime.utcnow().isoformat() + "Z"
            j["result"]      = result
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
    # Guards against path traversal and non-HTML files
    base = REPORTS_DIR.resolve()
    target = (REPORTS_DIR / name).resolve()
    if not str(target).startswith(str(base) + os.sep) or target.suffix.lower() != ".html":
        abort(404)
    return target


def _launch_job(job_type, title, runner):
    # Creates the job record and starts the worker thread
    job    = job_manager.create(job_type, title)
    job_id = job["id"]

    def work():
        if not job_manager._mark_running(job_id):
            return

        def on_progress(current, total, url, issue_count, timings):
            # Matches the crawler's on_progress callback signature
            job_manager.update_progress(job_id, {
                "phase":   "analise",
                "message": f"[{current}/{total}] {url} — {issue_count} problema(s)",
                "current": current,
                "total":   total,
            })

        def should_cancel():
            j = job_manager.get(job_id)
            return bool(j and j["cancel_requested"])

        try:
            result = runner(on_progress, should_cancel)
            if should_cancel():
                job_manager.mark_failed(job_id, "Tarefa cancelada pelo utilizador.")
            else:
                job_manager.mark_done(job_id, result)
        except Exception as exc:
            if should_cancel():
                job_manager.mark_failed(job_id, "Tarefa cancelada pelo utilizador.")
            else:
                job_manager.add_log(job_id, traceback.format_exc())
                job_manager.mark_failed(job_id, str(exc))

    threading.Thread(target=work, daemon=True).start()
    return job


def _job_response(factory):
    # Wraps job creation; returns 202 on success or 400 on validation error
    try:
        job = factory()
    except ValueError as exc:
        return _error_response(str(exc), 400)
    return jsonify(job), 202


def _run_audit(url, max_pages, on_progress, should_cancel):
    # Runs crawl_site with the configured settings and returns the result dict
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    crawl_config = CrawlConfig(
        max_pages=max_pages if max_pages > 0 else 200,
        crawler_workers=WORKERS,
    )
    audit_config = AuditConfig(
        validate_visible_only=True,
        report_dir=str(REPORTS_DIR),
    )
    result = crawl_site(url, crawl_config, audit_config, on_progress=on_progress)
    try:
        write_json_report(result, str(REPORTS_DIR))
    except Exception:
        pass
    return result


# Static file endpoints
@app.get("/")
def index():
    return send_file(UI_FILE)


@app.get("/headings_app.js")
def serve_js():
    return send_file(JS_FILE, mimetype="application/javascript; charset=utf-8")


# Report file endpoints
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
            f.unlink()
            deleted += 1
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
    target.rename(dest)
    return jsonify({"name": new_name, "url": f"/relatorios/{new_name}"})


@app.delete("/api/relatorios/<path:name>")
def delete_report(name):
    target = _resolve_report_path(name)
    if not target.is_file():
        abort(404)
    target.unlink()
    return jsonify({"deleted": name})


# Health and job endpoints
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


# Audit job creation endpoints
@app.post("/api/jobs/headings/pagina")
def create_page_job():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()
    if not url:
        return _error_response("Indica a URL da página.")
    return _job_response(lambda: _launch_job(
        "pagina", f"Página: {url}",
        lambda p, sc: _run_audit(url, max_pages=1, on_progress=p, should_cancel=sc),
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
        lambda p, sc: _run_audit(url, max_pages=max_pages, on_progress=p, should_cancel=sc),
    ))

if __name__ == "__main__":
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[headings] Running at http://localhost:5001")
    app.run(debug=False, port=5001, threaded=True)