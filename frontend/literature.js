"use strict";
/* Step 5 — literature (Module 3). Uses helpers from app.js: el, api, toast, busy, fmtDate, state, route. */

const VERIFY = {
  supported: ["ok", "подтверждена"],
  partial: ["exploratory", "частично"],
  not_supported: ["danger", "не подтверждена"],
  unverified: ["", "не проверена"],
};
const RELATION = { supports: "подтверждает", contradicts: "противоречит", context: "контекст" };
const FULLTEXT = { pdf: "PDF", pmc: "PMC full text", abstract_only: "только abstract", none: "нет текста" };
const LIT_STATUS_CLASS = { described_consistent: "ok", described_contradicting: "danger",
  described_small_series: "exploratory", not_described: "accent", insufficient_sources: "" };

async function viewLiterature(root, p) {
  if (!state.styles) state.styles = await api("/literature/styles");
  const data = await api(`/projects/${p.id}/literature`);
  const tabs = [["sources", `Источники (${data.sources.length})`], ["matrix", "Матрица"],
    ["findings", "Находки × литература"], ["search", "Поиск по базе"], ["ground", "Цитирование текста"],
    ["bib", "Список литературы"], ["discover", "Похожие публикации и журналы"]];
  const bar = el("div", { class: "tabs" }, tabs.map(([id, label]) => el("button", {
    class: "tab" + (state.litTab === id ? " active" : ""), text: label,
    onclick: () => { state.litTab = id; viewLiterature(root, p); } })));
  const body = el("div", {}, el("p", { class: "hint", text: "Загрузка…" }));
  const c = data.completeness;
  const banner = c.blocking.length
    ? el("div", { class: "banner warn" }, el("strong", { text: `Блокирующие проблемы (${c.blocking.length}) — экспорт будет запрещён` }),
        el("ul", {}, c.blocking.slice(0, 5).map((b) => el("li", { text: b }))))
    : null;
  root.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "banner info small", text: "Цитата = проверяемый фрагмент: агент ссылается только на фрагменты этой базы; выдержка сверяется с текстом фрагмента кодом, затем цитату независимо проверяет другая модель. Метаданные — только из PubMed/Crossref." }),
    banner, el("div", {}, bar, body)));
  const render = { sources: litSources, matrix: litMatrix, findings: litFindings, search: litSearch, ground: litGround,
    bib: litBibliography, discover: litDiscover }[state.litTab];
  try { await render(body, p, data, () => viewLiterature(root, p)); }
  catch (e) { body.replaceChildren(el("div", { class: "card empty", text: e.message })); }
}

/* ---------- similar publications and journal suggestions (backend/app/literature/discovery.py) ---------- */

