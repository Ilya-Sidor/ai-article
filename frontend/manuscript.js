"use strict";
/* Module 5 — manuscript. Uses helpers from app.js: el, api, toast, busy, fmtDate, state, route, EVIDENCE. */

const SEC_STATUS = { empty: ["", "не начат"], draft: ["exploratory", "черновик"], accepted: ["ok", "принят"] };
const SEVERITY = { blocking: ["danger", "блокирует"], warning: ["exploratory", "предупреждение"], info: ["", "инфо"] };

async function viewDraft(root, p) {
  viewDraftRender(root, p, await api(`/projects/${p.id}/manuscript`));
}

function viewDraftRender(root, p, m, reloadArg) {
  const reload = reloadArg || (async (fresh) => viewDraftRender(root, p, fresh || await api(`/projects/${p.id}/manuscript`)));
  if (!m.has_analysis) {
    root.replaceChildren(el("div", { class: "card empty" }, "Сначала выполните анализ. ", el("a", { href: `#/p/${p.id}/analysis`, text: "К анализу" })));
    return;
  }
  const approved = m.plan && m.plan.status === "approved";
  const tabs = [["terms", "1. Терминология"], ["inputs", "2. Данные автора"], ["plan", "3. План"], ["assets", "4. Таблицы и рисунки"],
    ["sections", "5. Разделы"], ["front", "6. Название и ключевые слова"]];
  if (!state.msTab) state.msTab = approved ? "sections" : "plan";
  const bar = el("div", { class: "tabs" }, tabs.map(([id, label]) => el("button", {
    class: "tab" + (state.msTab === id ? " active" : ""), text: label, disabled: id === "sections" && !approved,
    onclick: () => { state.msTab = id; viewDraftRender(root, p, m, reload); } })));
  const c = m.counts || {};
  const lim = m.template?.limits || {};
  const counters = el("div", { class: "row small", style: "gap:14px" },
    el("span", {}, "Основной текст: ", el("strong", { text: `${c.words ?? 0}` }), ` / ${lim.words_total ?? "—"} слов`),
    el("span", {}, "Abstract: ", el("strong", { text: `${c.abstract_words ?? 0}` }), ` / ${m.template?.abstract?.words ?? "—"}`),
    el("span", { text: `Таблиц: ${c.tables ?? 0} · рисунков: ${c.figures ?? 0} · ссылок: ${c.references ?? 0}` }),
    el("span", { class: "badge " + (m.blocking ? "danger" : "ok"), text: m.blocking ? `блокирующих замечаний: ${m.blocking}` : "блокирующих замечаний нет" }));
  const body = { terms: msTerms, inputs: msInputs, plan: msPlan, assets: msAssets, sections: msSections, front: msFront }[state.msTab](p, m, reload);
  root.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "banner info small", text: "Числа в тексте — ссылки на результаты анализа ({{…}}), их подставляет код. Цитаты — только проверенные фрагменты базы литературы. Каждый раздел: черновик → правки → принятие; все версии сохраняются." }),
    el("div", { class: "card", style: "padding:12px 16px" }, counters),
    el("div", {}, bar, body)));
}

/* ---------- 1. terminology ---------- */

