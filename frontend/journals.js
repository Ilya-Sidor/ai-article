"use strict";
/* Module 4 — journal profiles. Uses helpers from app.js: el, api, toast, busy, fmtDate, state, route. */

const PROFILE_STATUS = {
  empty: ["", "не заполнен"], draft: ["exploratory", "черновик"], verified: ["ok", "проверен"],
  stale: ["danger", "проверка устарела (> 90 дней)"], changed: ["danger", "guidelines изменились"],
  outdated_verification: ["exploratory", "изменён после проверки"],
};
const FIELD_STATUS = {
  grounded: ["ok", "цитата из guidelines"], unverified: ["danger", "цитата не найдена"],
  manual: ["accent", "вручную"], missing: ["", "нет в guidelines"],
};
const CHECK_STATUS = { pass: ["ok", "✓"], fail: ["danger", "✕"], warn: ["exploratory", "!"],
  pending: ["", "…"], manual: ["accent", "?"] };

function statusBadge(st) {
  const [cls, label] = PROFILE_STATUS[st] || ["", st];
  return el("span", { class: "badge " + cls, text: label });
}

async function journalSchema() {
  if (!state.journalSchema) state.journalSchema = await api("/journals/schema");
  return state.journalSchema;
}

/* ---------- base of journals ---------- */

async function renderJournals(app) {
  const list = await api("/journals");
  const name = el("input", { placeholder: "Название журнала" });
  const publisher = el("input", { placeholder: "Издатель" });
  const url = el("input", { placeholder: "URL страницы Author Guidelines" });
  const add = el("button", { class: "primary", text: "Добавить журнал" });
  add.addEventListener("click", () => busy(add, async () => {
    const j = await api("/journals", { json: { name: name.value, publisher: publisher.value, guidelines_url: url.value } });
    location.hash = `#/journals/${j.id}`;
  }));
  app.replaceChildren(
    el("div", { class: "row between", style: "margin-bottom:16px" }, el("h1", { text: "База профилей журналов" }),
      el("a", { href: "#/", class: "small", text: "← Проекты" })),
    el("div", { class: "banner info small", style: "margin-bottom:16px",
      text: "Значения требований не зашиты в код: они берутся из актуальных Author Guidelines — каждое с цитатой — и подтверждаются человеком с указанием даты и URL. Профиль старше 90 дней требует перепроверки." }),
    el("div", { class: "card" }, el("div", { class: "table-wrap", style: "max-height:none" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Журнал", "Издатель", "Статус", "Поля", "Проверен", "Guidelines"].map((h) => el("th", { text: h })))),
      el("tbody", {}, list.map((j) => el("tr", {},
        el("td", {}, el("a", { href: `#/journals/${j.id}`, text: j.name }), j.custom ? el("span", { class: "badge", style: "margin-left:6px", text: "пользовательский" }) : null),
        el("td", { class: "small", text: j.publisher || "—" }),
        el("td", {}, statusBadge(j.status)),
        el("td", { class: "small", text: Object.entries(j.field_counts).map(([k, v]) => `${(FIELD_STATUS[k] || [0, k])[1]}: ${v}`).join(" · ") || "—" }),
        el("td", { class: "small", text: j.verification.verified_at ? `${fmtDate(j.verification.verified_at)} · ${j.verification.verified_by}` : "—" }),
        el("td", { class: "small" }, j.guidelines_url ? el("a", { href: j.guidelines_url, target: "_blank", rel: "noopener", text: "официальный сайт ↗" }) : "—"))))))),
    el("div", { class: "card stack" }, el("h2", { text: "Добавить журнал (FR-4.2)" }),
      el("div", { class: "form-grid" }, name, publisher, url), el("div", {}, add)));
}

/* ---------- one profile ---------- */

