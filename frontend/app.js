"use strict";

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "value") node.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected") node[k] = !!v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// Long operations run as background jobs (mirrors LONG_OPERATIONS in backend/app/jobs.py).
const LONG_OPS = [/\/analysis\/run$/, /\/hypotheses$/, /\/findings\/[^/]+\/interpret$/, /\/literature\/identifiers$/,
  /\/literature\/sources\/[^/]+\/extract$/, /\/literature\/findings\/[^/]+\/compare$/, /\/literature\/ground$/,
  /\/literature\/retractions$/, /\/manuscript\/terms\/auto$/, /\/manuscript\/plan$/, /\/manuscript\/sections\/[^/]+\/generate$/,
  /\/manuscript\/sections\/[^/]+\/revise$/, /\/manuscript\/front\/generate$/, /\/cover\/generate$/, /\/cover\/revise$/,
  /^\/journals\/[^/]+\/extract$/, /^\/journals\/[^/]+\/recheck$/];

async function rawApi(path, opts = {}) {
  const init = { ...opts, headers: { "X-AIA": "1", ...(opts.headers || {}) } };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
    init.method = init.method || "POST";
  }
  const res = await fetch("/api" + path, init);
  if (res.status === 401 && !path.startsWith("/auth/")) {
    state.user = null;
    if (!location.hash.startsWith("#/login")) location.hash = "#/login";
    throw new Error("требуется вход");
  }
  if (!res.ok) {
    let msg = res.statusText;
    let detail = null;
    try { detail = (await res.json()).detail; msg = detail || msg; } catch (_) { /* not json */ }
    if (typeof msg !== "string") msg = msg.message || JSON.stringify(msg);
    const err = new Error(msg);
    err.detail = detail;
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

async function waitJob(jobId, onTick) {
  for (;;) {
    await new Promise((r) => setTimeout(r, 1200));
    const j = await rawApi(`/jobs/${jobId}`);
    if (onTick) onTick(j);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error || "задача завершилась с ошибкой");
  }
}

async function api(path, opts = {}) {
  const method = (opts.method || (opts.json !== undefined ? "POST" : "GET")).toUpperCase();
  const bare = path.split("?")[0];
  if (method === "POST" && LONG_OPS.some((rx) => rx.test(bare.replace(/^\/projects\/[^/]+/, "")) || rx.test(bare))) {
    const sep = path.includes("?") ? "&" : "?";
    const r = await rawApi(path + sep + "async=1", opts);
    if (r && r.job_id) return waitJob(r.job_id);
    return r;
  }
  return rawApi(path, opts);
}

function toast(message, kind) {
  const t = el("div", { class: "toast" + (kind === "error" ? " error" : ""), text: message });
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), kind === "error" ? 7000 : 3500);
}

async function busy(button, fn) {
  const label = button.innerHTML;
  button.disabled = true;
  button.replaceChildren(el("span", { class: "spinner" }), " " + button.textContent);
  try { return await fn(); }
  catch (e) { toast(e.message, "error"); }
  finally { button.disabled = false; button.innerHTML = label; }
}

const fmtDate = (iso) => iso ? new Date(iso).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" }) : "—";
const fmtP = (p) => p === null || p === undefined ? "—" : (p < 0.001 ? "< 0.001" : p.toFixed(3));
const fmtNum = (x) => {
  if (x === null || x === undefined) return "—";
  const a = Math.abs(x);
  return x.toFixed(a >= 100 ? 0 : a >= 10 ? 1 : a >= 0.1 || a === 0 ? 2 : 3);
};

const EVIDENCE = {
  significant: "значимая после поправки",
  exploratory: "exploratory",
  descriptive: "описательная",
};
const VTYPES = { binary: "бинарный", categorical: "категориальный", ordinal: "порядковый",
  quantitative: "количественный", identifier: "идентификатор", text: "свободный текст" };
const GROUPS = { clinical: "клиника", morphology: "морфология", ihc: "ИГХ", molecular: "молекулярный",
  followup: "наблюдение", other: "прочее" };
const STAGE_LABEL = { created: "создан", data_pending: "данные: проверка анонимизации",
  data_confirmed: "данные подтверждены", analysis: "анализ" };

const STEPS = [
  { id: "project", title: "Проект", sub: "фокус, журнал, тип статьи" },
  { id: "data", title: "Загрузка данных", sub: "анонимизация и подтверждение" },
  { id: "cases", title: "Структурирование", sub: "случаи × признаки, словарь" },
  { id: "analysis", title: "Анализ", sub: "находки и журнал гипотез" },
  { id: "literature", title: "Литература", sub: "база источников, новизна" },
  { id: "outline", title: "Структура статьи", sub: "по профилю журнала" },
  { id: "draft", title: "Генерация разделов", sub: "план, разделы, версии" },
  { id: "checks", title: "Проверки", sub: "чек-лист, этика, заимствования" },
  { id: "cover", title: "Cover letter", sub: "письмо редактору" },
  { id: "export", title: "Экспорт", sub: ".docx, рисунки, supplementary" },
];

const state = { meta: null, project: null, analysisTab: "findings", casesTab: "table", filter: "all",
  litTab: "sources", litExpanded: null, litQuery: "", styles: null };

/* ------------------------------------------------------------------ *
 * Routing
 * ------------------------------------------------------------------ */