function msTerms(p, m, reload) {
  const edits = JSON.parse(JSON.stringify(m.terms || {}));
  const rows = m.terms_needed.map((t) => {
    const cur = edits[t.name] || (edits[t.name] = { en: "", levels: {} });
    const en = el("input", { value: cur.en || "", placeholder: "English term", onchange: (e) => { cur.en = e.target.value; } });
    return el("tr", {},
      el("td", {}, el("div", { text: t.name }), el("div", { class: "small muted", text: `${t.group || ""} · ${t.vtype}` })),
      el("td", {}, en),
      el("td", {}, t.levels.map((lv) => el("div", { class: "row", style: "flex-wrap:nowrap;margin-bottom:4px" },
        el("span", { class: "small", style: "min-width:120px", text: lv }),
        el("input", { value: (cur.levels || {})[lv] || "", placeholder: "EN", onchange: (e) => { cur.levels = cur.levels || {}; cur.levels[lv] = e.target.value; } })))));
  });
  const auto = el("button", { text: "Перевести автоматически", disabled: !state.meta.llm.available, title: state.meta.llm.available ? state.meta.llm.extraction_model : state.meta.llm.reason });
  auto.addEventListener("click", () => busy(auto, async () => reload(await api(`/projects/${p.id}/manuscript/terms/auto`, { method: "POST" }))));
  const save = el("button", { class: "primary", text: "Сохранить терминологию" });
  save.addEventListener("click", () => busy(save, async () => { reload(await api(`/projects/${p.id}/manuscript/terms`, { method: "PUT", json: { terms: edits } })); toast("Сохранено"); }));
  return el("div", { class: "card stack" },
    el("p", { class: "hint", text: "Английские названия признаков и значений для текста, таблиц и подписей (ВОЗ, HGNC, стандартная запись ИГХ). Одно название на всю рукопись — основа проверки согласованности терминов." }),
    el("div", { class: "row" }, auto, save),
    el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Признак", "English", "Значения"].map((h) => el("th", { text: h })))), el("tbody", {}, rows))));
}

/* ---------- 2. author inputs ---------- */

function msInputs(p, m, reload) {
  const inp = JSON.parse(JSON.stringify(m.inputs || {}));
  const field = (label, path, opts = {}) => {
    const keys = path.split(".");
    let obj = inp;
    for (const k of keys.slice(0, -1)) obj = obj[k] = obj[k] || {};
    const last = keys[keys.length - 1];
    const node = opts.textarea ? el("textarea", { style: "min-height:52px" }, obj[last] || "") : el("input", { value: obj[last] || "", placeholder: opts.placeholder || "" });
    node.addEventListener("change", () => { obj[last] = node.value; });
    return el("label", { class: "field" }, el("span", { text: label }), node);
  };
  const authors = el("textarea", { style: "min-height:70px", placeholder: "Одна строка — один автор: Имя Фамилия; Conceptualization, Formal analysis" },
    (inp.authors || []).map((a) => `${a.name}; ${(a.roles || []).join(", ")}`).join("\n"));
  authors.addEventListener("change", () => {
    inp.authors = authors.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
      const [name, roles] = l.split(";");
      return { name: name.trim(), roles: (roles || "").split(",").map((r) => r.trim()).filter(Boolean) };
    });
  });
  const waiver = el("input", { type: "checkbox", checked: !!(inp.ethics || {}).waiver, onchange: (e) => { inp.ethics = inp.ethics || {}; inp.ethics.waiver = e.target.checked; } });
  const consent = el("select", { onchange: (e) => { inp.consent = inp.consent || {}; inp.consent.status = e.target.value; } },
    [["", "—"], ["written", "письменное согласие получено"], ["waived", "не требовалось (ретроспективно, анонимизировано)"], ["not_applicable", "не применимо"]]
      .map(([k, l]) => el("option", { value: k, selected: (inp.consent || {}).status === k, text: l })));
  const markerRows = (group, listKey, cols) => {
    const names = m.terms_needed.filter((t) => t.group === group).map((t) => (m.terms[t.name] || {}).en || t.name);
    inp[listKey] = inp[listKey] || [];
    for (const n of names) if (!inp[listKey].find((x) => x.marker === n)) inp[listKey].push({ marker: n });
    return el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, [el("th", { text: group === "ihc" ? "Маркер" : "Альтерация" }), ...cols.map(([, l]) => el("th", { text: l }))])),
      el("tbody", {}, inp[listKey].map((row) => el("tr", {}, el("td", { text: row.marker }), cols.map(([k]) => {
        const i = el("input", { value: row[k] || "" });
        i.addEventListener("change", () => { row[k] = i.value; });
        return el("td", {}, i);
      }))))));
  };
  const save = el("button", { class: "primary", text: "Сохранить" });
  save.addEventListener("click", () => busy(save, async () => { reload(await api(`/projects/${p.id}/manuscript/inputs`, { method: "PUT", json: inp })); toast("Сохранено"); }));
  return el("div", { class: "stack" },
    el("div", { class: "card stack" }, el("h3", { text: "Материал и дизайн (для Materials and methods)" }),
      el("div", { class: "form-grid" }, field("Учреждение / архив", "study.institution"), field("Период", "study.period", { placeholder: "2016–2022" }),
        field("Пересмотр препаратов", "study.review", { placeholder: "all cases reviewed by two pathologists" })),
      field("Критерии отбора", "study.selection", { textarea: true }),
      field("Определение follow-up / исходов", "study.followup", { textarea: true })),
    el("div", { class: "card stack" }, el("h3", { text: "Иммуногистохимия (FR-5.3: клоны, разведения, платформы)" }),
      markerRows("ihc", "ihc", [["clone", "Клон"], ["vendor", "Производитель"], ["dilution", "Разведение"], ["platform", "Платформа"], ["retrieval", "Демаскировка"], ["scoring", "Оценка"]])),
    el("div", { class: "card stack" }, el("h3", { text: "Молекулярные методы" }),
      markerRows("molecular", "molecular", [["method", "Метод"], ["panel", "Панель / праймеры"], ["platform", "Платформа"], ["details", "Детали"]])),
    el("div", { class: "card stack" }, el("h3", { text: "Заявления (FR-5.10)" }),
      el("label", { class: "field" }, el("span", { text: "Авторы и роли CRediT" }), authors),
      el("div", { class: "form-grid" }, field("Этический комитет", "ethics.committee"), field("Номер одобрения", "ethics.approval_number"),
        field("Дата", "ethics.approval_date")),
      el("label", { class: "row small" }, waiver, "одобрение не требовалось (waiver)"),
      el("label", { class: "field" }, el("span", { text: "Информированное согласие" }), consent),
      field("Конфликт интересов", "coi", { textarea: true, placeholder: "The authors declare no competing interests." }),
      field("Финансирование", "funding", { textarea: true }),
      field("Доступность данных", "data_availability", { textarea: true }),
      field("Благодарности", "acknowledgements", { textarea: true }),
      el("div", { class: "banner warn small", text: "ИИ не может быть указан автором; авторы несут ответственность за содержание (FR-7.2). Заявление об использовании ИИ формируется автоматически по фактическому журналу обращений к моделям." })),
    el("div", {}, save));
}