async function litDiscover(body, p, data, reload) {
  const last = await api(`/projects/${p.id}/literature/discover`);
  const q = last.queries || {};
  const topic = el("textarea", { style: "min-height:52px;font-family:var(--mono);font-size:12px" }, q.topic_query || "");
  const cases = el("textarea", { style: "min-height:52px;font-family:var(--mono);font-size:12px" }, q.cases_query || "");
  const run = el("button", { class: "primary", text: last.at ? "Искать заново (ИИ составит запросы)" : "Найти похожие публикации и журналы" });
  run.addEventListener("click", () => busy(run, async () => { await api(`/projects/${p.id}/literature/discover`, { json: {} }); reload(); }));
  const rerun = el("button", { text: "Искать по этим запросам", disabled: !last.at });
  rerun.addEventListener("click", () => busy(rerun, async () => {
    await api(`/projects/${p.id}/literature/discover`, { json: { queries: { topic_query: topic.value, cases_query: cases.value, keywords: q.keywords || [] } } });
    reload();
  }));
  const pubmed = (pmid) => el("a", { href: `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`, target: "_blank", rel: "noopener", text: "PubMed" });
  const addBtn = (pmid) => {
    const b = el("button", { class: "small", text: "В литературу" });
    b.addEventListener("click", () => busy(b, async () => { await api(`/projects/${p.id}/literature/identifiers`, { json: { values: [pmid] } }); b.textContent = "✓ добавлено"; b.disabled = true; }));
    return b;
  };
  const journalAction = (j) => {
    if (j.profile) {
      const b = el("button", { class: "small", text: "Выбрать журналом" });
      b.addEventListener("click", () => busy(b, async () => {
        const r = await api(`/projects/${p.id}/journal`, { method: "PUT", json: { journal_id: j.profile.id } });
        toast(r.applied.message);
      }));
      return b;
    }
    const b = el("button", { class: "small ghost", text: "Добавить в базу" });
    b.addEventListener("click", () => busy(b, async () => {
      const r = await api("/journals", { json: { name: j.journal, publisher: "", guidelines_url: "" } });
      toast(`«${r.name}» добавлен в базу журналов — заполните профиль по guidelines`);
      reload();
    }));
    return b;
  };
  const journalRow = (j) => el("tr", {},
    el("td", {}, j.journal, j.profile ? el("span", { class: "badge ok", style: "margin-left:6px", text: "в базе профилей" }) : null),
    el("td", { text: `${j.n} (${j.share}%)` }),
    el("td", { class: "small" }, j.examples.map((e) => el("div", {}, `${e.year} · ${e.title.slice(0, 90)} `, pubmed(e.pmid)))),
    el("td", {}, journalAction(j)));
  const journals = last.journals ? el("div", { class: "card stack" },
    el("h3", { text: "Где публикуют похожие работы (последние 10 лет)" }),
    el("p", { class: "hint small", text: `Найдено ${last.journals.n} статей по теме в PubMed; журналы — по числу публикаций. Это подсказка, а не рейтинг: учитывайте профиль и требования журнала.` }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Журнал", "Статей", "Примеры", ""].map((h) => el("th", { text: h })))),
      el("tbody", {}, last.journals.journals.map(journalRow))))) : null;
  const casesCard = last.cases ? el("div", { class: "card stack" },
    el("h3", { text: `Опубликованные похожие случаи (${last.cases.n})` }),
    el("p", { class: "hint small", text: last.cases.n
      ? "Прочитайте и процитируйте значимые из них в обсуждении; если похожие случаи есть, не пишите «впервые описан». Отметка «открытый доступ» — полный текст можно загрузить в базу бесплатно."
      : "Похожих описаний не найдено по этому запросу. Попробуйте расширить запрос — прежде чем писать о новизне, убедитесь, что поиск достаточно широк." }),
    last.cases.cases.map((c) => el("div", { class: "row", style: "border-bottom:1px solid var(--border);padding:6px 0;flex-wrap:nowrap;align-items:flex-start" },
      el("div", { class: "grow small" }, el("div", { text: c.title }),
        el("div", { class: "muted", text: `${c.authors || ""} · ${c.journal || ""} · ${c.year || ""}` }),
        c.open_access ? el("span", { class: "badge ok", text: "открытый доступ" }) : null),
      el("div", { class: "row", style: "gap:4px;flex-wrap:nowrap" }, c.pmid ? pubmed(c.pmid) : null, c.pmid ? addBtn(c.pmid) : null)))) : null;
  body.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "card stack" }, el("h3", { text: "Похожие публикации и подбор журнала" }),
      el("p", { class: "hint small", text: "ИИ составляет поисковые запросы по названию, ключевым сообщениям и (обезличенным) признакам случая; сам поиск идёт в PubMed и Europe PMC — все журналы и статьи реальные. Запросы можно поправить и повторить поиск без ИИ." }),
      el("div", { class: "row" }, run, rerun),
      last.at ? el("div", { class: "form-grid" },
        el("label", { class: "field" }, el("span", { text: "Запрос по теме (для подбора журнала)" }), topic),
        el("label", { class: "field" }, el("span", { text: "Запрос похожих случаев" }), cases)) : null),
    journals, casesCard));
}

/* ---------- sources ---------- */

function sourceLinks(s) {
  return el("span", { class: "row small", style: "gap:8px" },
    s.doi ? el("a", { href: `https://doi.org/${s.doi}`, target: "_blank", rel: "noopener", text: "DOI" }) : null,
    s.pmid ? el("a", { href: `https://pubmed.ncbi.nlm.nih.gov/${s.pmid}/`, target: "_blank", rel: "noopener", text: "PubMed" }) : null,
    s.fulltext.status === "pdf" ? el("a", { href: `/api/projects/${state.project.id}/literature/sources/${s.id}/file.pdf`, target: "_blank", text: "PDF" }) : null);
}