async function renderJournal(app, jid) {
  const [p, sch] = await Promise.all([api(`/journals/${jid}`), journalSchema()]);
  const reload = () => renderJournal(app, jid);
  const s = p.summary;

  // sources
  const pasted = el("textarea", { style: "min-height:110px", placeholder: "Откройте страницу guidelines в браузере, выделите всё (⌘A) и вставьте сюда" });
  const addText = el("button", { class: "primary small", text: "Сохранить текст" });
  addText.addEventListener("click", () => busy(addText, async () => {
    await api(`/journals/${jid}/sources/text`, { json: { text: pasted.value, url: p.guidelines_url || null } });
    toast("Текст guidelines сохранён");
    reload();
  }));
  const pdfInput = el("input", { type: "file", accept: ".pdf", class: "hidden" });
  const pdfBtn = el("button", { class: "small", text: "Загрузить PDF страницы", onclick: () => pdfInput.click() });
  pdfInput.addEventListener("change", () => busy(pdfBtn, async () => {
    await sendFiles(`/journals/${jid}/sources/pdf`, [pdfInput.files[0]], { field: "file" });
    reload();
  }));
  const urlBtn = el("button", { class: "small", text: "Загрузить по URL", disabled: !p.guidelines_url });
  urlBtn.addEventListener("click", () => busy(urlBtn, async () => {
    await api(`/journals/${jid}/sources/url`, { json: { url: p.guidelines_url } });
    reload();
  }));
  const recheckBtn = el("button", { class: "small", text: "Перепроверить guidelines", disabled: !p.sources.length });
  recheckBtn.addEventListener("click", () => busy(recheckBtn, async () => {
    const r = await api(`/journals/${jid}/recheck`, { method: "POST" });
    for (const x of r.results) toast(`${x.source}: ${x.status === "unchanged" ? "без изменений" : x.status === "changed" ? "ИЗМЕНИЛИСЬ" : x.message}`, x.status === "error" ? "error" : null);
    reload();
  }));
  const sourcesCard = el("div", { class: "card stack" },
    el("div", { class: "row between" }, el("h2", { text: "Текст Author Guidelines" }), el("div", { class: "row" }, urlBtn, pdfBtn, pdfInput, recheckBtn)),
    el("p", { class: "hint", text: "Большинство издателей закрывают guidelines от автоматической загрузки. Надёжный путь — сохранить страницу как PDF или вставить её текст. Для перепроверки загрузите новую версию: изменения будут показаны построчно." }),
    p.sources.length ? el("div", { class: "table-wrap", style: "max-height:none" }, el("table", {},
      el("thead", {}, el("tr", {}, ["ID", "Тип", "Загружен", "Символов", "Хэш", ""].map((h) => el("th", { text: h })))),
      el("tbody", {}, p.sources.map((src) => {
        const upd = el("input", { type: "file", accept: ".pdf,.txt,.html", class: "hidden" });
        upd.addEventListener("change", async () => {
          const f = upd.files[0];
          try {
            let r;
            if (f.name.toLowerCase().endsWith(".pdf")) {
              r = await sendFiles(`/journals/${jid}/sources/pdf`, [f], { field: "file", extra: { replaces: src.id } });
            } else {
              r = await api(`/journals/${jid}/sources/text`, { json: { text: await f.text(), replaces: src.id, url: src.url } });
            }
            toast(r.changed ? "Guidelines изменились — просмотрите изменения" : "Текст не изменился");
            reload();
          } catch (e) { toast(e.message, "error"); }
        });
        return el("tr", {}, el("td", { class: "mono", text: src.id }),
          el("td", { text: { url: "URL", pdf: "PDF", text: "вставленный текст" }[src.kind] + (src.filename ? ` (${src.filename})` : "") }),
          el("td", { class: "small", text: fmtDate(src.fetched_at) }), el("td", { class: "num", text: src.chars }),
          el("td", { class: "mono small", text: src.hash }),
          el("td", {}, el("div", { class: "row", style: "gap:4px;flex-wrap:nowrap" },
            el("button", { class: "small ghost", text: "Текст", onclick: async () => {
              const t = await api(`/journals/${jid}/sources/${src.id}`);
              const w = window.open("", "_blank"); if (w) { w.document.title = src.id; const pre = w.document.createElement("pre"); pre.style.whiteSpace = "pre-wrap"; pre.textContent = t.text; w.document.body.append(pre); }
            } }),
            el("button", { class: "small ghost", text: "Новая версия", onclick: () => upd.click() }), upd,
            el("button", { class: "small ghost", text: "✕", onclick: async () => { if (confirm("Удалить источник?")) { await api(`/journals/${jid}/sources/${src.id}`, { method: "DELETE" }); reload(); } } }))));
      })))) : null,
    el("div", { class: "stack", style: "gap:8px" }, pasted, el("div", {}, addText)));

  // change banner
  const change = p.guidelines_changed ? el("div", { class: "banner warn stack" },
    el("strong", { text: `Guidelines изменились (${fmtDate(p.guidelines_changed.detected_at)}, источник ${p.guidelines_changed.source_id}). Проверьте изменения, обновите поля и подтвердите профиль заново.` }),
    el("pre", { class: "small", style: "max-height:260px;overflow:auto;background:var(--surface);padding:8px;border-radius:6px" },
      p.guidelines_changed.diff.map((l) => el("div", { style: l.startsWith("+") ? "color:var(--ok)" : l.startsWith("-") ? "color:var(--danger)" : null, text: l }))),
    el("div", {}, el("button", { class: "small", text: "Изменения просмотрены", onclick: async () => { await api(`/journals/${jid}/acknowledge-change`, { method: "POST" }); reload(); } }))) : null;

  // extraction
  const llmOn = state.meta.llm.available;
  const exBtn = el("button", { class: "primary", text: "Извлечь профиль из guidelines", disabled: !llmOn || !p.sources.length,
    title: !llmOn ? state.meta.llm.reason : !p.sources.length ? "сначала добавьте текст guidelines" : `${state.meta.llm.extraction_model}; каждое значение — с цитатой` });
  exBtn.addEventListener("click", () => busy(exBtn, async () => {
    const r = await api(`/journals/${jid}/extract`, { method: "POST" });
    toast(`Извлечено полей: ${r.extraction.n_changed}; ручные значения сохранены (${r.extraction.skipped_manual.length})`);
    reload();
  }));

  // verification
  const who = el("input", { placeholder: "Кто проверил (ФИО, роль)", value: p.verification.verified_by || "" });
  const verBtn = el("button", { class: "ok", text: "Подтвердить: профиль сверен с официальным сайтом" });
  verBtn.addEventListener("click", () => busy(verBtn, async () => {
    await api(`/journals/${jid}/verify`, { json: { verified_by: who.value } });
    toast("Профиль подтверждён");
    reload();
  }));
  const counts = s.field_counts;

  // fields editor
  const edits = {};
  const groups = Object.entries(sch.groups);
  const tabs = groups.map(([g]) => g);
  state.journalGroup = tabs.includes(state.journalGroup) ? state.journalGroup : tabs[0];
  const fieldsBox = el("div", {});
  const renderFields = () => {
    const [, fields] = groups.find(([g]) => g === state.journalGroup);
    fieldsBox.replaceChildren(el("div", { class: "table-wrap", style: "max-height:none" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Параметр", "Значение", "Источник", ""].map((h) => el("th", { text: h })))),
      el("tbody", {}, fields.map((f) => {
        const cur = p.fields[f.path];
        const v = edits[f.path]?.value ?? (cur?.value ?? "");
        let input;
        if (f.type === "bool") {
          input = el("select", {}, [["", "—"], ["true", "да"], ["false", "нет"]].map(([k, l]) => el("option", { value: k, selected: String(v) === k, text: l })));
        } else if (f.type.startsWith("enum:")) {
          input = el("select", {}, el("option", { value: "", text: "—" }), f.type.slice(5).split("|").map((o) =>
            el("option", { value: o, selected: v === o, text: sch.requirement_labels[o] || o })));
        } else {
          input = el("input", { value: Array.isArray(v) ? v.join("; ") : v, placeholder: f.type === "list" ? "через «;»" : f.type === "int" ? "число" : "" });
        }
        input.addEventListener("change", () => { edits[f.path] = { value: input.value }; saveBtn.disabled = false; });
        const suggestion = p.suggestions?.[f.path];
        const st = cur ? FIELD_STATUS[cur.status] : null;
        return el("tr", {},
          el("td", { style: "min-width:220px" }, el("div", { text: f.label }), f.hint ? el("div", { class: "small muted", text: f.hint }) : null),
          el("td", { style: "min-width:220px" }, input,
            suggestion && !cur?.value ? el("div", { class: "small", style: "margin-top:4px" }, "предложение: ", el("code", { text: suggestion }), " ",
              el("button", { class: "small ghost", text: "подставить", onclick: () => { input.value = suggestion; edits[f.path] = { value: suggestion }; saveBtn.disabled = false; } })) : null),
          el("td", { class: "small", style: "max-width:380px" }, st ? el("span", { class: "badge " + st[0], text: st[1] }) : el("span", { class: "muted", text: "не заполнено" }),
            cur?.quote ? el("div", { class: "muted", style: "margin-top:4px", text: "«" + cur.quote + "»" }) : null),
          el("td", {}, el("button", { class: "small ghost", title: "Отметить: в guidelines не указано", text: "нет в guidelines",
            onclick: () => { edits[f.path] = { missing: true }; input.value = ""; saveBtn.disabled = false; } })));
      })))));
  };
  const saveBtn = el("button", { class: "primary", text: "Сохранить изменения", disabled: true });
  saveBtn.addEventListener("click", () => busy(saveBtn, async () => {
    await api(`/journals/${jid}/fields`, { method: "PUT", json: { updates: edits } });
    toast("Сохранено; значения, введённые вручную, отмечены как «вручную»");
    reload();
  }));
  const groupTabs = el("div", { class: "tabs" }, tabs.map((g) => el("button", { class: "tab" + (g === state.journalGroup ? " active" : ""), text: g,
    onclick: () => { state.journalGroup = g; renderJournal(app, jid); } })));
  renderFields();

  app.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "row between" },
      el("div", {}, el("h1", { text: p.name }), el("div", { class: "meta" }, statusBadge(s.status), el("span", { text: p.publisher || "" }),
        p.guidelines_url ? el("a", { href: p.guidelines_url, target: "_blank", rel: "noopener", text: "Author Guidelines ↗" }) : null,
        el("span", { text: `версия ${p.version}` }))),
      el("a", { href: "#/journals", class: "small", text: "← База журналов" })),
    change,
    sourcesCard,
    el("div", { class: "card stack" },
      el("div", { class: "row between" }, el("h2", { text: "Профиль" }), el("div", { class: "row" }, exBtn, saveBtn)),
      el("div", { class: "chips" }, Object.entries(FIELD_STATUS).map(([k, [cls, label]]) => el("span", { class: "badge " + cls, text: `${label}: ${counts[k] || 0}` }))),
      p.extraction ? el("p", { class: "hint small", text: `Извлечено ${fmtDate(p.extraction.created_at)} (${p.extraction.model}); ${p.extraction.invalid?.length ? "отклонено некорректных значений: " + p.extraction.invalid.length : ""}` }) : null,
      groupTabs, fieldsBox),
    el("div", { class: "card stack" }, el("h2", { text: "Проверка человеком (FR-4.1)" }),
      p.verification.status === "verified" || p.verification.status === "outdated"
        ? el("p", { text: `Проверен: ${p.verification.verified_by}, ${fmtDate(p.verification.verified_at)}, по ${p.verification.guidelines_url}` + (p.verification.status === "outdated" ? " — после этого профиль изменялся" : "") }) : null,
      el("p", { class: "hint", text: "Подтверждение фиксирует дату, URL и версии текста guidelines. Нельзя подтвердить профиль с полями без подтверждающей цитаты — исправьте их вручную или отметьте «нет в guidelines»." }),
      el("div", { class: "row", style: "flex-wrap:nowrap" }, who, verBtn)),
    el("details", {}, el("summary", { text: `История изменений (${(p.history || []).length})` }),
      el("div", { class: "table-wrap", style: "margin-top:8px;max-height:300px" }, el("table", {}, el("tbody", {}, [...(p.history || [])].reverse().map((h) =>
        el("tr", {}, el("td", { class: "mono", text: "v" + h.version }), el("td", { class: "small", text: fmtDate(h.ts) }), el("td", { text: h.actor }),
          el("td", { class: "mono small", text: h.action }), el("td", { class: "small muted", text: (h.changed || []).join(", ") })))))))));
}