/* ---------- 3. plan ---------- */

function msPlan(p, m, reload) {
  const plan = m.plan ? JSON.parse(JSON.stringify(m.plan)) : null;
  const gen = el("button", { class: plan ? "" : "primary", text: plan ? "Сгенерировать заново" : "Сгенерировать план", disabled: !state.meta.llm.available || !m.n_accepted_findings,
    title: !m.n_accepted_findings ? "примите находки на шаге «Анализ»" : state.meta.llm.available ? state.meta.llm.reasoning_model : state.meta.llm.reason });
  gen.addEventListener("click", () => busy(gen, async () => reload(await api(`/projects/${p.id}/manuscript/plan`, { method: "POST" }))));
  if (!plan) {
    return el("div", { class: "card stack" },
      el("p", { text: `Принятых находок: ${m.n_accepted_findings}. План строится из них, подтверждённой новизны и профиля журнала: ключевые сообщения, тезисы по разделам, бюджет слов, таблицы и рисунки (FR-5.1).` }),
      el("div", {}, gen));
  }
  const msgs = el("textarea", { style: "min-height:70px" }, plan.key_messages.join("\n"));
  const secBox = el("div", { class: "stack" });
  const drawSecs = () => secBox.replaceChildren(...plan.sections.map((s, i) => {
    const h = el("input", { value: s.heading, onchange: (e) => { s.heading = e.target.value; } });
    const w = el("input", { value: s.words, style: "max-width:90px", onchange: (e) => { s.words = parseInt(e.target.value || "0", 10); } });
    const pts = el("textarea", { style: "min-height:70px" }, s.points.join("\n"));
    pts.addEventListener("change", () => { s.points = pts.value.split("\n"); });
    return el("div", { class: "card", style: "padding:12px;box-shadow:none" },
      el("div", { class: "row", style: "flex-wrap:nowrap" }, el("span", { class: "mono muted", text: i + 1 }), h, w, el("span", { class: "small muted", text: "слов" }),
        el("button", { class: "small ghost", text: "✕", onclick: () => { plan.sections.splice(i, 1); drawSecs(); } })),
      el("div", { style: "margin-top:6px" }, pts));
  }));
  drawSecs();
  const total = plan.sections.reduce((a, s) => a + (s.words || 0), 0);
  const save = el("button", { text: "Сохранить план" });
  const collect = () => ({ key_messages: msgs.value.split("\n"), sections: plan.sections });
  save.addEventListener("click", () => busy(save, async () => reload(await api(`/projects/${p.id}/manuscript/plan`, { method: "PUT", json: collect() }))));
  const approve = el("button", { class: "ok", text: plan.status === "approved" ? "✓ План согласован" : "Согласовать план", disabled: plan.status === "approved" });
  approve.addEventListener("click", () => busy(approve, async () => {
    await api(`/projects/${p.id}/manuscript/plan`, { method: "PUT", json: collect() });
    const r = await api(`/projects/${p.id}/manuscript/plan/approve`, { method: "POST" });
    state.msTab = "sections";
    reload(r);
  }));
  return el("div", { class: "stack" },
    el("div", { class: "card stack" },
      el("div", { class: "row between" }, el("h3", { text: "Ключевые сообщения (1–3)" }), el("span", { class: "badge " + (plan.status === "approved" ? "ok" : "exploratory"), text: plan.status === "approved" ? "согласован" : "черновик" })),
      msgs, plan.rationale ? el("p", { class: "hint small", text: plan.rationale }) : null),
    el("div", { class: "card stack" },
      el("div", { class: "row between" }, el("h3", { text: "Разделы, тезисы и бюджет слов" }),
        el("span", { class: "small", text: `сумма: ${total} слов · лимит журнала: ${m.template?.limits?.words_total ?? "не указан"}` })),
      secBox,
      el("div", {}, el("button", { class: "small", text: "+ раздел", onclick: () => { plan.sections.push({ heading: "New section", points: [], words: 200 }); drawSecs(); } }))),
    el("div", { class: "row" }, save, approve, gen,
      el("span", { class: "hint", text: "Изменение плана после согласования отправит его на повторное согласование; разделы пометятся как требующие обновления." })));
}