async function route() {
  const app = document.getElementById("app");
  const parts = location.hash.replace(/^#\/?/, "").split("?")[0].split("/").filter(Boolean);
  try {
    if (parts[0] === "login" || parts[0] === "register") {
      renderLogin(app, parts[0] === "register");
      return;
    }
    if (!state.user) { location.hash = "#/login"; return; }
    if (!state.meta) await loadMeta();
    if (parts[0] === "account") {
      await renderAccount(app);
    } else if (parts[0] === "journals") {
      if (parts[1]) await renderJournal(app, parts[1]);
      else await renderJournals(app);
    } else if (parts[0] === "p" && parts[1]) {
      state.project = await api(`/projects/${parts[1]}`);
      const step = STEPS.find((s) => s.id === parts[2]) ? parts[2] : defaultStep(state.project);
      await renderProject(app, step);
    } else {
      await renderDashboard(app);
    }
  } catch (e) {
    app.replaceChildren(el("div", { class: "card empty" }, el("p", { text: e.message }), el("a", { href: "#/", text: "← К проектам" })));
  }
  window.scrollTo(0, 0);
}

function defaultStep(p) {
  if (p.stage === "analysis") return "analysis";
  if (p.stage === "data_confirmed") return "cases";
  return "data";
}

/* ------------------------------------------------------------------ *
 * Dashboard
 * ------------------------------------------------------------------ */

async function renderDashboard(app) {
  const projects = await api("/projects");
  const list = projects.length
    ? el("div", { class: "projects" }, projects.map((p) => el("a", { class: "card project-card", href: `#/p/${p.id}` },
        el("h3", { text: p.title }),
        el("div", { class: "meta" },
          el("span", { class: "badge accent", text: STAGE_LABEL[p.stage] || p.stage }),
          p.journal && el("span", { text: p.journal }),
          el("span", { text: state.meta.article_types[p.article_type] || p.article_type })),
        el("p", { class: "small muted", style: "margin-top:8px" }, `n = ${p.n_cases || 0} · изменён ${fmtDate(p.updated_at)}`))))
    : el("div", { class: "card empty" }, el("p", { text: "Проектов пока нет. Создайте первый — например, по серии случаев редкой опухоли." }));

  app.replaceChildren(
    el("div", { class: "row between", style: "margin-bottom:16px" }, el("h1", { text: "Проекты статей" })),
    projectForm(null),
    el("h2", { style: "margin-top:28px", text: "Мои проекты" }),
    list,
  );
}

function projectForm(project) {
  const f = {
    title: el("input", { value: project?.title || "", placeholder: "Например: PDGFRA-мутантные ГИСО желудка — серия 24 случаев", required: true }),
    focus: el("textarea", { placeholder: "Фокус публикации: что интересного в серии, какой вопрос хотите изучить" }, project?.focus || ""),
    journal: el("input", { value: project?.journal || "", list: "journals", placeholder: "Начните вводить или выберите из списка" }),
    type: el("select", {}, Object.entries(state.meta.article_types).map(([k, v]) =>
      el("option", { value: k, selected: (project?.article_type || "case_series") === k, text: v }))),
  };
  const datalist = el("datalist", { id: "journals" }, state.meta.journals.map((j) => el("option", { value: j.name, text: j.publisher })));
  const submit = el("button", { class: "primary", type: "submit", text: project ? "Сохранить" : "Создать проект" });
  const form = el("form", { class: "card stack", onsubmit: async (e) => {
      e.preventDefault();
      const body = { title: f.title.value.trim(), focus: f.focus.value.trim(), article_type: f.type.value };
      if (!project) body.journal = f.journal.value.trim();
      await busy(submit, async () => {
        if (project) {
          state.project = await api(`/projects/${project.id}`, { method: "PATCH", json: body });
          toast("Сохранено");
          route();
        } else {
          const p = await api("/projects", { json: body });
          const match = (await api("/journals")).find((j) => j.name.toLowerCase() === body.journal.toLowerCase());
          if (match) await api(`/projects/${p.id}/journal`, { method: "PUT", json: { journal_id: match.id } });
          location.hash = `#/p/${p.id}/data`;
        }
      });
    } },
    el("h2", { text: project ? "Параметры проекта" : "Новый проект" }),
    el("div", { class: "form-grid" },
      el("label", { class: "field" }, el("span", { text: "Рабочее название" }), f.title),
      project ? null : el("label", { class: "field" }, el("span", { text: "Целевой журнал" }), f.journal, datalist),
      el("label", { class: "field" }, el("span", { text: "Тип статьи" }), f.type)),
    el("label", { class: "field" }, el("span", { text: "Описание фокуса" }), f.focus),
    el("div", { class: "row" }, submit,
      el("span", { class: "hint", text: project ? "Профиль журнала выбирается ниже, из базы журналов." : "После создания выберите журнал из базы профилей (шаг «Проект»)." })),
  );
  return form;
}

/* ------------------------------------------------------------------ *
 * Project shell
 * ------------------------------------------------------------------ */

function stepState(p, id) {
  const stageIdx = { created: 1, data_pending: 1, data_confirmed: 3, analysis: 4 }[p.stage] || 1;
  const idx = STEPS.findIndex((s) => s.id === id) + 1;
  if (STEPS[idx - 1].soon) return "locked";
  if (idx === 1) return "done";
  if (idx === 2) return p.anonymization.status === "confirmed" ? "done" : "open";
  if (idx === 3) return stageIdx >= 3 ? "done" : "locked";
  if (idx === 4) return stageIdx >= 3 ? (p.stage === "analysis" ? "done" : "open") : "locked";
  if (idx === 5) return "open";
  if (idx === 6) return p.journal_id ? "open" : "locked";
  if (idx === 8) return p.journal_id || p.stage === "analysis" ? "open" : "locked";
  if (idx === 7 || idx === 10) return p.stage === "analysis" ? "open" : "locked";
  if (idx === 9) return p.stage === "analysis" && p.journal_id ? "open" : "locked";
  return "locked";
}

async function renderProject(app, step) {
  const p = state.project;
  const nav = el("nav", { class: "card steps" }, el("ol", {}, STEPS.map((s, i) => {
    const st = stepState(p, s.id);
    const cls = ["step", st === "done" ? "done" : "", st === "locked" ? "locked" : "", s.id === step ? "current" : ""].join(" ");
    const inner = [el("span", { class: "step-num", text: st === "done" && s.id !== step ? "✓" : String(i + 1) }),
      el("span", {}, s.title, el("small", { text: s.soon ? "MVP · в разработке" : s.sub }))];
    return el("li", {}, st === "locked"
      ? el("span", { class: cls, title: s.soon ? "Этап появится в MVP (см. PRD, раздел 10)" : "Сначала завершите предыдущие этапы" }, inner)
      : el("a", { class: cls, href: `#/p/${p.id}/${s.id}` }, inner));
  })));

  const head = el("div", { class: "project-head" },
    el("div", { class: "row between" },
      el("h1", { text: p.title }),
      el("a", { href: "#/", class: "small", text: "← Все проекты" })),
    el("div", { class: "meta" },
      el("span", { class: "badge accent", text: STAGE_LABEL[p.stage] || p.stage }),
      p.journal && el("span", { text: "Журнал: " + p.journal }),
      el("span", { text: state.meta.article_types[p.article_type] }),
      el("span", { text: `n = ${p.n_cases || 0}` })));

  const content = el("section", {});
  const jobsBox = el("div", {});
  app.replaceChildren(el("div", { class: "project-layout" }, nav, el("div", {}, head, jobsBox, content)));
  watchJobs(p.id, jobsBox);
  const view = { project: viewProject, data: viewData, cases: viewCases, analysis: viewAnalysis,
    literature: viewLiterature, outline: viewOutline, checks: viewChecksHub, draft: viewDraft, cover: viewCover, export: viewExport }[step];
  await view(content, p);
}

/* ---------- step 1: project ---------- */

async function viewProject(root, p) {
  const audit = await api(`/projects/${p.id}/audit`);
  const del = el("button", { class: "danger small", text: "Удалить проект", onclick: async () => {
    if (!confirm("Удалить проект со всеми данными и результатами анализа? Действие необратимо.")) return;
    await api(`/projects/${p.id}`, { method: "DELETE" });
    location.hash = "#/";
  } });
  root.replaceChildren(
    projectForm(p),
    await projectJournalCard(p),
    el("div", { class: "card" },
      el("div", { class: "row between" }, el("h2", { text: "Журнал аудита" }), del),
      el("p", { class: "hint", text: "Кто и когда что изменил, какие действия выполнял агент (FR-0.7). Содержимое данных в журнал не пишется." }),
      el("div", { class: "table-wrap", style: "max-height:360px" }, el("table", {},
        el("thead", {}, el("tr", {}, ["Время", "Кто", "Действие", "Детали"].map((h) => el("th", { text: h })))),
        el("tbody", {}, audit.map((a) => el("tr", {},
          el("td", { class: "small", text: fmtDate(a.ts) }),
          el("td", { text: { author: "автор", agent: "агент", system: "система" }[a.actor] || a.actor }),
          el("td", { class: "mono", text: a.action }),
          el("td", { class: "small muted", text: JSON.stringify(a.details) }))))))),
  );
}

/* ---------- step 2: upload & anonymization ---------- */

async function viewData(root, p) {
  const { pending } = await api(`/projects/${p.id}/anonymization`);
  const input = el("input", { type: "file", multiple: true, accept: ".xlsx,.xls,.csv,.tsv,.json", class: "hidden" });
  const upload = async (files) => {
    if (!files.length) return;
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    zone.replaceChildren(el("span", { class: "spinner" }), " Загрузка и локальная анонимизация…");
    try {
      await api(`/projects/${p.id}/uploads`, { method: "POST", body: fd });
      toast("Файл обработан — проверьте отчёт об анонимизации");
    } catch (e) { toast(e.message, "error"); }
    route();
  };
  input.addEventListener("change", () => upload(input.files));
  const zone = el("div", { class: "dropzone", onclick: () => input.click(),
      ondragover: (e) => { e.preventDefault(); zone.classList.add("drag"); },
      ondragleave: () => zone.classList.remove("drag"),
      ondrop: (e) => { e.preventDefault(); zone.classList.remove("drag"); upload(e.dataTransfer.files); } },
    el("h3", { text: "Перетащите файлы с данными серии или нажмите для выбора" }),
    el("p", { class: "hint", text: "Прототип принимает .xlsx, .csv, .tsv, .json (строка = случай). Файлы разбираются на сервере; исходник не сохраняется, во внешние сервисы до вашего подтверждения ничего не передаётся." }));

  const parts = [];
  if (!["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) {
    parts.push(el("div", { class: "banner warn" },
      el("strong", { text: "Вы работаете через публичный адрес. " }),
      "Трафик проходит через провайдера туннеля, который видит загружаемые файлы до анонимизации. Файлы с персональными данными пациентов загружайте только при локальном доступе (http://localhost) или заранее обезличенными."));
  }
  if (p.anonymization.status === "confirmed" && !pending.length) {
    parts.push(el("div", { class: "banner ok" },
      el("strong", { text: "Анонимизация подтверждена " }), `${fmtDate(p.anonymization.confirmed_at)}. `,
      "Данные доступны для анализа. ", el("a", { href: `#/p/${p.id}/cases`, text: "Перейти к структурированию →" })));
  }
  parts.push(input, zone);
  for (const rep of pending) parts.push(reportCard(p, rep));
  if (pending.length) {
    const check = el("input", { type: "checkbox", id: "ack" });
    const btn = el("button", { class: "primary", disabled: true, text: "Подтвердить анонимизацию" });
    check.addEventListener("change", () => { btn.disabled = !check.checked; });
    btn.addEventListener("click", () => busy(btn, async () => {
      await api(`/projects/${p.id}/anonymization/confirm`, { method: "POST" });
      toast("Анонимизация подтверждена");
      location.hash = `#/p/${p.id}/cases`;
    }));
    parts.push(el("div", { class: "card stack" },
      el("h2", { text: "Подтверждение (FR-1.5)" }),
      el("label", { class: "row", style: "align-items:flex-start;flex-wrap:nowrap" }, check,
        el("span", { text: "Я проверил(а) отчёт и таблицу после анонимизации: прямых идентификаторов не осталось, квазиидентификаторы допустимы для этой серии." })),
      el("div", { class: "row" }, btn,
        el("span", { class: "hint", text: "До подтверждения запросы к внешней LLM для проекта заблокированы." }))));
  }
  root.replaceChildren(el("div", { class: "stack" }, parts));
}

function reportCard(p, rep) {
  const counts = rep.counts || {};
  const labels = { column_removed: "столбцов удалено", name: "ФИО", phone: "телефоны", snils: "СНИЛС", policy: "полисы",
    passport: "паспорта", email: "e-mail", address: "адреса", record: "номера ИБ", block: "номера блоков",
    date: "даты → интервалы", birth_date: "даты рождения" };
  const preview = el("div", {}, el("p", { class: "hint", text: "Загрузка…" }));
  api(`/projects/${p.id}/anonymization/preview/${rep.upload_id}`).then((d) => {
    preview.replaceChildren(dataTable(d.columns, d.rows, { maxHeight: "340px" }));
  }).catch((e) => preview.replaceChildren(el("p", { class: "hint", text: e.message })));

  const discardBtn = el("button", { class: "small danger", text: "Отменить загрузку", onclick: async () => {
    await api(`/projects/${p.id}/anonymization/discard?upload_id=${rep.upload_id}`, { method: "POST" });
    route();
  } });

  return el("div", { class: "card stack" },
    el("div", { class: "row between" },
      el("div", {}, el("h2", { text: "Отчёт об анонимизации: " + rep.file }),
        el("div", { class: "meta" }, `${rep.n_rows} строк · столбцов ${rep.n_columns_in} → ${rep.n_columns_out} · ${fmtDate(rep.uploaded_at)}`)),
      discardBtn),
    el("div", { class: "chips" }, Object.entries(counts).map(([k, v]) => el("span", { class: "badge accent", text: `${labels[k] || k}: ${v}` }))),
    rep.notes.length ? el("div", { class: "banner info" }, rep.notes.map((n) => el("div", { text: n }))) : null,
    el("h3", { text: "Столбцы" }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Столбец", "Действие", "Детали"].map((h) => el("th", { text: h })))),
      el("tbody", {}, rep.columns.map((c) => el("tr", {}, el("td", { text: c.column }), el("td", { text: c.action }), el("td", { class: "small", text: c.detail })))))),
    rep.quasi.length ? el("div", { class: "banner warn" },
      el("strong", { text: "Требуют ручной проверки (квазиидентификаторы)" }),
      el("ul", {}, rep.quasi.slice(0, 30).map((q) => el("li", { text: `${q.column}${q.row ? ", строка " + q.row : ""}${q.preview ? " (" + q.preview + ")" : ""}: ${q.reason}` })))) : null,
    el("details", {},
      el("summary", { text: `Замены в тексте ячеек (${rep.hits.length}${rep.hits_truncated ? "+" : ""})` }),
      el("div", { class: "table-wrap", style: "margin-top:8px;max-height:320px" }, el("table", {},
        el("thead", {}, el("tr", {}, ["Тип", "Столбец", "Строка", "Было (маска)", "Стало"].map((h) => el("th", { text: h })))),
        el("tbody", {}, rep.hits.map((h) => el("tr", {},
          el("td", { text: h.label }), el("td", { text: h.column }), el("td", { class: "num", text: h.row }),
          el("td", { class: "mono", text: h.preview }), el("td", { class: "mono", text: h.replacement }))))))),
    el("details", { open: true }, el("summary", { text: "Таблица после анонимизации (первые 50 строк)" }), preview),
  );
}

function dataTable(columns, rows, opts = {}) {
  const cols = columns.filter((c) => !c.startsWith("_"));
  return el("div", { class: "table-wrap", style: opts.maxHeight ? `max-height:${opts.maxHeight}` : null },
    el("table", { class: "cases" },
      el("thead", {}, el("tr", {}, cols.map((c) => el("th", { text: c })))),
      el("tbody", {}, rows.map((r) => el("tr", {}, cols.map((c) => el("td", { class: r[c] ? "" : "missing", title: r[c] || "", text: r[c] || "—" })))))));
}

/* ---------- step 3: cases & dictionary ---------- */

async function viewCases(root, p) {
  const ds = await api(`/projects/${p.id}/dataset`);
  if (!ds.rows.length) {
    root.replaceChildren(el("div", { class: "card empty" }, "Данные ещё не подтверждены. ", el("a", { href: `#/p/${p.id}/data`, text: "Загрузить данные" })));
    return;
  }
  const tabs = el("div", { class: "tabs" }, [["table", `Случаи × признаки (${ds.rows.length})`], ["dictionary", "Словарь данных"]].map(([id, label]) =>
    el("button", { class: "tab" + (state.casesTab === id ? " active" : ""), text: label, onclick: () => { state.casesTab = id; viewCases(root, p); } })));
  const body = state.casesTab === "table" ? casesTable(p, ds) : dictionaryEditor(p, ds);
  root.replaceChildren(tabs, body);
}

function casesTable(p, ds) {
  const problems = new Map(ds.problems.map((x) => [`${x.case_id}|${x.column}`, x.issue]));
  const cols = ds.columns;
  const legend = el("div", { class: "row small" },
    el("span", { class: "badge", text: "— пропуск" }),
    el("span", { class: "badge danger", text: "не распознано / вне словаря" }),
    el("span", { class: "hint", text: "Двойной клик по ячейке — редактирование. Наведите на ID случая, чтобы увидеть источник (файл, строка)." }),
    el("a", { class: "btn small", href: `/api/projects/${p.id}/dataset/export.csv`, text: "Экспорт .csv" }));
  const table = el("table", { class: "cases" },
    el("thead", {}, el("tr", {}, cols.map((c, i) => el("th", { class: i === 0 ? "sticky" : "", text: c })))),
    el("tbody", {}, ds.rows.map((r) => el("tr", {}, cols.map((c, i) => {
      if (i === 0) {
        const src = (ds.provenance[r.case_id] || []).map((s) => `${s.file}, строка ${s.row}`).join("; ");
        return el("td", { class: "sticky mono", title: "Источник: " + src, text: r[c] });
      }
      const issue = problems.get(`${r.case_id}|${c}`);
      const td = el("td", { class: "editable " + (issue ? "problem" : r[c] ? "" : "missing"), title: issue || r[c] || "", text: r[c] || "—" });
      td.addEventListener("dblclick", () => editCell(p, td, r, c));
      return td;
    })))));
  return el("div", { class: "stack" },
    ds.problems.length ? el("div", { class: "banner warn", text: `Проблемных значений: ${ds.problems.length}. Они исключаются из анализа, пока не будут исправлены.` }) : null,
    legend, el("div", { class: "table-wrap" }, table));
}

function editCell(p, td, row, column) {
  const input = el("input", { value: row[column] || "" });
  td.replaceChildren(input);
  input.focus();
  const save = async () => {
    const value = input.value.trim();
    if (value === (row[column] || "")) { td.textContent = row[column] || "—"; return; }
    try {
      await api(`/projects/${p.id}/dataset`, { method: "PATCH", json: { case_id: row.case_id, column, value } });
      toast("Значение сохранено; перезапустите анализ, чтобы учесть изменения");
      route();
    } catch (e) { toast(e.message, "error"); td.textContent = row[column] || "—"; }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); if (e.key === "Escape") { input.value = row[column] || ""; input.blur(); } });
  input.addEventListener("blur", save);
}