async function litSources(body, p, data, reload) {
  const report = (r) => {
    const added = (r.added || []).length;
    if (added) toast(`Добавлено источников: ${added}`);
    for (const e of r.errors || []) toast(`${e.file || e.value}: ${e.error}`, "error");
    for (const s of r.skipped || []) toast(`${s.title}: ${s.reason}`, "error");
    reload();
  };

  // PDF upload
  const pdfInput = el("input", { type: "file", multiple: true, accept: ".pdf", class: "hidden" });
  const drop = el("div", { class: "dropzone", style: "padding:18px", onclick: () => pdfInput.click(),
      ondragover: (e) => { e.preventDefault(); drop.classList.add("drag"); },
      ondragleave: () => drop.classList.remove("drag"),
      ondrop: (e) => { e.preventDefault(); drop.classList.remove("drag"); uploadPdf(e.dataTransfer.files); } },
    el("strong", { text: "PDF статей" }), el("div", { class: "hint", text: "перетащите или нажмите; DOI ищется в тексте и сверяется с Crossref" }));
  const uploadPdf = async (files) => {
    if (!files.length) return;
    const status = el("span", { text: " Передача PDF…" });
    drop.replaceChildren(el("span", { class: "spinner" }), status);
    try {
      report(await sendFiles(`/projects/${p.id}/literature/pdf`, [...files], {
        onProgress: (name, i, n) => { status.textContent = i < n ? ` Передача «${name}»: ${Math.round(100 * i / n)}%` : " Разбор PDF и сверка метаданных…"; } }));
    }
    catch (e) { toast(e.message, "error"); reload(); }
  };
  pdfInput.addEventListener("change", () => uploadPdf(pdfInput.files));

  // identifiers
  const ids = el("textarea", { placeholder: "DOI или PMID, по одному в строке\n10.1038/modpathol.2008…\n18084247", style: "min-height:64px" });
  const idBtn = el("button", { class: "primary small", text: "Добавить" });
  idBtn.addEventListener("click", () => busy(idBtn, async () => {
    const values = ids.value.split(/\n|,|;/).map((x) => x.trim()).filter(Boolean);
    if (!values.length) return;
    report(await api(`/projects/${p.id}/literature/identifiers`, { json: { values } }));
  }));

  // RIS / BibTeX
  const refInput = el("input", { type: "file", accept: ".ris,.bib,.txt", class: "hidden" });
  const refBtn = el("button", { class: "small", text: "Импорт RIS / BibTeX", onclick: () => refInput.click() });
  refInput.addEventListener("change", () => busy(refBtn, async () => {
    const fd = new FormData();
    fd.append("file", refInput.files[0]);
    report(await api(`/projects/${p.id}/literature/import`, { method: "POST", body: fd }));
  }));

  // PubMed suggestions (FR-3.12)
  const q = el("input", { placeholder: "Поиск в PubMed, напр.: PDGFRA GIST CD117 negative" });
  const results = el("div", {});
  const qBtn = el("button", { class: "small", text: "Искать" });
  const doSearch = () => busy(qBtn, async () => {
    if (!q.value.trim()) return;
    const items = await api(`/projects/${p.id}/literature/pubmed?q=${encodeURIComponent(q.value.trim())}`);
    results.replaceChildren(items.length ? el("div", { class: "table-wrap", style: "max-height:260px;margin-top:8px" }, el("table", {},
      el("tbody", {}, items.map((it) => {
        const add = el("button", { class: "small", text: it.in_base ? "в базе" : "Добавить", disabled: it.in_base });
        add.addEventListener("click", () => busy(add, async () => report(await api(`/projects/${p.id}/literature/identifiers`, { json: { values: [it.pmid] } }))));
        return el("tr", {}, el("td", {}, el("div", { text: it.title }),
            el("div", { class: "small muted" }, `${it.label} · ${it.journal || ""} · PMID ${it.pmid}`,
              it.retraction !== "ok" ? el("span", { class: "badge danger", text: " отозвана" }) : null)),
          el("td", {}, add));
      })))) : el("p", { class: "hint", text: "Ничего не найдено." }));
  });
  qBtn.addEventListener("click", doSearch);
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

  const addPanel = el("div", { class: "card" },
    el("h2", { text: "Добавить источники" }),
    el("div", { class: "form-grid" },
      el("div", {}, pdfInput, drop),
      el("div", { class: "stack", style: "gap:8px" }, ids, el("div", { class: "row" }, idBtn, refBtn, refInput)),
      el("div", {}, el("div", { class: "row", style: "flex-wrap:nowrap" }, q, qBtn),
        el("p", { class: "hint small", style: "margin-top:6px", text: "Найденное — «рекомендовано к прочтению»; в базу попадает только после вашего добавления." }),
        results)),
    data.contact_email_configured ? null : el("p", { class: "hint small", style: "margin-top:10px", text: "Полный текст берётся из PubMed Central. Для поиска OA-версий через Unpaywall задайте AI_ARTICLE_CONTACT_EMAIL." }));

  const retrBtn = el("button", { class: "small", text: "Проверить отзывы статей" });
  retrBtn.addEventListener("click", () => busy(retrBtn, async () => {
    const r = await api(`/projects/${p.id}/literature/retractions`, { method: "POST" });
    toast(r.changed.length ? `Статус изменился: ${r.changed.join(", ")}` : "Изменений нет");
    reload();
  }));

  const rows = data.sources.map((s) => sourceRow(p, s, reload));
  const list = data.sources.length
    ? el("div", { class: "card" }, el("div", { class: "row between" }, el("h2", { text: "База источников проекта" }), retrBtn),
        el("div", { class: "table-wrap", style: "max-height:none" }, el("table", {},
          el("thead", {}, el("tr", {}, ["ID", "Источник", "Метаданные", "Текст", "Тезисы", ""].map((h) => el("th", { text: h })))),
          el("tbody", {}, rows))))
    : el("div", { class: "card empty", text: "База пуста. Добавьте статьи, которые планируете цитировать." });
  body.replaceChildren(el("div", { class: "stack" }, addPanel, list));
}