/* ---------- 4. tables and figures ---------- */

function msAssets(p, m, reload) {
  const tables = m.tables.map((t) => ({ ...t }));
  const figures = m.figures.map((f) => ({ ...f }));
  let figureLanguage = m.figure_language || "en";
  const row = (it, kind) => {
    const inc = el("input", { type: "checkbox", checked: it.include, onchange: (e) => { it.include = e.target.checked; } });
    const cap = el("input", { value: it.caption || "", placeholder: it.caption_effective, onchange: (e) => { it.caption = e.target.value; } });
    return el("tr", {}, el("td", {}, inc), el("td", { class: "mono small", text: `{{${kind}:${it.id}}}` }),
      el("td", { class: "small", text: it.kind }), el("td", { style: "min-width:340px" }, cap),
      kind === "FIG" ? el("td", {}, el("a", { href: `/api/projects/${p.id}/manuscript/figures/${it.id}.png?lang=${m.figure_language || "en"}`, target: "_blank", text: "просмотр" })) : el("td", {}));
  };
  const save = el("button", { class: "primary", text: "Сохранить" });
  save.addEventListener("click", () => busy(save, async () => reload(await api(`/projects/${p.id}/manuscript/assets`, { method: "PUT", json: { tables, figures, figure_language: figureLanguage } }))));
  const tbl = (items, kind) => el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["В статье", "Ссылка в тексте", "Тип", "Подпись (пусто — по умолчанию)", ""].map((h) => el("th", { text: h })))),
    el("tbody", {}, items.map((it) => row(it, kind)))));
  return el("div", { class: "stack" },
    el("div", { class: "card stack" }, el("h3", { text: "Таблицы (FR-5.6)" }),
      el("p", { class: "hint small", text: "Значения ячеек берутся из результатов анализа. Нумерация — по порядку включённых; в тексте используются ссылки {{TAB:…}}." }), tbl(tables, "TAB")),
    el("div", { class: "card stack" }, el("h3", { text: "Рисунки (FR-5.7)" }),
      el("p", { class: "hint small", text: `При экспорте рисунки выгружаются в формате и разрешении из профиля журнала (${m.template?.figures?.dpi_photo || 300} dpi).` }),
      el("label", { class: "row" }, el("span", { text: "Подписи на рисунках (оси, категории, легенды):" }),
        el("select", { onchange: (e) => { figureLanguage = e.target.value; } },
          [["en", "Английский — из «Терминологии»"], ["ru", "Русский — как в исходных данных"]].map(([v, t]) =>
            el("option", { value: v, text: t, selected: v === figureLanguage })))),
      el("p", { class: "hint small", text: "После смены языка нажмите «Сохранить» — «просмотр» и экспорт покажут рисунки на выбранном языке. Подписи под рисунками (Figure legends) пишутся в поле «Подпись»." }),
      tbl(figures, "FIG")),
    el("div", {}, save));
}