function dictionaryEditor(p, ds) {
  const items = ds.dictionary.map((v) => ({ ...v }));
  const rows = items.map((v) => {
    const vtype = el("select", { onchange: (e) => { v.vtype = e.target.value; } },
      Object.entries(VTYPES).map(([k, lbl]) => el("option", { value: k, selected: v.vtype === k, text: lbl })));
    const group = el("select", { onchange: (e) => { v.group = e.target.value; } },
      Object.entries(GROUPS).map(([k, lbl]) => el("option", { value: k, selected: v.group === k, text: lbl })));
    const levels = el("input", { value: (v.levels || []).join(" | "), placeholder: "уровни через «|», по порядку",
      onchange: (e) => { v.levels = e.target.value.split("|").map((s) => s.trim()).filter(Boolean); } });
    const include = el("input", { type: "checkbox", checked: v.include, onchange: (e) => { v.include = e.target.checked; } });
    return el("tr", {},
      el("td", {}, el("div", { text: v.name }), v.edited ? el("span", { class: "badge accent", text: "изменён автором" }) : null),
      el("td", {}, group), el("td", {}, vtype),
      el("td", { style: "min-width:260px" }, levels, v.unit ? el("div", { class: "small muted", text: "единицы: " + v.unit }) : null),
      el("td", { class: "num", text: `${v.n_present} / ${v.n_missing}` }),
      el("td", {}, include));
  });
  const save = el("button", { class: "primary", text: "Сохранить словарь" });
  save.addEventListener("click", () => busy(save, async () => {
    await api(`/projects/${p.id}/dictionary`, { method: "PUT", json: items });
    toast("Словарь сохранён");
    route();
  }));
  return el("div", { class: "stack" },
    el("p", { class: "hint", text: "Тип признака определяет статистический тест (FR-1.11, PRD 2.2) — LLM тест не выбирает. Для порядковых признаков порядок уровней задаёт ранги; для бинарных первый уровень — референсный." }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Признак", "Группа", "Тип", "Уровни", "Есть / пропуск", "В анализ"].map((h) => el("th", { text: h })))),
      el("tbody", {}, rows))),
    el("div", { class: "row" }, save, el("a", { class: "btn", href: `#/p/${p.id}/analysis`, text: "К анализу →" })));
}