function sourceRow(p, s, reload) {
  const v = s.verification.status;
  const meta = el("span", { class: "badge " + (v === "verified" ? "ok" : v === "mismatch" ? "danger" : "exploratory"),
    title: s.verification.details, text: v === "verified" ? `✓ ${s.metadata_source}` : v === "mismatch" ? "не совпадает" : "не найдены" });
  const retr = s.retraction.status === "retracted" ? el("span", { class: "badge danger", text: "ОТОЗВАНА" })
    : s.retraction.status === "retraction_notice" ? el("span", { class: "badge danger", text: "уведомление об отзыве" }) : null;
  const llmOn = state.meta.llm.available;
  const exBtn = el("button", { class: "small", text: s.extraction ? "Обновить" : "Извлечь", disabled: !llmOn || !s.n_chunks,
    title: llmOn ? `${state.meta.llm.extraction_model}: дизайн, n, маркеры, находки — с цитатами` : state.meta.llm.reason });
  exBtn.addEventListener("click", () => busy(exBtn, async () => {
    await api(`/projects/${p.id}/literature/sources/${s.id}/extract`, { method: "POST" });
    state.litExpanded = s.id;
    reload();
  }));
  const fixBtn = el("button", { class: "small ghost", text: "DOI/PMID", title: "Указать идентификатор и перепроверить метаданные" });
  fixBtn.addEventListener("click", async () => {
    const v2 = prompt("DOI или PMID этой статьи:", s.doi || s.pmid || "");
    if (!v2) return;
    try { await api(`/projects/${p.id}/literature/sources/${s.id}`, { method: "PATCH", json: { identifier: v2 } }); reload(); }
    catch (e) { toast(e.message, "error"); }
  });
  const delBtn = el("button", { class: "small ghost", text: "✕", title: "Удалить источник", onclick: async () => {
    if (!confirm(`Удалить ${s.id} (${s.label}) и связанные цитаты?`)) return;
    await api(`/projects/${p.id}/literature/sources/${s.id}`, { method: "DELETE" });
    reload();
  } });
  const expanded = state.litExpanded === s.id;
  const toggle = el("button", { class: "small ghost", text: expanded ? "▾" : "▸", title: "Подробнее",
    onclick: () => { state.litExpanded = expanded ? null : s.id; reload(); } });
  const row = el("tr", {},
    el("td", { class: "mono" }, toggle, s.id),
    el("td", {}, el("div", { text: s.title }), el("div", { class: "small muted" }, `${s.label} · ${s.journal || "—"} `, sourceLinks(s))),
    el("td", {}, meta, retr),
    el("td", { class: "small" }, el("span", { class: "badge " + (s.fulltext.status === "none" ? "danger" : s.fulltext.status === "abstract_only" ? "exploratory" : ""), text: FULLTEXT[s.fulltext.status], title: s.fulltext.origin }),
      el("div", { class: "muted", text: `${s.n_chunks} фрагм.` })),
    el("td", {}, s.extraction ? el("span", { class: "badge ok", text: "есть" }) : null, " ", exBtn),
    el("td", {}, el("div", { class: "row", style: "gap:2px;flex-wrap:nowrap" }, fixBtn, delBtn)));
  if (!expanded) return row;
  const detail = el("td", { colspan: 6, style: "background:var(--surface-2)" }, el("p", { class: "hint", text: "Загрузка…" }));
  (async () => {
    const parts = [];
    if (s.extraction) parts.push(extractionView(s.extraction));
    const chunks = await api(`/projects/${p.id}/literature/sources/${s.id}/chunks`);
    parts.push(el("details", {}, el("summary", { text: `Индексированные фрагменты (${chunks.length}) — раздел References не индексируется` }),
      el("div", { class: "stack", style: "margin-top:8px;max-height:360px;overflow:auto" }, chunks.map((c) =>
        el("div", { class: "small" }, el("span", { class: "mono muted", text: `${c.id} · стр. ${c.page ?? "—"} · ${c.section}  ` }), c.text)))));
    detail.replaceChildren(...parts);
  })();
  return [row, el("tr", {}, detail)];
}