/* ---------- 5. sections ---------- */

function renderSegments(paragraphs) {
  return paragraphs.map((para) => {
    if (para.type === "heading") return el("h4", { text: para.segments[0].v });
    return el("p", { style: "line-height:1.7" }, para.segments.map((s) => {
      if (s.t === "text") return s.v;
      if (s.t === "fact") return el("span", { class: "fact", title: `${s.id}: ${s.desc}`, text: s.v });
      if (s.t === "ref") return el("span", { class: "fact", title: s.raw, text: s.v });
      if (s.t === "cite") return el("span", { class: s.bad.length ? "cite bad" : "cite", title: s.ids.join(", "), text: s.v });
      if (s.t === "todo") return el("span", { class: "todo", text: `[уточнить: ${s.v}]` });
      return el("span", { class: "todo", title: "нет такого факта / таблицы", text: s.v });
    }));
  });
}

function msSections(p, m, reload) {
  if (!state.msSection || !m.sections.find((s) => s.key === state.msSection)) state.msSection = m.sections[0]?.key;
  const list = el("div", { class: "card", style: "padding:8px" }, m.sections.map((s) => {
    const [cls, label] = SEC_STATUS[s.status];
    const nBlock = s.issues.filter((i) => i.severity === "blocking").length;
    return el("button", { class: "step" + (s.key === state.msSection ? " current" : ""), style: "width:100%;border:none;background:none;text-align:left;cursor:pointer",
      onclick: () => { state.msSection = s.key; reload(m); } },
      el("span", { class: "grow" }, el("div", { text: s.heading }),
        el("div", { class: "row", style: "gap:4px;margin-top:2px" }, el("span", { class: "badge " + cls, text: label }),
          s.stale ? el("span", { class: "badge danger", text: "требует обновления" }) : null,
          nBlock ? el("span", { class: "badge danger", text: `!${nBlock}` }) : null,
          s.words ? el("span", { class: "small muted", text: `${s.words}${s.budget ? "/" + s.budget : ""} сл.` }) : null)));
  }));
  const s = m.sections.find((x) => x.key === state.msSection);
  const editor = sectionEditor(p, m, s, reload);
  return el("div", { class: "project-layout", style: "grid-template-columns:230px minmax(0,1fr)" }, list, editor);
}