/* ---------- step 4: analysis ---------- */

async function viewAnalysis(root, p) {
  if (p.anonymization.status !== "confirmed") {
    root.replaceChildren(el("div", { class: "card empty" }, "Анализ доступен после подтверждения анонимизации. ", el("a", { href: `#/p/${p.id}/data`, text: "К данным" })));
    return;
  }
  const res = await api(`/projects/${p.id}/analysis`);
  const runBtn = el("button", { class: "primary", text: res.summary ? "Перезапустить анализ" : "Запустить анализ" });
  runBtn.addEventListener("click", () => busy(runBtn, async () => {
    await api(`/projects/${p.id}/analysis/run`, { method: "POST" });
    toast("Анализ выполнен");
    route();
  }));
  const header = el("div", { class: "card" },
    el("div", { class: "row between" },
      el("div", {},
        el("h2", { text: "Аналитический движок" }),
        el("div", { class: "meta" }, (res.summary
          ? [`Прогон ${res.summary.run_id} · ${fmtDate(res.summary.created_at)}`, `n = ${res.summary.n}`,
             `тестов: ${res.summary.n_tests}`, `находок: ${res.summary.n_findings}`]
          : ["Анализ ещё не запускался"]).map((t) => el("span", { text: t })))),
      el("div", { class: "row" }, runBtn,
        res.summary ? el("a", { class: "btn", href: `/api/projects/${p.id}/analysis/export.zip`, text: "Скрипт анализа (.zip)" }) : null)),
    res.guardrails ? guardrailBanner(res) : null);

  if (!res.summary) {
    root.replaceChildren(header, el("div", { class: "card empty" },
      el("p", { text: "Движок посчитает описательную статистику, проверит попарные ассоциации тестом, выбранным по типам признаков, применит FDR-поправку, найдёт выбросы и нетипичные профили, построит кластеризацию." }),
      el("p", { class: "hint", text: "Числа считаются кодом в отдельном процессе; скрипт сохраняется и воспроизводит результат." })));
    return;
  }
  const tabs = [["findings", `Находки (${res.findings.length})`], ["table1", "Таблица 1"], ["patterns", "Паттерны"],
    ["log", `Журнал гипотез (${res.hypotheses.length})`], ["ask", "Свой вопрос"]];
  const tabBar = el("div", { class: "tabs" }, tabs.map(([id, label]) =>
    el("button", { class: "tab" + (state.analysisTab === id ? " active" : ""), text: label, onclick: () => { state.analysisTab = id; viewAnalysis(root, p); } })));
  const body = { findings: findingsView, table1: table1View, patterns: patternsView, log: logView, ask: askView }[state.analysisTab](p, res, root);
  root.replaceChildren(el("div", { class: "stack" }, header, el("div", {}, tabBar, body)));
}

function guardrailBanner(res) {
  const g = res.guardrails;
  const cls = g.code === "descriptive" || g.code === "minimal" ? "warn" : "info";
  return el("div", { class: "banner " + cls, style: "margin-top:14px" },
    el("strong", { text: `Статистические ограничители: ${g.label} (${g.n_range}, в серии n = ${res.summary.n})` }),
    el("ul", {}, [...g.rules, ...g.common_rules].map((r) => el("li", { text: r }))));
}

function findingsView(p, res, root) {
  const filters = [["all", "Все"], ["significant", "Значимые"], ["exploratory", "Exploratory"], ["descriptive", "Описательные"],
    ["accepted", "Принятые"], ["rejected", "Отклонённые"], ["user", "Вопросы автора"]];
  const match = (f) => state.filter === "all" || f.evidence === state.filter || f.status === state.filter || (state.filter === "user" && f.origin === "user");
  const list = res.findings.filter(match);
  return el("div", {},
    el("div", { class: "filters" }, filters.map(([id, label]) => el("button", {
      class: "filter" + (state.filter === id ? " on" : ""), text: label,
      onclick: () => { state.filter = id; viewAnalysis(root, p); } }))),
    list.length ? el("div", { class: "findings" }, list.map((f) => findingCard(p, f, root)))
      : el("div", { class: "card empty", text: "Нет находок с таким фильтром." }));
}

function contingency(r) {
  if (!r || r.kind !== "cat_cat") return null;
  return el("table", { class: "ct" },
    el("thead", {}, el("tr", {}, el("th", { text: `${r.a} \\ ${r.b}` }), r.levels_b.map((l) => el("th", { text: l })))),
    el("tbody", {}, r.table.map((row, i) => el("tr", {}, el("th", { text: r.levels_a[i] }), row.map((x) => el("td", { text: x }))))));
}