/* ---------- project: journal selection (step 1) ---------- */

async function projectJournalCard(p) {
  const [list, pj] = await Promise.all([api("/journals"), api(`/projects/${p.id}/journal`)]);
  const sel = el("select", {}, el("option", { value: "", text: "— выберите журнал из базы —" }),
    list.map((j) => el("option", { value: j.id, selected: p.journal_id === j.id, text: `${j.name} · ${PROFILE_STATUS[j.status][1]}` })));
  const apply = el("button", { class: "primary small", text: "Выбрать журнал" });
  apply.addEventListener("click", () => busy(apply, async () => {
    if (!sel.value) return;
    const r = await api(`/projects/${p.id}/journal`, { method: "PUT", json: { journal_id: sel.value } });
    toast(r.applied.message);
    route();
  }));
  const prof = pj.profile;
  return el("div", { class: "card stack" },
    el("h2", { text: "Целевой журнал (модуль 4)" }),
    el("div", { class: "row", style: "flex-wrap:nowrap" }, sel, apply, el("a", { class: "btn small", href: "#/journals", text: "База журналов" })),
    prof ? el("div", { class: "row small" }, statusBadge(prof.status), el("a", { href: `#/journals/${prof.id}`, text: "Открыть профиль" }),
      prof.verification.verified_at ? el("span", { class: "muted", text: `проверен ${fmtDate(prof.verification.verified_at)}` }) : null) : null,
    prof && prof.status !== "verified" ? el("div", { class: "banner warn small", text: "Профиль журнала не подтверждён человеком: лимиты и требования нельзя считать достоверными. Заполните и подтвердите профиль по официальному сайту." }) : null,
    el("p", { class: "hint small", text: "При выборе журнала стиль цитирования из профиля применяется к списку литературы; смена журнала — сценарий переподачи (FR-0.5)." }));
}