function sectionEditor(p, m, s, reload) {
  const post = async (path, json) => reload(await api(`/projects/${p.id}/manuscript/sections/${s.key}${path}`, json === undefined ? { method: "POST" } : { json }));
  const llmOn = state.meta.llm.available;
  const gen = el("button", { class: s.versions.length ? "" : "primary", text: s.kind === "statements" ? "Сформировать из данных автора" : (s.versions.length ? "Перегенерировать" : "Сгенерировать"),
    disabled: s.kind !== "statements" && !llmOn, title: llmOn ? state.meta.llm.reasoning_model : state.meta.llm.reason });
  gen.addEventListener("click", () => busy(gen, () => post("/generate")));
  const accept = el("button", { class: s.status === "accepted" ? "active-ok" : "ok", text: s.status === "accepted" ? `✓ Принят (v${s.accepted_version})` : "Принять раздел", disabled: !s.versions.length || s.status === "accepted" });
  accept.addEventListener("click", () => busy(accept, () => post("/accept")));
  const reopen = s.status === "accepted" ? el("button", { class: "small ghost", text: "вернуть в работу", onclick: () => post("/reopen") }) : null;

  if (!state.msView) state.msView = "preview";
  const views = [["preview", "Просмотр"], ["edit", "Редактор"], ["issues", `Проверки (${s.issues.length})`], ["versions", `Версии (${s.versions.length})`]];
  const vbar = el("div", { class: "tabs" }, views.map(([id, label]) => el("button", { class: "tab" + (state.msView === id ? " active" : ""), text: label,
    onclick: () => { state.msView = id; reload(m); } })));

  const textarea = el("textarea", { style: "min-height:420px;font-family:var(--mono);font-size:12.5px;line-height:1.6" }, s.source);
  let selection = "";
  const grabSelection = () => {
    if (state.msView === "edit") selection = textarea.value.slice(textarea.selectionStart, textarea.selectionEnd);
    else selection = String(window.getSelection() || "");
    return selection.trim();
  };
  let body;
  if (state.msView === "edit") {
    const insert = (txt) => {
      const a = textarea.selectionStart, b = textarea.selectionEnd;
      textarea.value = textarea.value.slice(0, a) + txt + textarea.value.slice(b);
      textarea.focus();
      textarea.selectionStart = textarea.selectionEnd = a + txt.length;
    };
    const factSel = el("select", { style: "max-width:260px" }, el("option", { value: "", text: "Вставить число из анализа…" }),
      m.facts.map((f) => el("option", { value: f.id, text: `${f.id} = ${f.value} — ${f.desc}` })));
    factSel.addEventListener("change", () => { if (factSel.value) insert(`{{${factSel.value}}}`); factSel.value = ""; });
    const citSel = el("select", { style: "max-width:260px" }, el("option", { value: "", text: "Вставить цитату…" }),
      m.citations.map((c) => el("option", { value: c.id, text: `${c.id} ${c.source}: ${c.claim.slice(0, 80)}` })));
    citSel.addEventListener("change", () => { if (citSel.value) insert(`[[${citSel.value}]]`); citSel.value = ""; });
    const refSel = el("select", { style: "max-width:200px" }, el("option", { value: "", text: "Ссылка на таблицу/рисунок…" }),
      [...m.tables.filter((t) => t.include).map((t) => [`{{TAB:${t.id}}}`, `Таблица: ${t.caption_effective}`]),
       ...m.figures.filter((f) => f.include).map((f) => [`{{FIG:${f.id}}}`, `Рисунок: ${f.caption_effective}`])].map(([v, l]) => el("option", { value: v, text: l })));
    refSel.addEventListener("change", () => { if (refSel.value) insert(refSel.value); refSel.value = ""; });
    const save = el("button", { class: "primary", text: "Сохранить версию" });
    save.addEventListener("click", () => busy(save, async () => reload(await api(`/projects/${p.id}/manuscript/sections/${s.key}`, { method: "PUT", json: { source: textarea.value } }))));
    body = el("div", { class: "stack" }, el("div", { class: "row" }, factSel, citSel, refSel), textarea, el("div", {}, save));
  } else if (state.msView === "issues") {
    body = s.issues.length ? el("div", { class: "stack" }, s.issues.map((i) => {
      const [cls, label] = SEVERITY[i.severity];
      const num = i.code === "number_unmatched" ? (i.message.match(/число (\S+)/) || [])[1] : null;
      return el("div", { class: "row", style: "align-items:flex-start;flex-wrap:nowrap;border-bottom:1px solid var(--border);padding:6px 0" },
        el("span", { class: "badge " + cls, text: label }),
        el("div", { class: "grow" }, el("div", { text: i.message }), i.fragment ? el("div", { class: "small muted mono", text: i.fragment }) : null,
          i.suggestion ? el("div", { class: "small", text: "→ " + i.suggestion }) : null),
        num ? el("button", { class: "small", text: "Проверено вручную", onclick: () => post("/whitelist", { number: num }) }) : null);
    })) : el("p", { class: "hint", text: "Замечаний нет." });
  } else if (state.msView === "versions") {
    const out = el("pre", { class: "small", style: "white-space:pre-wrap;max-height:360px;overflow:auto" });
    body = el("div", { class: "stack" }, el("div", { class: "table-wrap", style: "max-height:300px" }, el("table", {}, el("tbody", {},
      [...s.versions].reverse().map((v) => el("tr", {}, el("td", { class: "mono", text: "v" + v.n }), el("td", { class: "small", text: fmtDate(v.ts) }),
        el("td", { text: { agent: "агент", author: "автор", system: "система" }[v.actor] || v.actor }), el("td", { class: "small", text: v.note }),
        el("td", {}, v.n > 1 ? el("button", { class: "small ghost", text: "diff", onclick: async () => {
          const d = await api(`/projects/${p.id}/manuscript/sections/${s.key}/diff?a=${v.n - 1}&b=${v.n}`);
          out.replaceChildren(...d.diff.map((l) => el("div", { style: l.startsWith("+") ? "color:var(--ok)" : l.startsWith("-") ? "color:var(--danger)" : null, text: l })));
        } }) : null, v.n !== s.versions.length ? el("button", { class: "small ghost", text: "откатить", onclick: () => post("/rollback", { n: v.n }) }) : null)))))), out);
  } else {
    body = s.segments.length ? el("div", { class: "manuscript" }, renderSegments(s.segments)) : el("p", { class: "hint", text: "Раздел ещё не написан." });
  }

  const last = s.versions[s.versions.length - 1];
  const instr = el("input", { placeholder: "Инструкция агенту: «сократи до 250 слов», «усиль сравнение с Wu 2023»… (выделите фрагмент, чтобы применить только к нему)" });
  const apply = el("button", { class: "small primary", text: "Применить", disabled: !llmOn || !s.versions.length });
  apply.addEventListener("click", () => busy(apply, async () => {
    if (!instr.value.trim()) return;
    const sel = grabSelection();
    reload(await api(`/projects/${p.id}/manuscript/sections/${s.key}/revise`, { json: { instruction: instr.value.trim(), selection: sel || null } }));
  }));
  const comment = el("button", { class: "small", text: "Комментарий к выделенному", disabled: !s.versions.length, onclick: async () => {
    const sel = grabSelection();
    const text = prompt(sel ? `Комментарий к «${sel.slice(0, 80)}»:` : "Комментарий к разделу:");
    if (text) post("/comments", { quote: sel, text });
  } });
  return el("div", { class: "card stack" },
    el("div", { class: "row between" }, el("div", {}, el("h2", { text: s.heading }),
      s.plan ? el("div", { class: "small muted", text: "План: " + s.plan.points.join(" · ") + (s.plan.words ? ` (${s.plan.words} слов)` : "") }) : null),
      el("div", { class: "row" }, gen, accept, reopen)),
    s.stale ? el("div", { class: "banner warn small", text: "Входные данные раздела изменились (находки, анализ, литература или данные автора) — перегенерируйте или проверьте текст (FR-0.4)." }) : null,
    last?.questions?.length ? el("div", { class: "banner info small" }, el("strong", { text: "Агент просит уточнить:" }), el("ul", {}, last.questions.map((q) => el("li", { text: q })))) : null,
    last?.citations_report?.length ? el("div", { class: "small muted", text: "Новые цитаты: " + last.citations_report.map((r) => `${r.marker} → ${r.status === "added" ? r.citation + " (" + r.verification + ")" : "отклонена: " + r.reason}`).join("; ") }) : null,
    vbar, body,
    el("div", { class: "row", style: "flex-wrap:nowrap" }, instr, apply, comment),
    s.comments.filter((c) => !c.resolved).map((c) => el("div", { class: "comment row", style: "flex-wrap:nowrap" },
      el("div", { class: "grow" }, c.quote ? el("div", { class: "small muted", text: `«${c.quote.slice(0, 160)}»` }) : null, c.text),
      el("button", { class: "small ghost", text: "решено", onclick: () => post(`/comments/${c.id}/resolve`) }))));
}