function findingCard(p, f, root) {
  const reload = () => viewAnalysis(root, p);
  const setStatus = async (status) => {
    try {
      await api(`/projects/${p.id}/findings/${f.id}`, { json: { status: f.status === status ? "proposed" : status } });
      reload();
    } catch (e) { toast(e.message, "error"); }
  };
  const commentBox = el("div", { class: "hidden", style: "margin-top:10px" });
  const commentInput = el("textarea", { placeholder: "Комментарий к находке" });
  commentBox.append(commentInput, el("div", { class: "row", style: "margin-top:6px" },
    el("button", { class: "small primary", text: "Сохранить комментарий", onclick: async () => {
      if (!commentInput.value.trim()) return;
      await api(`/projects/${p.id}/findings/${f.id}`, { json: { comment: commentInput.value.trim() } });
      reload();
    } })));

  const llmOn = state.meta.llm.available;
  const interpretBtn = el("button", { class: "small", text: "Интерпретация ИИ", disabled: !llmOn,
    title: llmOn ? `Модель ${state.meta.llm.reasoning_model}: альтернативные объяснения и ограничения` : state.meta.llm.reason });
  interpretBtn.addEventListener("click", () => busy(interpretBtn, async () => {
    await api(`/projects/${p.id}/findings/${f.id}/interpret`, { method: "POST" });
    reload();
  }));

  const figure = f.figure ? el("div", { class: "figure" },
    el("img", { loading: "lazy", alt: "График: " + f.title, src: `/api/projects/${p.id}/findings/${f.id}/figure.png?run=${encodeURIComponent(f.test_id || f.key)}` })) : null;

  const it = f.interpretation;
  return el("article", { class: `card finding ${f.evidence} ${f.status}` },
    el("div", { class: "finding-head" },
      el("span", { class: "finding-id", text: f.id }),
      el("h3", { text: f.title }),
      el("span", { class: "badge " + f.evidence, text: EVIDENCE[f.evidence] }),
      f.origin === "user" ? el("span", { class: "badge accent", text: "вопрос автора" }) : null,
      f.null_result ? el("span", { class: "badge", text: "ассоциация не выявлена" }) : null,
      f.status === "accepted" ? el("span", { class: "badge ok", text: "принята" }) : null,
      f.status === "rejected" ? el("span", { class: "badge danger", text: "отклонена" }) : null),
    el("div", { class: "finding-body" + (figure ? "" : " nofig") },
      el("div", {},
        el("dl", { class: "kv" },
          el("dt", { text: "Утверждение" }), el("dd", {}, f.statement, contingency(f.result)),
          el("dt", { text: "Статистика" }), el("dd", { class: "mono", text: f.statistics }),
          el("dt", { text: "Ограничения" }), el("dd", {}, el("ul", {}, f.limitations.map((l) => el("li", { text: l })))),
          [el("dt", { text: "Литература" }), el("dd", { class: "small" }, f.literature
            ? [el("span", { class: "badge accent", text: f.literature.label }), ` источников с данными: ${f.literature.n_sources_with_evidence} из ${f.literature.n_sources_in_base} в базе`,
               f.literature.novelty ? el("div", { style: "margin-top:4px", text: "Новизна (подтверждена): " + f.literature.novelty }) : null]
            : el("a", { href: `#/p/${p.id}/literature`, text: "не сопоставлена — шаг «Литература»" }))],
          f.explanation ? [el("dt", { text: "Логика" }), el("dd", { class: "small" },
            el("div", { text: "Данные: " + f.explanation.data }),
            el("div", { text: "Метод: " + f.explanation.method }),
            el("div", { text: "Почему: " + f.explanation.why }))] : null),
        f.wording_en ? el("details", {}, el("summary", { text: "Формулировка для статьи (согласована с уровнем доказательности)" }),
          el("p", { style: "margin-top:8px;font-style:italic", text: f.wording_en })) : null,
        f.code ? el("details", {}, el("summary", { text: "Код анализа" }), el("pre", { text: f.code })) : null,
        it ? el("div", { class: "interpretation" },
          el("strong", { text: "Интерпретация ИИ " }), el("span", { class: "small muted", text: `${it.model} · ${fmtDate(it.created_at)}` }),
          el("p", { style: "margin-top:6px", text: it.interpretation }),
          el("div", { class: "small", text: "Альтернативные объяснения:" }), el("ul", {}, it.alternative_explanations.map((x) => el("li", { text: x }))),
          el("div", { class: "small", text: "Ограничения:" }), el("ul", {}, it.limitations.map((x) => el("li", { text: x }))),
          el("div", { class: "small", text: "Что проверить:" }), el("ul", {}, it.next_checks.map((x) => el("li", { text: x }))),
          it.unverified_numbers.length ? el("div", { class: "banner warn small", text: "Числа, которых нет в карточке (проверьте): " + it.unverified_numbers.join(", ") }) : null) : null,
        f.comments.map((c) => el("div", { class: "comment" }, el("span", { class: "muted small", text: fmtDate(c.ts) + " · " }), c.text)),
        commentBox),
      figure),
    el("div", { class: "actions" },
      el("button", { class: "small " + (f.status === "accepted" ? "active-ok" : "ok"), text: f.status === "accepted" ? "✓ Принята" : "Принять", onclick: () => setStatus("accepted") }),
      el("button", { class: "small " + (f.status === "rejected" ? "active-danger" : "danger"), text: f.status === "rejected" ? "✕ Отклонена" : "Отклонить", onclick: () => setStatus("rejected") }),
      el("button", { class: "small", text: "Комментировать", onclick: () => { commentBox.classList.toggle("hidden"); commentInput.focus(); } }),
      interpretBtn));
}

function table1View(p, res) {
  const rows = [];
  for (const v of res.table1) {
    if (v.vtype === "quantitative") {
      rows.push(el("tr", {}, el("td", {}, el("strong", { text: v.variable })),
        el("td", { text: v.median === undefined ? "—" : `${fmtNum(v.median)} [${fmtNum(v.q1)}–${fmtNum(v.q3)}]` }),
        el("td", { text: v.min === undefined ? "—" : `${fmtNum(v.min)}–${fmtNum(v.max)}` }),
        el("td", { class: "num", text: `${v.n} / ${v.missing}` })));
    } else {
      rows.push(el("tr", {}, el("td", {}, el("strong", { text: v.variable })), el("td", {}), el("td", {}),
        el("td", { class: "num", text: `${v.n} / ${v.missing}` })));
      for (const c of v.counts || []) {
        rows.push(el("tr", {}, el("td", { style: "padding-left:26px", text: c.level }),
          el("td", { text: `${c.count} (${c.percent ?? "—"}%)` }), el("td", {}), el("td", {})));
      }
    }
  }
  return el("div", { class: "stack" },
    el("p", { class: "hint", text: "Клинико-патологическая характеристика серии (FR-2.1): медиана [IQR] и диапазон для количественных признаков, n (%) — для категориальных." }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Признак", "Медиана [IQR] / n (%)", "Диапазон", "Есть / пропуск"].map((h) => el("th", { text: h })))),
      el("tbody", {}, rows))));
}

function patternsView(p, res) {
  const cl = res.clustering;
  const img = (name, alt) => {
    const box = el("div", {});
    const image = el("img", { alt, src: `/api/projects/${p.id}/analysis/figures/${name}.png?run=${res.summary.run_id}` });
    image.addEventListener("error", () => box.replaceChildren(el("p", { class: "hint", text: "График недоступен для текущих данных." })));
    box.append(image);
    return box;
  };
  let clusterBlock;
  if (!cl) clusterBlock = el("p", { class: "hint", text: "Кластеризация не выполняется при n < 5 (guardrail)." });
  else if (cl.status !== "ok") clusterBlock = el("p", { class: "hint", text: cl.reason });
  else {
    clusterBlock = el("div", { class: "stack" },
      el("p", { class: "hint", text: `${cl.method}. ${res.guardrails.clustering === "visual" ? "При n < 10 — только визуализация." : "Устойчивость: bootstrap Jaccard по кластерам (≥ 0.75 — устойчивый, < 0.6 — неустойчивый)."}` }),
      img("heatmap", "Тепловая карта с дендрограммой"),
      res.guardrails.clustering === "stability" ? el("table", { class: "ct" },
        el("thead", {}, el("tr", {}, el("th", { text: "k" }), el("th", { text: "Размеры кластеров" }), el("th", { text: "Устойчивость (Jaccard)" }))),
        el("tbody", {}, cl.partitions.map((part) => {
          const sizes = {};
          part.labels.forEach((l) => { sizes[l] = (sizes[l] || 0) + 1; });
          return el("tr", {}, el("td", { text: part.k }), el("td", { text: Object.values(sizes).join(" / ") }),
            el("td", { text: Object.entries(part.stability || {}).map(([c, s]) => `#${c}: ${s ?? "—"}`).join("  ") }));
        }))) : null);
  }
  return el("div", { class: "overview" },
    el("div", { class: "card" }, el("h3", { text: "Кластеризация случаев (FR-2.3)" }), clusterBlock),
    el("div", { class: "card" }, el("h3", { text: "Профиль ИГХ / молекулярных альтераций (FR-2.5)" }),
      el("p", { class: "hint", text: "Бинарные ИГХ-маркеры и альтерации по случаям: взаимоисключение и ко-встречаемость видны по столбцам." }),
      img("oncoprint", "Oncoprint")));
}