function groundedBadge(item) {
  return item.grounded ? el("span", { class: "badge ok", title: item.chunk_id, text: "цитата найдена" })
    : el("span", { class: "badge danger", text: item.issue || "не подтверждено" });
}

function extractionView(ex) {
  const itemRow = (it) => el("li", {}, el("strong", { text: it.name }), `: ${it.summary}`,
    it.n_total != null ? el("span", { class: "mono", text: ` [${it.n_positive ?? "?"}/${it.n_total}]` }) : null, " ", groundedBadge(it),
    el("div", { class: "small muted", text: `«${it.quote}»` }));
  return el("div", { class: "stack", style: "gap:8px" },
    el("div", { class: "meta" }, el("span", { text: `Дизайн: ${ex.design}` }), el("span", { text: `Объект: ${ex.entity}` }),
      el("span", { text: `n = ${ex.n_cases ?? "не указано"}` }), el("span", { class: "muted", text: `${ex.model} · ${fmtDate(ex.created_at)}` }),
      ex.truncated ? el("span", { class: "badge exploratory", text: "текст обрезан" }) : null),
    ex.histotypes.length ? el("div", { class: "small", text: "Гистотипы: " + ex.histotypes.join(", ") }) : null,
    ex.markers.length ? el("div", {}, el("h4", { text: "ИГХ" }), el("ul", {}, ex.markers.map(itemRow))) : null,
    ex.molecular.length ? el("div", {}, el("h4", { text: "Молекулярные альтерации" }), el("ul", {}, ex.molecular.map(itemRow))) : null,
    ex.key_findings.length ? el("div", {}, el("h4", { text: "Основные находки" }), el("ul", {}, ex.key_findings.map((k) =>
      el("li", {}, k.text, " ", groundedBadge(k), el("div", { class: "small muted", text: `«${k.quote}»` }))))) : null,
    ex.limitations.length ? el("div", { class: "small", text: "Ограничения: " + ex.limitations.join("; ") }) : null);
}

/* ---------- matrix (FR-3.6) ---------- */

async function litMatrix(body, p) {
  const m = await api(`/projects/${p.id}/literature/matrix`);
  const hasLit = m.rows.some((r) => r.source_id);
  const table = (group, title) => {
    const cols = m.columns[group];
    if (!cols.length) return null;
    return el("div", {}, el("h3", { text: title }), el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", { class: "sticky", text: "Источник" }), el("th", { text: "Дизайн" }), el("th", { text: "n" }),
        cols.map((c) => el("th", { text: c })))),
      el("tbody", {}, m.rows.map((r) => el("tr", { style: r.source_id ? null : "background:var(--accent-soft)" },
        el("td", { class: "sticky" }, el("strong", { text: r.label })), el("td", { class: "small", text: r.design || "—" }),
        el("td", { class: "num", text: r.n ?? "—" }),
        cols.map((c) => {
          const v = r[group][c];
          if (!v) return el("td", { class: "missing", text: "—" });
          const frac = v.n_total ? `${v.n_pos ?? "?"}/${v.n_total}` + (v.n_pos != null ? ` (${Math.round(100 * v.n_pos / v.n_total)}%)` : "") : (v.text || "есть");
          return el("td", { class: "small", title: v.text || "", text: frac });
        })))))));
  };
  body.replaceChildren(el("div", { class: "stack" },
    el("p", { class: "hint", text: "Сводная таблица «источник × параметры» (FR-3.6) строится из извлечённых тезисов. Первая строка — ваша серия (бинарные ИГХ/молекулярные признаки из последнего прогона анализа)." }),
    hasLit ? null : el("div", { class: "banner warn", text: "Нет источников с извлечёнными тезисами — нажмите «Извлечь» на вкладке «Источники»." }),
    table("ihc", "Иммуногистохимия"), table("molecular", "Молекулярные альтерации")));
}

/* ---------- findings × literature (FR-2.6, FR-3.7) ---------- */