/* ---------- 6. front matter ---------- */

function msFront(p, m, reload) {
  const f = { ...m.front };
  const gen = el("button", { text: "Предложить варианты", disabled: !state.meta.llm.available });
  gen.addEventListener("click", () => busy(gen, async () => reload(await api(`/projects/${p.id}/manuscript/front/generate`, { method: "POST" }))));
  const title = el("input", { value: f.title || "", placeholder: "Название статьи" });
  const maxc = m.template?.title?.max_chars;
  const counter = el("span", { class: "small muted", text: `${(f.title || "").length}${maxc ? " / " + maxc : ""} символов` });
  title.addEventListener("input", () => { counter.textContent = `${title.value.length}${maxc ? " / " + maxc : ""} символов`; });
  const running = el("input", { value: f.running_title || "" });
  const kw = el("input", { value: (f.keywords || []).join("; ") });
  const hl = el("textarea", {}, (f.highlights || []).join("\n"));
  const save = el("button", { class: "primary", text: "Сохранить" });
  save.addEventListener("click", () => busy(save, async () => reload(await api(`/projects/${p.id}/manuscript/front`, { method: "PUT",
    json: { title: title.value, running_title: running.value, keywords: kw.value.split(";"), highlights: hl.value.split("\n") } }))));
  return el("div", { class: "card stack" },
    el("div", { class: "row" }, gen, el("span", { class: "hint", text: "Варианты строятся по принятым разделам (Title → 3–5 вариантов)." })),
    (f.title_candidates || []).length ? el("div", {}, el("h4", { text: "Варианты названия" }), f.title_candidates.map((t) =>
      el("div", { class: "row small", style: "flex-wrap:nowrap" }, el("button", { class: "small", text: "выбрать", onclick: () => { title.value = t; title.dispatchEvent(new Event("input")); } }), t))) : null,
    el("label", { class: "field" }, el("span", { text: "Название" }), title, counter),
    el("label", { class: "field" }, el("span", { text: "Running title" }), running),
    el("label", { class: "field" }, el("span", { text: `Ключевые слова (через «;»)${m.template?.keywords?.max ? `, до ${m.template.keywords.max}` : ""}` }), kw),
    el("label", { class: "field" }, el("span", { text: "Highlights (по строке)" }), hl),
    el("div", {}, save));
}