function logView(p, res) {
  const rows = [...res.hypotheses].sort((a, b) => (a.p ?? 2) - (b.p ?? 2));
  return el("div", { class: "stack" },
    el("p", { class: "hint", text: "Все проверенные гипотезы, включая незначимые и не выполненные из-за ограничителей (FR-2.11). q — поправка Benjamini–Hochberg по всем выполненным тестам прогона. Этот журнал — основа честного раздела Methods." }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["ID", "Признак A", "Признак B", "Источник", "Тест", "n", "p", "q", "Эффект (95% ДИ)", "Статус"].map((h) => el("th", { text: h })))),
      el("tbody", {}, rows.map((h) => el("tr", {},
        el("td", { class: "mono", text: h.id || "—" }), el("td", { text: h.a }), el("td", { text: h.b }),
        el("td", { text: h.origin === "user" ? "автор" : "авто" }), el("td", { class: "small", text: h.test || "—" }),
        el("td", { class: "num", text: h.n ?? "—" }),
        el("td", { class: "num", text: fmtP(h.p) }), el("td", { class: "num", text: fmtP(h.q) }),
        el("td", { class: "small", text: h.effect ? `${h.effect.name} ${fmtNum(h.effect.value)} (${fmtNum(h.effect.ci_low)}–${fmtNum(h.effect.ci_high)})` : "—" }),
        el("td", { class: "small", text: h.reason ? `${h.status}: ${h.reason}` : h.status })))))));
}

function askView(p, res, root) {
  const vars = res.table1.map((v) => v.variable);
  const selA = el("select", {}, el("option", { value: "", text: "— признак A —" }), vars.map((v) => el("option", { value: v, text: v })));
  const selB = el("select", {}, el("option", { value: "", text: "— признак B —" }), vars.map((v) => el("option", { value: v, text: v })));
  const question = el("textarea", { placeholder: "Например: связана ли потеря экспрессии CD117 с мутацией PDGFRA?" });
  const rationale = el("p", { class: "hint" });
  const parseBtn = el("button", { text: "Разобрать вопрос" });
  parseBtn.addEventListener("click", () => busy(parseBtn, async () => {
    if (!question.value.trim()) return toast("Введите вопрос", "error");
    const r = await api(`/projects/${p.id}/hypotheses/parse`, { json: { question: question.value.trim() } });
    selA.value = r.a; selB.value = r.b;
    rationale.textContent = (r.source === "llm" ? `${state.meta.llm.extraction_model}: ` : "") + r.rationale;
  }));
  const testBtn = el("button", { class: "primary", text: "Проверить гипотезу" });
  testBtn.addEventListener("click", () => busy(testBtn, async () => {
    if (!selA.value || !selB.value) return toast("Выберите два признака", "error");
    await api(`/projects/${p.id}/hypotheses`, { json: { a: selA.value, b: selB.value, question: question.value.trim() || null } });
    toast("Гипотеза добавлена в журнал; анализ пересчитан с FDR по всем тестам");
    state.analysisTab = "findings"; state.filter = "user";
    viewAnalysis(root, p);
  }));
  return el("div", { class: "card stack" },
    el("h3", { text: "Своя гипотеза (FR-2.12)" }),
    el("p", { class: "hint", text: state.meta.llm.available
      ? "Вопрос на естественном языке сопоставляется с признаками словаря. В LLM передаются только названия и типы признаков — не данные случаев. Тест выбирается по типам признаков."
      : "Claude API не настроен — вопрос сопоставляется с признаками по названиям. Можно выбрать признаки вручную." }),
    question, el("div", { class: "row" }, parseBtn), rationale,
    el("div", { class: "form-grid" }, selA, selB),
    el("div", { class: "row" }, testBtn,
      el("span", { class: "hint", text: "Гипотеза попадает в журнал и в семейство FDR-поправки, даже если результат незначимый." })),
    res.user_hypotheses.length ? el("div", {}, el("h3", { text: "Заданные вопросы" }),
      el("ul", {}, res.user_hypotheses.map((h) => el("li", { text: `${h.a} × ${h.b}${h.question ? " — «" + h.question + "»" : ""}` })))) : null);
}

/* ------------------------------------------------------------------ *
 * Boot
 * ------------------------------------------------------------------ */

async function boot() {
  window.addEventListener("hashchange", route);
  try { state.user = await rawApi("/auth/me"); } catch (_) { state.user = null; }
  if (state.user) await loadMeta();
  renderUserMenu();
  route();
}

async function loadMeta() {
  state.meta = await api("/meta");
  const s = state.meta.llm;
  document.getElementById("llm-status").replaceChildren(
    el("span", { class: "dot " + (s.available ? "on" : "off") }),
    s.available ? (s.local ? `ИИ локально: ${s.reasoning_model}` : `${{ gemini: "Gemini", free: "ИИ (бесплатно)", openrouter: "OpenRouter" }[s.provider] || "Claude"}: ${s.reasoning_model}${s.extraction_model !== s.reasoning_model ? " / " + s.extraction_model : ""}`)
      : `ИИ-функции отключены: ${s.reason || "модель не настроена"}`);
}

function renderUserMenu() {
  const box = document.getElementById("user-menu");
  if (!box) return;
  box.replaceChildren(...(state.user ? [
    el("a", { href: "#/account", class: "small", text: state.user.email + (state.user.totp_enabled ? " 🔒" : "") }),
    el("button", { class: "small ghost", text: "Выйти", onclick: async () => {
      await rawApi("/auth/logout", { method: "POST" });
      state.user = null; renderUserMenu(); location.hash = "#/login";
    } })] : []));
}

boot();

/* ------------------------------------------------------------------ *
 * Background jobs banner (NFR-14): survives page reloads
 * ------------------------------------------------------------------ */

async function watchJobs(pid, box) {
  let seen = false;
  for (;;) {
    if (!document.body.contains(box)) return;
    let active = [];
    try { active = await rawApi(`/jobs?project=${pid}`); } catch (_) { return; }
    if (!active.length) {
      box.replaceChildren();
      if (seen) { toast("Фоновая задача завершена"); route(); }
      return;
    }
    seen = true;
    box.replaceChildren(el("div", { class: "banner info small", style: "margin-bottom:12px" },
      el("span", { class: "spinner" }), " Выполняется: ",
      active.map((j) => `${j.label} (${j.status === "queued" ? "в очереди" : "с " + new Date(j.started_at).toLocaleTimeString("ru-RU")})`).join("; "),
      el("span", { class: "muted", text: " — можно перезагрузить страницу, задача продолжится" })));
    await new Promise((r) => setTimeout(r, 2000));
  }
}

/* ------------------------------------------------------------------ *
 * Authentication (NFR-5)
 * ------------------------------------------------------------------ */