function citationItem(p, c, reload) {
  const [cls, label] = VERIFY[c.verification.status] || ["", c.verification.status];
  const decide = (d) => api(`/projects/${p.id}/literature/citations/${c.id}`, { json: { decision: c.decision === d ? "pending" : d } }).then(reload).catch((e) => toast(e.message, "error"));
  const reverify = el("button", { class: "small ghost", text: "Перепроверить", disabled: !state.meta.llm.available });
  reverify.addEventListener("click", () => busy(reverify, async () => { await api(`/projects/${p.id}/literature/citations/${c.id}/verify`, { method: "POST" }); reload(); }));
  return el("div", { class: "card", style: "padding:12px;box-shadow:none" + (c.decision === "rejected" ? ";opacity:.55" : "") },
    el("div", { class: "row", style: "gap:8px" },
      el("span", { class: "mono small muted", text: c.id }), el("strong", { text: c.source_label }),
      el("span", { class: "small muted", text: `стр. ${c.page ?? "—"} · ${c.section}` }),
      el("span", { class: "badge", text: RELATION[c.relation] }),
      el("span", { class: "badge " + cls, title: c.verification.rationale, text: "проверка: " + label }),
      c.origin === "author" ? el("span", { class: "badge accent", text: "добавлена автором" }) : null),
    el("div", { style: "margin-top:6px", text: c.claim }),
    el("blockquote", { style: "margin:6px 0;padding-left:10px;border-left:3px solid var(--border);color:var(--ink-2)", text: c.quote }),
    c.verification.rationale ? el("div", { class: "small muted", text: c.verification.rationale }) : null,
    el("div", { class: "row", style: "margin-top:6px;gap:6px" },
      el("button", { class: "small " + (c.decision === "accepted" ? "active-ok" : "ok"), text: c.decision === "accepted" ? "✓ Принята" : "Принять", onclick: () => decide("accepted") }),
      el("button", { class: "small " + (c.decision === "rejected" ? "active-danger" : "danger"), text: c.decision === "rejected" ? "✕ Отклонена" : "Отклонить", onclick: () => decide("rejected") }),
      reverify));
}

async function litFindings(body, p, data, reload) {
  const items = await api(`/projects/${p.id}/literature/findings`);
  if (!items.length) {
    body.replaceChildren(el("div", { class: "card empty" }, "Нет находок для сопоставления. ", el("a", { href: `#/p/${p.id}/analysis`, text: "Запустите анализ" })));
    return;
  }
  const llmOn = state.meta.llm.available;
  body.replaceChildren(el("div", { class: "stack" },
    el("p", { class: "hint", text: `Для каждой находки агент (${state.meta.llm.reasoning_model}) ищет в базе проекта, оценивает, описана ли ассоциация и на каких сериях, и формулирует новизну. В модель уходит карточка находки (агрегированные числа), не данные случаев.` }),
    items.map((f) => {
      const comp = f.comparison;
      const btn = el("button", { class: "small primary", text: comp ? "Сопоставить заново" : "Сопоставить с литературой", disabled: !llmOn || !data.sources.length,
        title: !llmOn ? state.meta.llm.reason : !data.sources.length ? "база литературы пуста" : "" });
      btn.addEventListener("click", () => busy(btn, async () => { await api(`/projects/${p.id}/literature/findings/${f.id}/compare`, { method: "POST" }); reload(); }));
      const attach = manualAttach(p, f, reload);
      let compView = null;
      if (comp) {
        const nov = el("textarea", { style: "min-height:48px" }, comp.novelty || "");
        const confirmBtn = el("button", { class: "small " + (comp.novelty_confirmed ? "active-ok" : "ok"), text: comp.novelty_confirmed ? "✓ Новизна подтверждена" : "Подтвердить новизну" });
        confirmBtn.addEventListener("click", () => busy(confirmBtn, async () => {
          await api(`/projects/${p.id}/literature/findings/${f.id}/novelty`, { json: { text: nov.value.trim(), confirmed: !comp.novelty_confirmed } });
          reload();
        }));
        compView = el("div", { class: "stack", style: "margin-top:10px;gap:10px" },
          el("div", { class: "row" }, el("span", { class: "badge " + (LIT_STATUS_CLASS[comp.literature_status] ?? ""), text: comp.status_label }),
            el("span", { class: "small muted", text: `источников с данными: ${comp.n_sources_with_evidence} из ${comp.n_sources_in_base} · ${comp.model} · ${fmtDate(comp.created_at)}` })),
          el("p", { text: comp.summary }),
          comp.citations.map((c) => citationItem(p, c, reload)),
          comp.rejected.length ? el("details", {}, el("summary", { text: `Отброшено кодом как непроверяемое: ${comp.rejected.length}` }),
            el("ul", { class: "small" }, comp.rejected.map((r) => el("li", { text: `${r.chunk_id}: ${r.reason} — «${r.quote}»` })))) : null,
          el("details", {}, el("summary", { text: `Поисковые запросы агента (${comp.queries.length})` }), el("ul", { class: "small mono" }, comp.queries.map((q) => el("li", { text: q })))),
          el("div", {}, el("h4", { text: "Новизна / gap (FR-3.7)" }), nov, el("div", { class: "row", style: "margin-top:6px" }, confirmBtn,
            el("span", { class: "hint", text: "Подтверждённая формулировка пойдёт в Introduction/Discussion." }))));
      }
      return el("article", { class: `card finding ${f.evidence}` },
        el("div", { class: "finding-head" }, el("span", { class: "finding-id", text: f.id }), el("h3", { text: f.title }),
          el("span", { class: "badge " + f.evidence, text: EVIDENCE[f.evidence] }),
          f.status === "accepted" ? el("span", { class: "badge ok", text: "принята" }) : null),
        el("p", { class: "small", text: f.statement }),
        el("div", { class: "row" }, btn, attach.button), attach.panel, compView);
    })));
}