/* ---------- step 6: structure by profile ---------- */

async function viewOutline(root, p) {
  const pj = await api(`/projects/${p.id}/journal`);
  if (!pj.profile) {
    root.replaceChildren(el("div", { class: "card empty" }, "Выберите целевой журнал на шаге «Проект». ", el("a", { href: `#/p/${p.id}/project`, text: "Выбрать" })));
    return;
  }
  const t = pj.template;
  const v = (x, suffix = "") => x === null || x === undefined || (Array.isArray(x) && !x.length) ? el("span", { class: "muted", text: "не указано" }) : (Array.isArray(x) ? x.join(" · ") : String(x) + suffix);
  const req = (r) => r ? el("span", { class: "badge " + (r === "required" ? "danger" : r === "not_accepted" ? "" : "accent"), text: { required: "обязательно", recommended: "рекомендуется", optional: "по желанию", not_accepted: "не принимается" }[r] }) : el("span", { class: "muted", text: "не указано" });
  root.replaceChildren(el("div", { class: "stack" },
    pj.profile.status !== "verified" ? el("div", { class: "banner warn", text: `Профиль «${t.journal}» — ${PROFILE_STATUS[pj.profile.status][1]}. Структура ниже построена по непроверенным данным.` }) : null,
    t.accepted === false ? el("div", { class: "banner warn", text: `Журнал не принимает тип «${t.article_type_label}». Смените тип статьи или журнал.` }) : null,
    t.missing.length ? el("div", { class: "banner info small", text: "В профиле не заполнено: " + t.missing.join(", ") }) : null,
    el("div", { class: "card stack" },
      el("h2", { text: `Структура: ${t.type_name || t.article_type_label} — ${t.journal}` }),
      el("p", { class: "hint", text: "Шаблон рукописи по профилю журнала (FR-4.4). Согласование плана с тезисами и бюджетом слов по разделам — модуль 5 (FR-5.1)." }),
      el("dl", { class: "kv" },
        el("dt", { text: "Разделы" }), el("dd", {}, t.sections.length ? el("ol", { style: "margin:0;padding-left:18px" }, t.sections.map((s) => el("li", { text: s }))) : v(null)),
        el("dt", { text: "Лимит слов" }), el("dd", {}, v(t.limits.words_total), t.limits.words_counted ? el("div", { class: "small muted", text: t.limits.words_counted }) : null),
        el("dt", { text: "Abstract" }), el("dd", {}, t.abstract.structured === null ? v(null) : (t.abstract.structured ? "структурированный: " + (t.abstract.headings.join(" / ") || "заголовки не указаны") : "неструктурированный"), ", ", v(t.abstract.words, " слов")),
        el("dt", { text: "Название" }), el("dd", {}, v(t.title.max_chars, " символов"), t.title.max_words ? `, ${t.title.max_words} слов` : ""),
        el("dt", { text: "Ключевые слова" }), el("dd", {}, t.keywords.max ? `${t.keywords.min ?? "?"}–${t.keywords.max}${t.keywords.mesh ? ", MeSH" : ""}` : v(null)),
        el("dt", { text: "Таблицы / рисунки" }), el("dd", {}, `${t.limits.tables ?? "—"} / ${t.limits.figures ?? "—"}`, t.limits.tables_figures ? ` (вместе ≤ ${t.limits.tables_figures})` : ""),
        el("dt", { text: "Ссылки" }), el("dd", {}, v(t.limits.references), t.citation.style_name ? ` · ${t.citation.style_name}` : "", t.citation.csl_id ? el("span", { class: "mono small", text: ` (${t.citation.csl_id})` }) : null),
        el("dt", { text: "Английский" }), el("dd", {}, v(t.language_variant)),
        el("dt", { text: "Тон" }), el("dd", {}, v(t.tone)))),
    el("div", { class: "card stack" }, el("h3", { text: "Обязательные заявления и элементы" }),
      el("table", { class: "ct", style: "width:100%" }, el("tbody", {},
        [...t.statements, ...t.extras].map((s) => el("tr", {}, el("td", { style: "text-align:left", text: s.label }), el("td", { style: "text-align:left" }, req(s.requirement)))))),
      t.reporting_guidelines.length ? el("p", { text: "Чек-листы отчётности: " + t.reporting_guidelines.join(", ") }) : null,
      t.ai_policy ? el("p", { class: "small", text: "Политика по ИИ: " + t.ai_policy }) : null)));
}