function renderLogin(app, register) {
  const invite = new URLSearchParams(location.hash.split("?")[1] || "").get("invite") || "";
  const inviteBanner = el("div", { class: "hidden" });
  const email = el("input", { type: "email", placeholder: "e-mail", autocomplete: "username" });
  const password = el("input", { type: "password", placeholder: register ? "пароль (не короче 10 символов)" : "пароль",
    autocomplete: register ? "new-password" : "current-password" });
  const totp = el("input", { placeholder: "код из приложения (6 цифр)", inputmode: "numeric", autocomplete: "one-time-code" });
  const totpRow = el("label", { class: "field hidden" }, el("span", { text: "Код двухфакторной аутентификации" }), totp);
  const setup = el("input", { placeholder: "код установки" });
  const setupRow = el("label", { class: "field hidden" }, el("span", { text: "Код установки (для первой учётной записи)" }), setup);
  const submit = el("button", { class: "primary", type: "submit", text: register ? "Зарегистрироваться" : "Войти" });
  const form = el("form", { class: "card stack", style: "max-width:420px;margin:40px auto", onsubmit: async (e) => {
    e.preventDefault();
    await busy(submit, async () => {
      try {
        state.user = await rawApi(register ? "/auth/register" : "/auth/login",
          { json: register ? { email: email.value, password: password.value, setup_code: setup.value, invite } : { email: email.value, password: password.value, totp: totp.value } });
      } catch (err) {
        if (err.detail && err.detail.mfa_required) { totpRow.classList.remove("hidden"); totp.focus(); toast(err.detail.message); return; }
        if (err.detail && err.detail.setup_required) { setupRow.classList.remove("hidden"); setup.focus(); toast(err.detail.message); return; }
        throw err;
      }
      await loadMeta();
      renderUserMenu();
      location.hash = "#/";
    });
  } },
    el("h1", { text: register ? "Регистрация" : "Вход" }), inviteBanner,
    el("label", { class: "field" }, el("span", { text: "E-mail" }), email),
    el("label", { class: "field" }, el("span", { text: "Пароль" }), password),
    totpRow, setupRow, submit,
    el("p", { class: "small" }, register ? el("a", { href: "#/login", text: "Уже есть аккаунт — войти" }) : el("a", { href: "#/register", text: "Создать аккаунт" })),
    el("p", { class: "hint small", text: "Данные проектов хранятся зашифрованными (AES-256); каждый пользователь видит только свои проекты." }));
  app.replaceChildren(form);
  email.focus();
  if (register) rawApi("/auth/setup").then((r) => {
    if (r.setup_required) { setupRow.classList.remove("hidden"); if (!invite) inviteBanner.className = "hidden"; }
  }).catch(() => {});
  if (register && invite) rawApi(`/auth/invite/${encodeURIComponent(invite)}`).then((r) => {
    inviteBanner.className = "banner " + (r.valid ? "ok" : "warn");
    inviteBanner.textContent = r.valid ? "Вас пригласили в AI Article. Придумайте пароль — и можно работать."
      : "Приглашение недействительно или уже использовано. Попросите у администратора новую ссылку.";
    if (r.valid && r.email) { email.value = r.email; email.readOnly = true; password.focus(); }
  }).catch(() => {});
  else if (register) {
    inviteBanner.className = "banner";
    inviteBanner.textContent = "Регистрация — по ссылке-приглашению от администратора.";
  }
}

async function renderAccount(app) {
  const me = await rawApi("/auth/me");
  const box2fa = el("div", { class: "stack" });
  const draw2fa = () => {
    if (me.totp_enabled) {
      const pw = el("input", { type: "password", placeholder: "пароль" });
      const code = el("input", { placeholder: "код" });
      const off = el("button", { class: "danger small", text: "Отключить 2FA" });
      off.addEventListener("click", () => busy(off, async () => {
        Object.assign(me, await rawApi("/auth/2fa/disable", { json: { password: pw.value, code: code.value } }));
        state.user = me; renderUserMenu(); draw2fa(); toast("2FA отключена");
      }));
      box2fa.replaceChildren(el("div", { class: "banner ok", text: "Двухфакторная аутентификация включена." }), el("div", { class: "row" }, pw, code, off));
    } else {
      const start = el("button", { class: "primary small", text: "Включить 2FA" });
      start.addEventListener("click", () => busy(start, async () => {
        const s = await rawApi("/auth/2fa/setup", { method: "POST" });
        const code = el("input", { placeholder: "6 цифр из приложения" });
        const confirmBtn = el("button", { class: "primary small", text: "Подтвердить" });
        confirmBtn.addEventListener("click", () => busy(confirmBtn, async () => {
          Object.assign(me, await rawApi("/auth/2fa/enable", { json: { code: code.value } }));
          state.user = me; renderUserMenu(); draw2fa(); toast("2FA включена");
        }));
        const qr = el("div", { style: "width:200px;background:#fff;padding:8px;border-radius:8px" });
        qr.innerHTML = s.svg; // generated by our own server from the otpauth URI
        box2fa.replaceChildren(el("p", { text: "Отсканируйте QR-код в приложении-аутентификаторе (Google Authenticator, Яндекс Ключ, 1Password…) или введите секрет вручную:" }),
          qr, el("code", { text: s.secret }), el("div", { class: "row" }, code, confirmBtn));
      }));
      box2fa.replaceChildren(el("p", { class: "hint", text: "Второй фактор защищает данные пациентов даже при утечке пароля (NFR-5)." }), el("div", {}, start));
    }
  };
  draw2fa();
  const oldPw = el("input", { type: "password", placeholder: "текущий пароль" });
  const newPw = el("input", { type: "password", placeholder: "новый пароль (≥ 10 символов)" });
  const change = el("button", { class: "small", text: "Сменить пароль" });
  change.addEventListener("click", () => busy(change, async () => {
    await rawApi("/auth/password", { json: { old: oldPw.value, new: newPw.value } });
    toast("Пароль изменён; другие сеансы завершены");
    oldPw.value = newPw.value = "";
  }));
  const aiCard = me.is_admin ? await aiSettingsCard() : null;
  const peopleCard = me.is_admin ? await colleaguesCard(me) : null;
  app.replaceChildren(el("div", { class: "stack", style: "max-width:720px" },
    el("h1", { text: "Аккаунт" }), peopleCard, aiCard,
    el("div", { class: "card stack" }, el("div", { text: me.email }), me.is_admin ? el("span", { class: "badge accent", text: "администратор" }) : null),
    el("div", { class: "card stack" }, el("h2", { text: "Двухфакторная аутентификация" }), box2fa),
    el("div", { class: "card stack" }, el("h2", { text: "Пароль" }), el("div", { class: "row" }, oldPw, newPw, change))));
}


/* ------------------------------------------------------------------ *
 * Colleagues: invitations and accounts (administrator)
 * ------------------------------------------------------------------ */

async function colleaguesCard(me) {
  const box = el("div", { class: "card stack" });
  const fmt = (iso) => new Date(iso).toLocaleDateString("ru-RU");
  const draw = (v, created) => {
    const base = (v.entry_url || location.origin).replace(/\/+$/, "");
    const email = el("input", { type: "email", placeholder: "e-mail коллеги (необязательно)", style: "min-width:260px" });
    const days = el("select", {}, [[7, "7 дней"], [3, "3 дня"], [14, "14 дней"], [30, "30 дней"]].map(([d, t]) => el("option", { value: d, text: t })));
    const make = el("button", { class: "primary small", text: "Создать приглашение" });
    make.addEventListener("click", () => busy(make, async () => {
      const r = await rawApi("/admin/invites", { json: { email: email.value, days: Number(days.value) } });
      // serveo's "Continue to Site" warning drops everything after "#" (the invitation); this parameter skips it
      const skip = /serveo(usercontent)?\.(net|com)$/.test(new URL(base).hostname) ? "?serveo-skip-browser-warning=true" : "";
      draw(r, { link: `${base}/${skip}#/register?invite=${r.token}`, email: email.value.trim(), days: days.value });
    }));
    let shown = null;
    if (created) {
      const link = el("input", { value: created.link, readOnly: true, style: "flex:1;min-width:300px", onfocus: (e) => e.target.select() });
      const copy = el("button", { class: "small", text: "Копировать" });
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(created.link); toast("Ссылка скопирована"); } catch { link.select(); toast("Нажмите ⌘C, чтобы скопировать"); }
      });
      shown = el("div", { class: "banner ok stack" },
        el("div", { text: `Приглашение создано${created.email ? " для " + created.email : ""}. Отправьте ссылку коллеге (почта, мессенджер). Она одноразовая, действует ${created.days} дн. и показывается только сейчас.` }),
        el("div", { class: "row" }, link, copy));
    }
    const statusText = { active: "ждёт регистрации", used: "использовано", expired: "истекло" };
    const invites = v.invites.length ? el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Для кого", "Статус", "До", ""].map((h) => el("th", { text: h })))),
      el("tbody", {}, v.invites.map((i) => {
        const revoke = i.status === "active" ? el("button", { class: "small danger", text: "Отозвать" }) : null;
        if (revoke) revoke.addEventListener("click", () => busy(revoke, async () => draw(await rawApi(`/admin/invites/${i.id}`, { method: "DELETE" }))));
        return el("tr", {}, el("td", { text: i.email || "любой e-mail" }),
          el("td", { text: statusText[i.status] + (i.used_by ? ` (${i.used_by})` : "") }),
          el("td", { class: "small", text: fmt(i.expires_at) }), el("td", {}, revoke));
      })))) : el("p", { class: "hint small", text: "Приглашений пока нет." });
    const users = el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Пользователь", "Проектов", "2FA", "С", ""].map((h) => el("th", { text: h })))),
      el("tbody", {}, v.users.map((u) => {
        const toggle = u.id === me.id ? null : el("button", { class: "small" + (u.is_active ? " danger" : ""), text: u.is_active ? "Заблокировать" : "Разблокировать" });
        if (toggle) toggle.addEventListener("click", () => busy(toggle, async () => {
          if (u.is_active && !confirm(`Заблокировать ${u.email}? Вход будет закрыт сразу; проекты сохранятся.`)) return;
          draw(await rawApi(`/admin/users/${u.id}`, { method: "PUT", json: { is_active: !u.is_active } }));
        }));
        return el("tr", {}, el("td", {}, u.email, u.is_admin ? el("span", { class: "badge accent", text: "админ", style: "margin-left:6px" }) : null,
          u.is_active ? null : el("span", { class: "badge danger", text: "заблокирован", style: "margin-left:6px" })),
          el("td", { text: String(u.projects) }), el("td", { text: u.totp_enabled ? "да" : "нет" }),
          el("td", { class: "small", text: fmt(u.created_at) }), el("td", {}, toggle));
      }))));
    box.replaceChildren(...[el("h2", { text: "Коллеги" }),
      el("p", { class: "hint small", text: "Регистрация закрыта: коллега регистрируется только по вашей одноразовой ссылке. Каждый видит только свои проекты." }),
      el("div", { class: "row" }, email, days, make), shown,
      el("h3", { text: "Приглашения" }), invites,
      el("h3", { text: "Пользователи" }), users].filter(Boolean));  // replaceChildren would print "null"
  };
  try { draw(await rawApi("/admin/people")); } catch (e) { box.replaceChildren(el("div", { class: "banner warn", text: e.message })); }
  return box;
}