function manualAttach(p, f, reload) {
  const panel = el("div", { class: "hidden stack", style: "margin-top:10px;gap:8px" });
  const q = el("input", { placeholder: "Поиск фрагмента в базе (англ.)" });
  const claim = el("input", { placeholder: "Что утверждает источник (будет текстом цитаты)" });
  const out = el("div", {});
  const run = async () => {
    const hits = await api(`/projects/${p.id}/literature/search?q=${encodeURIComponent(q.value)}&k=6`);
    out.replaceChildren(...hits.map((h) => el("div", { class: "small", style: "padding:6px 0;border-bottom:1px solid var(--border)" },
      el("strong", { text: h.source_label }), el("span", { class: "muted", text: ` · стр. ${h.page ?? "—"} · ${h.section} ` }),
      el("button", { class: "small", text: "Привязать", onclick: async () => {
        if (!claim.value.trim()) return toast("Сформулируйте утверждение", "error");
        try {
          await api(`/projects/${p.id}/literature/citations`, { json: { claim: claim.value.trim(), chunk_id: h.id, finding_key: f.key } });
          toast("Цитата добавлена; она не проверена — запустите проверку или решите вручную");
          reload();
        } catch (e) { toast(e.message, "error"); }
      } }),
      el("div", { text: h.text }))));
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  panel.append(el("div", { class: "row", style: "flex-wrap:nowrap" }, q, el("button", { class: "small", text: "Найти", onclick: run })), claim, out);
  const button = el("button", { class: "small", text: "Привязать фрагмент вручную", onclick: () => panel.classList.toggle("hidden") });
  return { button, panel };
}

/* ---------- search ---------- */

function highlight(text, query) {
  const terms = query.toLowerCase().split(/[^a-z0-9-]+/).filter((t) => t.length > 2);
  if (!terms.length) return [text];
  const re = new RegExp("(" + terms.map((t) => t.replace(/[-]/g, "\\-")).join("|") + ")", "gi");
  return text.split(re).map((part, i) => i % 2 ? el("mark", { text: part }) : part);
}

async function litSearch(body, p) {
  const q = el("input", { value: state.litQuery, placeholder: "Например: CD117 negative PDGFRA epithelioid" });
  const out = el("div", { class: "stack" });
  const run = async () => {
    state.litQuery = q.value;
    if (!q.value.trim()) return;
    const hits = await api(`/projects/${p.id}/literature/search?q=${encodeURIComponent(q.value)}&k=15`);
    out.replaceChildren(...(hits.length ? hits.map((h) => el("div", { class: "card", style: "padding:12px" },
      el("div", { class: "row small", style: "gap:8px" }, el("strong", { text: h.source_label }),
        el("span", { class: "muted mono", text: `${h.id} · стр. ${h.page ?? "—"} · ${h.section} · score ${h.score}` })),
      el("p", { style: "margin:6px 0 0" }, highlight(h.text, q.value)))) : [el("p", { class: "hint", text: "Ничего не найдено." })]));
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  body.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "row", style: "flex-wrap:nowrap" }, q, el("button", { class: "primary", text: "Искать", onclick: run })),
    el("p", { class: "hint", text: "Лексический поиск BM25 по фрагментам базы проекта (запросы на английском, с синонимами CD117/KIT, Ki-67/MIB-1). В MVP — гибридный поиск с эмбеддингами (pgvector)." }),
    out));
  if (state.litQuery) run();
}

/* ---------- grounding text (FR-3.8) ---------- */

const SENT_STATUS = { cited: ["ok", "цитата"], own_result: ["accent", "собственный результат"],
  general_knowledge: ["exploratory", "общее знание — проверьте"], needs_source: ["danger", "нужен источник"] };

