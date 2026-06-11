(() => {
"use strict";

const API_BASE = window.location.protocol === "file:" ? "http://localhost:5001" : "";

const toastEl          = document.getElementById("toast");
const jobsListEl       = document.getElementById("jobsList");
const reportsListEl    = document.getElementById("reportsList");
const modeToggle       = document.getElementById("modeToggle");
const urlInput         = document.getElementById("urlInput");
const urlLabel         = document.getElementById("urlLabel");
const limitField       = document.getElementById("limitField");
const maxPagesInput    = document.getElementById("maxPaginas");
const submitBtn        = document.getElementById("submitBtn");
const clearBtn         = document.getElementById("clearBtn");
const reportsRefreshBtn    = document.getElementById("reportsRefreshBtn");
const reportsDeleteAllBtn  = document.getElementById("reportsDeleteAllBtn");

let crawlMode = "single";
let hiddenJobIds = new Set();
let jobFilters = {};
let jobShowAll = {};
let openPages = {};

let _pollTimer = null;
function schedulePolling(fast = false) {
  clearTimeout(_pollTimer);
  _pollTimer = setTimeout(async () => {
    const hasActive = await refreshJobs({ silent: true });
    await refreshReports({ silent: true });
    schedulePolling(hasActive);
  }, fast ? 2500 : 9000);
}

function esc(v) {
  return String(v ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function formatBytes(b) {
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1048576).toFixed(1)} MB`;
}

function formatDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  const p = n => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth()+1)}/${d.getFullYear()} às ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function showToast(msg, isError = false) {
  toastEl.textContent = msg;
  toastEl.className = "toast show" + (isError ? " err" : "");
  clearTimeout(toastEl._timer);
  toastEl._timer = setTimeout(() => toastEl.classList.remove("show"), 2800);
}

async function fetchJson(url, opts = {}) {
  const res = await fetch(`${API_BASE}${url}`, opts);
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; }
  catch { throw new Error("Não foi possível ligar à API. Confirma que o backend está a correr."); }
  if (!res.ok) throw new Error(data.error || "Pedido falhou.");
  return data;
}

function setMode(mode) {
  crawlMode = mode;
  modeToggle.querySelectorAll(".tab-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.mode === mode)
  );
  const isSite = mode === "site";
  limitField.classList.toggle("hidden", !isSite);
  urlLabel.textContent = isSite ? "URL base do site" : "URL da página";
  urlInput.placeholder = isSite ? "https://www.exemplo.pt" : "https://www.exemplo.pt/pagina";
}

modeToggle.querySelectorAll(".tab-btn").forEach(b =>
  b.addEventListener("click", () => setMode(b.dataset.mode))
);

clearBtn.addEventListener("click", () => {
  urlInput.value = "";
  maxPagesInput.value = "0";
});

submitBtn.addEventListener("click", async () => {
  const url = urlInput.value.trim();
  if (!url) { showToast("Indica a URL para auditar.", true); return; }
  const endpoint = crawlMode === "site" ? "/api/jobs/headings/site" : "/api/jobs/headings/pagina";
  try {
    await fetchJson(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, max_paginas: parseInt(maxPagesInput.value, 10) || 0 }),
    });
    showToast("Auditoria lançada.");
    urlInput.value = "";
    await refreshJobs({ silent: true });
    schedulePolling(true);
  } catch (e) { showToast(e.message, true); }
});

function getIssueLabel(issue) {
  return { single_h1: "H1", empty_heading: "Vazio", hierarchy_skip: "Salto", starts_too_deep: "Início profundo", page_error: "Erro de página" }[issue.rule] ?? issue.rule ?? "?";
}

function getIssueClass(issue) {
  if (issue.rule === "hierarchy_skip")  return "warn-skip";
  if (issue.rule === "starts_too_deep") return "warn-deep";
  return "";
}

function renderHeadingTree(report, filter) {
  const headings = report.considered_headings || [];
  const issues = report.issues || [];

  const issuesByHeading = {};
  for (const issue of issues) {
    if (issue.heading_index == null) continue;
    (issuesByHeading[issue.heading_index] ??= []).push(issue);
  }

  const pageLevelIssues = issues.filter(i => i.heading_index == null);
  let html = '<div class="heading-list">';

  for (const pi of pageLevelIssues) {
    html += `<div class="h-row h-error" style="border-radius:6px">
      <span class="h-issue-badge">${esc(getIssueLabel(pi))}</span>
      <span class="h-text" style="font-size:11px;color:var(--red)">${esc(pi.message)}</span>
    </div>`;
  }

  if (!headings.length) {
    html += `<div style="font-size:12px;color:var(--muted);padding:6px 8px;font-style:italic">Nenhum heading encontrado.</div>`;
  }

  for (const h of headings) {
    const rowIssues = issuesByHeading[h.index] || [];
    const hasError = rowIssues.length > 0;
    if (filter === "errors" && !hasError) continue;
    const isEmpty = !h.text || h.is_empty;
    const badgesHtml = rowIssues.map(i =>
      `<span class="h-issue-badge ${getIssueClass(i)}">${esc(getIssueLabel(i))}</span>`
    ).join("");
    html += `<div class="h-row${hasError ? " h-error" : ""}">
        <span class="h-indent" style="width:${Math.max(0, h.level - 1) * 14}px;flex-shrink:0"></span>
        <span class="h-tag ${esc(h.tag)}">${esc(h.tag)}</span>
        <span class="h-text${isEmpty ? " empty-text" : ""}">${esc(isEmpty ? "(heading vazio)" : h.text)}</span>
        ${badgesHtml ? `<div class="h-issues">${badgesHtml}</div>` : ""}
      </div>`;
  }

  return html + "</div>";
}

const PAGE_LIMIT = 12;

function renderPagesList(job, reports) {
  const jobId = job.id;
  const filter = jobFilters[jobId] || "all";
  const showAll = jobShowAll[jobId] || false;
  const filtered = filter === "errors" ? reports.filter(r => !r.valid) : reports;

  if (!filtered.length) {
    return filter === "errors"
      ? `<div style="padding:12px 0;font-size:12px;color:var(--green);font-family:var(--mono)">✓ Nenhum erro encontrado.</div>`
      : `<div style="padding:12px 0;font-size:12px;color:var(--muted);font-family:var(--mono)">Sem páginas para mostrar.</div>`;
  }

  const visible = showAll ? filtered : filtered.slice(0, PAGE_LIMIT);
  const remaining = filtered.length - visible.length;

  const pagesHtml = visible.map(r => {
    const pageKey = jobId + ":" + r.url;
    const isOpen = openPages[pageKey] || false;
    const hasError = !r.valid;
    const issueCount = (r.issues || []).length;
    const isPageErr = r.issues && r.issues.some(i => i.rule === "page_error");
    return `<div class="page-item${hasError ? " has-issues" : ""}${isOpen ? " open" : ""}" data-page-key="${esc(pageKey)}">
        <div class="page-item-head" data-toggle-page="${esc(pageKey)}">
          <span class="page-chevron">▶</span>
          <span class="page-url" title="${esc(r.url)}">${esc(r.url)}</span>
          <button class="copy-url-btn" data-copy-url="${esc(r.url)}" title="Copiar link" type="button">⎘</button>
          <span class="page-badge ${hasError ? "badge-err" : "badge-ok"}">${hasError ? `⚠ ${issueCount} ${issueCount === 1 ? "erro" : "erros"}` : "✓ OK"}</span>
        </div>
        <div class="page-content">${isPageErr
          ? `<div class="page-error-msg">${esc((r.issues.find(i => i.rule === "page_error") || {}).message || "Erro ao carregar a página.")}</div>`
          : renderHeadingTree(r, filter)
        }</div>
      </div>`;
  }).join("");

  const moreHtml = remaining > 0
    ? `<button class="show-all-btn" data-job-show-all="${esc(jobId)}" type="button">Ver todas as páginas (mais ${remaining})</button>`
    : "";

  return pagesHtml + moreHtml;
}

function renderJobBody(job) {
  const result = job.result;

  if (!result) {
    if (job.state === "erro") {
      return `<div class="page-error-msg">${esc(job.error || "Erro desconhecido.")}</div>`;
    }
    const progress = job.progress || {};
    const partialReports = progress.partial_reports_with_errors || [];
    let html = `<div style="font-size:12px;color:var(--muted);font-family:var(--mono);padding:4px 0">${esc(progress.message || "A aguardar...")}</div>`;
    if (job.state === "a_correr" && partialReports.length > 0) {
      const partialCount = progress.partial_pages_with_issues || partialReports.length;
      html += `<div class="pages-header" style="margin-top:12px;margin-bottom:6px;">
          <span class="pages-header-title">Erros encontrados até agora (${partialCount})</span>
        </div>${renderPagesList(job, partialReports)}`;
    }
    return html;
  }

  const reports = result.reports || [];
  const pagesCrawled = result.pages_crawled || 0;
  const pagesWithErrors = result.pages_with_issues || 0;
  const jobId = job.id;
  const filter = jobFilters[jobId] || "all";
  const isSinglePage = pagesCrawled <= 1;

  const summaryHtml = `<div class="summary">
    <div class="s-card"><div class="s-num">${pagesCrawled}</div><div class="s-label">Páginas</div></div>
    <div class="s-card"><div class="s-num red">${pagesWithErrors}</div><div class="s-label">Com erros</div></div>
    <div class="s-card"><div class="s-num green">${pagesCrawled - pagesWithErrors}</div><div class="s-label">Sem erros</div></div>
  </div>`;

  const filterRowHtml = isSinglePage
    ? `<div class="pages-header"><span class="pages-header-title">Página auditada</span></div>`
    : `<div class="pages-header">
        <span class="pages-header-title">Páginas auditadas</span>
        <div class="filter-row">
          <button class="filter-btn${filter === "all" ? " active" : ""}" data-job-filter="${esc(jobId)}" data-filter-val="all">Todas</button>
          <button class="filter-btn${filter === "errors" ? " active" : ""}" data-job-filter="${esc(jobId)}" data-filter-val="errors">Só erros</button>
        </div>
      </div>`;

  return summaryHtml + filterRowHtml + renderPagesList(job, reports);
}

function renderJobs(allJobs) {
  const visible = allJobs.filter(j => !hiddenJobIds.has(j.id));
  if (!visible.length) {
    jobsListEl.innerHTML = `<div class="empty">Ainda não há auditorias.</div>`;
    return;
  }

  jobsListEl.innerHTML = visible.map(job => {
    const progress = job.progress || {};
    const pct = progress.percentage ?? 0;
    const hasTotal = (progress.total || 0) > 0;
    const isRunning = job.state === "a_correr";
    const isIndeterminate = isRunning && !hasTotal;
    const isDone = ["concluida", "erro", "cancelada"].includes(job.state);
    const canCancel = ["pendente", "a_correr"].includes(job.state);
    const progressLabel = hasTotal ? `${pct}%` : `${progress.current || 0} páginas`;
    return `<article class="job" data-job-id="${esc(job.id)}">
        <div class="job-head">
          <div class="job-left">
            <div class="job-title">${esc(job.title)}</div>
            <div class="job-meta">${esc(buildProgressMsg(job))}</div>
          </div>
          <div class="job-right">
            <span class="status estado-${esc(job.state)}">${esc(job.state.replaceAll("_", " "))}</span>
            ${canCancel ? `<button class="btn btn-danger" data-cancel="${esc(job.id)}" type="button">Cancelar</button>` : ""}
            ${isDone    ? `<button class="job-close" data-close="${esc(job.id)}" type="button" title="Fechar">×</button>` : ""}
          </div>
        </div>
        <div class="progress-wrap">
          <div class="progress-label">${esc(progressLabel)}</div>
          <div class="progress${isIndeterminate ? " indeterminate" : ""}"><div style="width:${isIndeterminate ? 100 : pct}%"></div></div>
        </div>
        <div class="job-body">${renderJobBody(job)}</div>
      </article>`;
  }).join("");

  jobsListEl.querySelectorAll("[data-copy-url]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        await navigator.clipboard.writeText(btn.dataset.copyUrl);
        showToast("Link copiado.");
      } catch (_) { showToast("Não foi possível copiar.", true); }
    });
  });

  jobsListEl.querySelectorAll("[data-job-show-all]").forEach(btn => {
    btn.addEventListener("click", () => {
      const jid = btn.dataset.jobShowAll;
      jobShowAll[jid] = true;
      const jobData = visible.find(j => j.id === jid);
      if (!jobData) return;
      const article = jobsListEl.querySelector(`[data-job-id="${CSS.escape(jid)}"]`);
      if (!article) return;
      const body = article.querySelector(".job-body");
      if (body) body.innerHTML = renderJobBody(jobData);
      attachInteractivity(article);
    });
  });

  jobsListEl.querySelectorAll("[data-cancel]").forEach(btn => {
    btn.addEventListener("click", async () => {
      try {
        await fetchJson(`/api/jobs/${btn.dataset.cancel}/cancel`, { method: "POST" });
        showToast("Cancelamento pedido.");
        await refreshJobs({ silent: true });
      } catch (e) { showToast(e.message, true); }
    });
  });

  jobsListEl.querySelectorAll("[data-close]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const jid = btn.dataset.close;
      try { await fetchJson(`/api/jobs/${jid}`, { method: "DELETE" }); } catch (_) {}
      hiddenJobIds.add(jid);
      delete jobFilters[jid];
      delete jobShowAll[jid];
      const el = jobsListEl.querySelector(`[data-job-id="${CSS.escape(jid)}"]`);
      if (el) el.remove();
      if (!jobsListEl.querySelector(".job")) {
        try {
          const hist = await fetchJson("/api/historico?limite=20");
          renderHistorico(hist.runs || []);
        } catch (_) {
          jobsListEl.innerHTML = `<div class="empty">Ainda não há auditorias.</div>`;
        }
      }
    });
  });

  jobsListEl.querySelectorAll("[data-job-filter]").forEach(btn => {
    btn.addEventListener("click", () => {
      const jid = btn.dataset.jobFilter;
      jobFilters[jid] = btn.dataset.filterVal;
      const jobData = visible.find(j => j.id === jid);
      if (!jobData) return;
      const article = jobsListEl.querySelector(`[data-job-id="${CSS.escape(jid)}"]`);
      if (!article) return;
      const body = article.querySelector(".job-body");
      if (body) body.innerHTML = renderJobBody(jobData);
      attachInteractivity(article);
    });
  });

  jobsListEl.querySelectorAll("[data-toggle-page]").forEach(el => {
    el.addEventListener("click", () => togglePage(el.dataset.togglePage, jobsListEl));
  });
}

function togglePage(key, container) {
  openPages[key] = !openPages[key];
  const item = container.querySelector(`[data-page-key="${CSS.escape(key)}"]`);
  if (item) item.classList.toggle("open", !!openPages[key]);
}

function attachInteractivity(container) {
  container.querySelectorAll("[data-toggle-page]").forEach(el => {
    el.addEventListener("click", () => togglePage(el.dataset.togglePage, container));
  });
  container.querySelectorAll("[data-job-filter]").forEach(btn => {
    btn.addEventListener("click", () => {
      jobFilters[btn.dataset.jobFilter] = btn.dataset.filterVal;
      refreshJobs({ silent: true });
    });
  });
}

function buildProgressMsg(job) {
  const p = job.progress || {};
  if (job.state === "concluida" && job.result) return "";
  if (p.phase === "crawl" || p.phase === "analise") {
    const current = p.current || 0;
    const total = p.total || 0;
    const errorPages = p.pages_with_issues || 0;
    if (total) return `${current} / ${total} páginas visitadas - ${errorPages} páginas com problemas`;
    if (current) return `${current} páginas visitadas - ${errorPages} páginas com problemas`;
  }
  return p.message || "A aguardar...";
}

function renderReports(reports) {
  if (!reports.length) {
    reportsListEl.innerHTML = `<div class="empty">Nenhum relatório guardado.</div>`;
    return;
  }
  reportsListEl.innerHTML = reports.map(r => `
    <article class="report-row">
      <div>
        <div class="report-name">${esc(r.name)}</div>
        <div class="report-meta">${esc(formatDate(r.modified_at))} · ${esc(formatBytes(r.size || 0))}</div>
      </div>
      <div class="report-actions">
        <a class="btn btn-secondary" href="${esc(r.url)}" target="_blank">Abrir</a>
        <a class="btn btn-secondary" href="${esc(r.url)}" download="${esc(r.name)}">Descarregar</a>
        <button class="btn btn-secondary" type="button" data-rename-report="${esc(r.name)}">Renomear</button>
        <button class="btn btn-danger"    type="button" data-delete-report="${esc(r.name)}">Apagar</button>
      </div>
      <div class="report-rename-row" data-rename-row="${esc(r.name)}">
        <input class="report-rename-input" type="text" value="${esc(r.name.replace(/\.html$/i, ""))}" maxlength="80" spellcheck="false">
        <button class="btn btn-primary"   type="button" data-rename-confirm="${esc(r.name)}">Guardar</button>
        <button class="btn btn-secondary" type="button" data-rename-cancel="${esc(r.name)}">Cancelar</button>
      </div>
    </article>
  `).join("");

  reportsListEl.querySelectorAll("[data-rename-report]").forEach(btn => {
    btn.addEventListener("click", () => {
      const row = reportsListEl.querySelector(`[data-rename-row="${CSS.escape(btn.dataset.renameReport)}"]`);
      if (row) { row.classList.add("open"); row.querySelector("input").select(); }
    });
  });

  reportsListEl.querySelectorAll("[data-rename-confirm]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const oldName = btn.dataset.renameConfirm;
      const row     = reportsListEl.querySelector(`[data-rename-row="${CSS.escape(oldName)}"]`);
      const newName = row ? row.querySelector("input").value.trim() : "";
      if (!newName) { showToast("O nome não pode estar vazio.", true); return; }
      try {
        await fetchJson(`/api/relatorios/${encodeURIComponent(oldName)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ new_name: newName }),
        });
        showToast("Relatório renomeado.");
        await refreshReports({ silent: true, force: true });
      } catch (e) { showToast(e.message, true); }
    });
  });

  reportsListEl.querySelectorAll("[data-rename-cancel]").forEach(btn => {
    btn.addEventListener("click", () => {
      const row = reportsListEl.querySelector(`[data-rename-row="${CSS.escape(btn.dataset.renameCancel)}"]`);
      if (row) row.classList.remove("open");
    });
  });

  reportsListEl.querySelectorAll("[data-delete-report]").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!confirm("Apagar este relatório?")) return;
      try {
        await fetchJson(`/api/relatorios/${encodeURIComponent(btn.dataset.deleteReport)}`, { method: "DELETE" });
        showToast("Relatório apagado.");
        await refreshReports({ silent: true, force: true });
      } catch (e) { showToast(e.message, true); }
    });
  });
}