/* ------------------------------------------------------------------ *
 * AI provider settings (administrator)
 * ------------------------------------------------------------------ */

async function aiSettingsCard() {
  const box = el("div", { class: "card stack" });
  const draw = (v) => {
    const provider = el("select", {}, Object.entries(v.providers).map(([k, l]) => el("option", { value: k, selected: v.provider === k, text: l })));
    const keyInput = (conf, hint, placeholder) => el("input", { type: "password", autocomplete: "off",
      placeholder: conf ? `ключ сохранён (${hint}) — введите новый, чтобы заменить` : placeholder });
    const gKey = keyInput(v.gemini.configured, v.gemini.key_hint, "ключ из Google AI Studio");
    const oKey = keyInput(v.openrouter.configured, v.openrouter.key_hint, "ключ из OpenRouter (sk-or-…)");
    const chainR = el("textarea", { style: "min-height:150px;font-family:var(--mono);font-size:12px" }, v.chains.reasoning.join("\n"));
    const chainE = el("textarea", { style: "min-height:150px;font-family:var(--mono);font-size:12px" }, v.chains.extraction.join("\n"));
    const modelSel = (current) => el("select", {}, v.gemini.models.map((m) => el("option", { value: m, selected: current === m, text: m })));
    const reasoning = modelSel(v.gemini.model_reasoning);
    const extraction = modelSel(v.gemini.model_extraction);
    const chainBox = el("div", { class: "stack", style: "gap:8px" },
      el("h3", { text: "Цепочки моделей" }),
      el("p", { class: "hint small", text: "Запрос идёт к первой модели списка, у которой не исчерпан лимит; при исчерпании — к следующей. Формат строки: gemini:модель или openrouter:модель. Порядок — оценка силы моделей, его можно менять." }),
      el("div", { class: "form-grid" },
        el("label", { class: "field" }, el("span", { text: "Анализ и текст статьи" }), chainR,
          el("button", { class: "small ghost", text: "по умолчанию", onclick: () => { chainR.value = v.chains.default_reasoning.join("\n"); } })),
        el("label", { class: "field" }, el("span", { text: "Извлечение данных и проверка цитат" }), chainE,
          el("button", { class: "small ghost", text: "по умолчанию", onclick: () => { chainE.value = v.chains.default_extraction.join("\n"); } }))),
      v.openrouter.free_models.length ? el("details", {}, el("summary", { text: `Бесплатные модели OpenRouter сейчас (${v.openrouter.free_models.length})` }),
        el("ul", { class: "small mono" }, v.openrouter.free_models.map((m) => el("li", {}, "openrouter:" + m.id, m.structured ? el("span", { class: "badge ok", style: "margin-left:6px", text: "JSON-схема" }) : el("span", { class: "badge", style: "margin-left:6px", text: "JSON по инструкции" }))))) : null);
    const geminiOnly = el("div", { class: "form-grid" },
      el("label", { class: "field" }, el("span", { text: "Gemini: анализ и текст" }), reasoning),
      el("label", { class: "field" }, el("span", { text: "Gemini: извлечение и проверка" }), extraction));
    const syncVisibility = () => {
      chainBox.classList.toggle("hidden", !["free", "openrouter"].includes(provider.value));
      geminiOnly.classList.toggle("hidden", provider.value !== "gemini");
    };
    provider.addEventListener("change", syncVisibility);
    const status = el("div", { class: "small" });
    const save = el("button", { class: "primary small", text: "Сохранить" });
    save.addEventListener("click", () => busy(save, async () => {
      const body = { provider: provider.value, gemini_model_reasoning: reasoning.value, gemini_model_extraction: extraction.value,
        chain_reasoning: chainR.value, chain_extraction: chainE.value };
      if (gKey.value.trim()) body.gemini_api_key = gKey.value.trim();
      if (oKey.value.trim()) body.openrouter_api_key = oKey.value.trim();
      const nv = await rawApi("/admin/llm", { method: "PUT", json: body });
      toast("Настройки ИИ сохранены");
      await loadMeta();
      draw(nv);
    }));
    const test = el("button", { class: "small", text: "Проверить подключение" });
    test.addEventListener("click", () => busy(test, async () => {
      const r = await rawApi("/admin/llm/test", { method: "POST" });
      status.replaceChildren(el("span", { class: "badge " + (r.ok ? "ok" : "danger"),
        text: r.ok ? `работает: ответила ${r.model} за ${r.seconds} с` : r.error }));
    }));
    const ex = Object.entries(v.exhausted || {}).filter(([k]) => k.includes(":"));
    box.replaceChildren(...[
      el("h2", { text: "ИИ-модели" }),
      el("div", { class: "row small" }, el("span", { class: "dot " + (v.status.available ? "on" : "off") }),
        v.status.available ? `сейчас первой ответит: ${v.status.reasoning_model} (текст), ${v.status.extraction_model} (проверки)` : v.status.reason),
      ex.length ? el("div", { class: "banner warn small" }, el("strong", { text: "Исчерпан лимит: " }),
        ex.map(([k, e]) => `${k} — до ${new Date(e.until).toLocaleString("ru-RU")}`).join("; ")) : null,
      el("label", { class: "field" }, el("span", { text: "Режим" }), provider),
      el("div", { class: "form-grid" },
        el("div", { class: "stack", style: "gap:6px" }, el("h3", { text: "Google Gemini" }),
          el("p", { class: "hint small" }, "Ключ: ", el("a", { href: "https://aistudio.google.com/apikey", target: "_blank", rel: "noopener", text: "AI Studio → Get API key" })),
          gKey),
        el("div", { class: "stack", style: "gap:6px" }, el("h3", { text: "OpenRouter" }),
          el("p", { class: "hint small" }, "Ключ: ", el("a", { href: "https://openrouter.ai/settings/keys", target: "_blank", rel: "noopener", text: "openrouter.ai → Keys" }),
            ". В ", el("a", { href: "https://openrouter.ai/settings/privacy", target: "_blank", rel: "noopener", text: "Privacy" }),
            " разрешите бесплатные эндпоинты, иначе бесплатные модели недоступны."),
          oKey)),
      geminiOnly, chainBox,
      el("div", { class: "banner warn small", text: "Бесплатные тарифы: Google и провайдеры OpenRouter могут использовать запросы для обучения моделей. Приложение отправляет только данные после подтверждённой анонимизации и опубликованные тексты. Лимиты: Gemini — по каждой модели отдельно (у сильнейших ~20 запросов в день), OpenRouter — 50 запросов в день на аккаунт (1000 после разового пополнения на $10)." }),
      el("div", { class: "row" }, save, test), status].filter(Boolean));
    syncVisibility();
  };
  try { draw(await rawApi("/admin/llm")); } catch (e) { box.replaceChildren(el("p", { class: "hint", text: e.message })); }
  return box;
}