/* ---------- step 8: pre-submission checks ---------- */

async function viewChecks(root, p) {
  const pj = await api(`/projects/${p.id}/journal`);
  if (!pj.profile) {
    root.replaceChildren(el("div", { class: "card empty" }, "Выберите целевой журнал на шаге «Проект». ", el("a", { href: `#/p/${p.id}/project`, text: "Выбрать" })));
    return;
  }
  const render = (data) => {
    const groups = {};
    for (const i of data.checklist) (groups[i.group] = groups[i.group] || []).push(i);
    root.replaceChildren(el("div", { class: "stack" },
      el("div", { class: "card stack" }, el("h2", { text: "Индикатор соответствия (FR-4.5)" }),
        el("div", { class: "table-wrap", style: "max-height:none" }, el("table", {},
          el("thead", {}, el("tr", {}, ["Параметр", "Сейчас", "Лимит журнала", ""].map((h) => el("th", { text: h })))),
          el("tbody", {}, data.compliance.map((c) => el("tr", {}, el("td", { text: c.label }),
            el("td", { class: "num", text: c.current ?? "—" }), el("td", { class: "num", text: c.limit ?? "не указан" }),
            el("td", {}, el("span", { class: "badge " + ({ ok: "ok", near: "exploratory", over: "danger" }[c.status] || ""),
              text: { ok: "в пределах", near: "близко к лимиту", over: "превышен", no_limit: "без лимита", pending: "после генерации текста" }[c.status] })))))))),
      el("div", { class: "card stack" },
        el("div", { class: "row between" }, el("h2", { text: "Чек-лист перед подачей (FR-4.6)" }),
          el("span", { class: "badge " + (data.blocking ? "danger" : "ok"), text: data.blocking ? `блокирующих пунктов: ${data.blocking}` : "блокирующих пунктов нет" })),
        el("p", { class: "hint", text: "Пункты формируются из профиля журнала и проверяются автоматически. «…» — проверка появится с модулем генерации текста; «?» — подтверждается автором." }),
        Object.entries(groups).map(([g, items]) => el("div", {}, el("h3", { text: g }),
          items.map((i) => {
            const [cls, mark] = CHECK_STATUS[i.status];
            const tick = i.status === "manual" || (i.status === "pass" && i.detail.startsWith("отмечено автором"))
              ? el("input", { type: "checkbox", checked: i.status === "pass", onchange: async (e) => {
                  render(await api(`/projects/${p.id}/journal/checklist`, { json: { key: i.key, done: e.target.checked } })); } }) : null;
            return el("div", { class: "row", style: "align-items:flex-start;flex-wrap:nowrap;padding:6px 0;border-bottom:1px solid var(--border)" },
              el("span", { class: "badge " + cls, style: "min-width:26px;justify-content:center", text: mark }),
              el("div", { class: "grow" }, el("div", {}, i.text, i.blocking ? el("span", { class: "badge danger", style: "margin-left:6px", text: "блокирует экспорт" }) : null),
                i.detail ? el("div", { class: "small muted", text: i.detail }) : null),
              tick);
          }))))));
  };
  render(pj);
}