/* ---------- step 10: export ---------- */

async function viewExport(root, p) {
  const st = await api(`/projects/${p.id}/manuscript/export/status`);
  const download = async (mode, btn) => busy(btn, async () => {
    const res = await fetch(`/api/projects/${p.id}/manuscript/export?mode=${mode}`, { method: "POST", headers: { "X-AIA": "1" } });
    if (!res.ok) { let msg = res.statusText; try { msg = (await res.json()).detail; } catch (_) {} throw new Error(msg); }
    const blob = await res.blob();
    const a = el("a", { href: URL.createObjectURL(blob), download: mode === "final" ? "manuscript_submission.zip" : "manuscript_DRAFT.zip" });
    document.body.append(a); a.click(); a.remove();
  });
  const draftBtn = el("button", { text: "Скачать черновик (.zip)" });
  draftBtn.addEventListener("click", () => download("draft", draftBtn));
  const finalBtn = el("button", { class: "primary", text: "Скачать пакет для подачи (.zip)", disabled: !st.ready });
  finalBtn.addEventListener("click", () => download("final", finalBtn));
  const groups = {};
  for (const b of st.blocking) (groups[b.heading || "—"] = groups[b.heading || "—"] || []).push(b);
  root.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "card stack" }, el("h2", { text: "Экспорт (FR-5.13)" }),
      el("p", { class: "hint", text: "Пакет: manuscript.docx (Times New Roman 12, двойной интервал, нумерация строк и страниц), manuscript.md, рисунки в формате и разрешении журнала, supplementary (анонимизированная таблица случаев, скрипт анализа, журнал гипотез)." }),
      el("div", { class: "row" }, draftBtn, finalBtn),
      st.ready ? el("div", { class: "banner ok", text: "Блокирующих проблем нет — рукопись готова к подаче." })
        : el("div", { class: "banner warn", text: `Экспорт для подачи заблокирован: ${st.blocking.length}. Черновик доступен всегда — с пометкой DRAFT и списком открытых вопросов.` })),
    st.blocking.length ? el("div", { class: "card stack" }, el("h3", { text: "Что нужно решить" }),
      Object.entries(groups).map(([g, items]) => el("div", {}, el("h4", { text: g }), el("ul", { class: "small" }, items.map((i) => el("li", {}, i.message, i.fragment ? el("span", { class: "muted", text: ` — ${i.fragment.slice(0, 120)}` }) : null)))))) : null,
    st.warnings.length ? el("details", { class: "card" }, el("summary", { text: `Предупреждения (${st.warnings.length})` }),
      el("ul", { class: "small" }, st.warnings.map((w) => el("li", { text: `${w.heading}: ${w.message}` })))) : null));
}