reportsRefreshBtn.addEventListener("click", () => refreshReports({ force: true }));
reportsDeleteAllBtn.addEventListener("click", async () => {
  if (!confirm("Apagar todos os relatórios guardados?")) return;
  try {
    const data = await fetchJson("/api/relatorios", { method: "DELETE" });
    showToast(`${data.deleted || 0} relatórios apagados.`);
    await refreshReports({ silent: true, force: true });
  } catch (e) { showToast(e.message, true); }
});

function isRenameOpen() {
  return !!reportsListEl.querySelector(".report-rename-row.open");
}

async function refreshReports({ silent = false, force = false } = {}) {
  if (!force && isRenameOpen()) return;
  try {
    const data = await fetchJson("/api/relatorios");
    renderReports(data.reports || []);
  } catch (e) {
    if (!silent) showToast(e.message, true);
    reportsListEl.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function renderHistorico(runs) {
  if (!runs.length) {
    jobsListEl.innerHTML = `<div class="empty">Ainda não há auditorias.</div>`;
    return;
  }
  jobsListEl.innerHTML = runs.map(run => {
    const tipoLabel = run.tipo === "pagina" ? "Página única" : "Site inteiro";
    const meta = [
      run.terminado_em ? formatDate(run.terminado_em) : null,
      run.total_paginas != null ? `${run.total_paginas} página(s)` : null,
      run.paginas_com_erros ? `${run.paginas_com_erros} com problemas` : "sem problemas",
      run.tempo_total_s != null ? `${Number(run.tempo_total_s).toFixed(1)}s` : null,
    ].filter(Boolean).join(" · ");
    return `<article class="job" data-hist-id="${esc(run.id)}">
        <div class="job-head">
          <div class="job-left">
            <div class="job-title">${esc(tipoLabel + ": " + run.url)}</div>
            <div class="job-meta">${esc(meta)}</div>
          </div>
          <div class="job-right">
            <span class="status estado-concluida">histórico</span>
            <button class="job-close" data-delete-hist="${esc(run.id)}" type="button" title="Remover do histórico">×</button>
          </div>
        </div>
      </article>`;
  }).join("");

  jobsListEl.querySelectorAll("[data-delete-hist]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const rid = btn.dataset.deleteHist;
      try { await fetchJson(`/api/historico/${rid}`, { method: "DELETE" }); } catch (_) {}
      const el = jobsListEl.querySelector(`[data-hist-id="${CSS.escape(rid)}"]`);
      if (el) el.remove();
      if (!jobsListEl.querySelector(".job"))
        jobsListEl.innerHTML = `<div class="empty">Ainda não há auditorias.</div>`;
    });
  });
}

async function refreshJobs({ silent = false } = {}) {
  try {
    const data    = await fetchJson("/api/jobs");
    const allJobs = data.jobs || [];
    const visible = allJobs.filter(j => !hiddenJobIds.has(j.id));
    const hasActive = allJobs.some(j => ["pendente", "a_correr"].includes(j.state) && !hiddenJobIds.has(j.id));
    if (visible.length) {
      renderJobs(allJobs);
    } else {
      try {
        const hist = await fetchJson("/api/historico?limite=20");
        renderHistorico(hist.runs || []);
      } catch (_) {
        jobsListEl.innerHTML = `<div class="empty">Ainda não há auditorias.</div>`;
      }
    }
    return hasActive;
  } catch (e) {
    if (!silent) showToast(e.message, true);
    jobsListEl.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return false;
  }
}

(async function init() {
  setMode("single");
  const [hasActive] = await Promise.all([
    refreshJobs({ silent: true }),
    refreshReports({ silent: true, force: true }),
  ]);
  schedulePolling(hasActive);
})();

})();