async function litGround(body, p, data, reload) {
  const docs = await api(`/projects/${p.id}/literature/groundings`);
  const text = el("textarea", { style: "min-height:140px", placeholder: "Вставьте абзац (англ.), например фрагмент Introduction. Агент расставит ссылки только на источники из базы и отметит утверждения без опоры. Не вставляйте персональные данные пациентов." });
  const btn = el("button", { class: "primary", text: "Расставить цитаты", disabled: !state.meta.llm.available || !data.sources.length });
  btn.addEventListener("click", () => busy(btn, async () => {
    if (text.value.trim().length < 10) return toast("Слишком короткий текст", "error");
    await api(`/projects/${p.id}/literature/ground`, { json: { text: text.value.trim() } });
    reload();
  }));
  body.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "card stack" }, el("h3", { text: "Автоматическая расстановка цитат (FR-3.8, FR-3.9)" }), text,
      el("div", { class: "row" }, btn, el("span", { class: "hint", text: "Каждая ссылка — конкретный фрагмент; неподтверждённые цитаты блокируют экспорт до вашего решения." }))),
    docs.map((d) => el("div", { class: "card stack" },
      el("div", { class: "meta" }, el("strong", { text: d.id }), el("span", { text: `${d.model} · ${fmtDate(d.created_at)}` })),
      el("p", {}, d.sentences.map((s) => [el("span", { title: SENT_STATUS[s.status][1], style: s.status === "needs_source" ? "background:var(--danger-soft)" : s.status === "general_knowledge" ? "background:var(--warn-soft)" : null, text: s.text }),
        s.markers.length ? el("strong", { style: "color:var(--accent)", text: " " + s.markers.join(", ") }) : null, " "])),
      el("details", {}, el("summary", { text: "Разбор по предложениям и проверка цитат" }),
        el("div", { class: "stack", style: "margin-top:8px" }, d.sentences.map((s) => el("div", {},
          el("div", { class: "row small" }, el("span", { class: "mono muted", text: `#${s.index + 1}` }), el("span", { class: "badge " + SENT_STATUS[s.status][0], text: SENT_STATUS[s.status][1] })),
          el("div", { class: "small", style: "margin:4px 0", text: s.text }),
          s.citations.map((c) => citationItem(p, c, reload)),
          s.rejected.length ? el("div", { class: "small muted", text: `отброшено непроверяемых цитат: ${s.rejected.length}` }) : null)))),
      d.bibliography.length ? el("div", {}, el("h4", { text: "Ссылки" }), el("ol", { class: "small", style: "padding-left:0;list-style:none" }, d.bibliography.map((b) => el("li", { text: b.text })))) : null))));
}

/* ---------- bibliography & completeness (FR-3.10, FR-3.11) ---------- */

async function litBibliography(body, p, data, reload) {
  const style = el("select", {}, Object.entries(state.styles).map(([k, v]) => el("option", { value: k, selected: data.settings.style === k, text: v })));
  const scope = el("select", {}, el("option", { value: "cited", text: "только принятые цитаты" }), el("option", { value: "all", text: "все источники базы" }));
  const list = el("div", {});
  const load = async () => {
    const b = await api(`/projects/${p.id}/literature/bibliography?style=${style.value}&scope=${scope.value}`);
    list.replaceChildren(b.entries.length ? el("ol", { style: "padding-left:0;list-style:none", class: "stack" }, b.entries.map((e) => el("li", {}, e.text)))
      : el("p", { class: "hint", text: scope.value === "cited" ? "Пока нет принятых цитат." : "База пуста." }));
  };
  style.addEventListener("change", async () => { await api(`/projects/${p.id}/literature/settings`, { method: "PUT", json: { style: style.value } }); load(); });
  scope.addEventListener("change", load);
  const c = data.completeness;
  const labels = Object.fromEntries(data.sources.map((s) => [s.id, `${s.id} (${s.label})`]));
  body.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "card stack" },
      el("div", { class: "row" }, el("label", { class: "field grow" }, el("span", { text: "Стиль (CSL)" }), style),
        el("label", { class: "field grow" }, el("span", { text: "Состав" }), scope)),
      el("p", { class: "hint small", text: "Метаданные берутся только из PubMed/Crossref. Стиль журнала будет подставляться из профиля журнала (модуль 4)." }),
      list),
    el("div", { class: "card stack" }, el("h3", { text: "Проверка полноты (FR-3.11)" }),
      el("div", { class: "meta" }, el("span", { text: `источников: ${c.n_sources}` }), el("span", { text: `процитировано: ${c.n_cited}` })),
      c.blocking.length ? el("div", { class: "banner warn" }, el("strong", { text: "Блокирует экспорт" }), el("ul", {}, c.blocking.map((b) => el("li", { text: b })))) : el("div", { class: "banner ok", text: "Блокирующих проблем нет." }),
      c.warnings.length ? el("div", {}, el("h4", { text: "Предупреждения" }), el("ul", { class: "small" }, c.warnings.map((w) => el("li", { text: w })))) : null,
      c.unused.length ? el("div", { class: "small" }, el("strong", { text: "Не использованы: " }), c.unused.map((u) => labels[u] || u).join(", ")) : null,
      c.duplicates.length ? el("div", { class: "small" }, el("strong", { text: "Дубликаты: " }), c.duplicates.map((d) => `${d.source} = ${d.duplicate_of}`).join(", ")) : null)));
  load();
}
