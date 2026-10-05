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
    root.replaceChildren(m.case_report
      ? el("div", { class: "card empty" }, "Сначала загрузите данные случая и подтвердите анонимизацию. ", el("a", { href: `#/p/${p.id}/data`, text: "К данным" }))
      : el("div", { class: "card empty" }, "Сначала выполните анализ. ", el("a", { href: `#/p/${p.id}/analysis`, text: "К анализу" })));
    return;
  }
  const approved = m.plan && m.plan.status === "approved";
  const tabs = [["terms", "1. Терминология"], ["inputs", "2. Данные автора"], ["plan", "3. План"], ["assets", "4. Таблицы и рисунки"],
    ["sections", "5. Разделы"], ["front", "6. Название и ключевые слова"], ["review", "Рецензия и согласованность"],
    ["fix", "Что исправить"], ["revision", "Ответ рецензентам"]];
  if (!state.msTab) state.msTab = approved ? "sections" : "plan";
  const bar = el("div", { class: "tabs" }, tabs.map(([id, label]) => el("button", {
    class: "tab" + (state.msTab === id ? " active" : ""), text: label, disabled: id === "sections" && !approved,
    onclick: () => { state.msTab = id; viewDraftRender(root, p, m, reload); } })));
  const c = m.counts || {};
  const lim = m.template?.limits || {};
  const counters = el("div", { class: "row small", style: "gap:14px" },
    el("span", {}, "Основной текст: ", el("strong", { text: `${c.words ?? 0}` }), ` / ${lim.words_total ?? "—"} слов`),
    el("span", {}, m.lang === "ru" ? "Резюме: " : "Abstract: ", el("strong", { text: `${c.abstract_words ?? 0}` }), ` / ${m.template?.abstract?.words ?? "—"}`),
    el("span", { text: `Таблиц: ${c.tables ?? 0} · рисунков: ${c.figures ?? 0} · ссылок: ${c.references ?? 0}` }),
    m.blocking
      ? el("button", { class: "badge danger", style: "border:none;cursor:pointer", text: `блокирующих замечаний: ${m.blocking} — показать и исправить`,
          onclick: () => { state.msTab = "fix"; viewDraftRender(root, p, m, reload); } })
      : el("span", { class: "badge ok", text: "блокирующих замечаний нет" }));
  const body = { terms: msTerms, inputs: msInputs, plan: msPlan, assets: msAssets, sections: msSections, front: msFront, review: msReview, revision: msRevision,
    fix: (pp) => msFix(pp, () => reload()) }[state.msTab](p, m, reload);
  root.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "banner info small", text: (m.case_report ? "Case report по руководству CARE. Числа и признаки в тексте — ссылки на данные случая ({{…}}), их подставляет код." : "Числа в тексте — ссылки на результаты анализа ({{…}}), их подставляет код.") + " Цитаты — только проверенные фрагменты базы литературы. Каждый раздел: черновик → правки → принятие; все версии сохраняются." }),
    el("div", { class: "card", style: "padding:12px 16px" }, counters),
    el("div", {}, bar, body)));
}

/* ---------- issues: where and how to fix (fix descriptors come from backend/app/manuscript/fixes.py) ---------- */

function gotoFix(p, goto) {
  if (!goto) return;
  if (goto.step === "draft") {
    state.msTab = goto.tab || "sections";
    if (goto.section) { state.msSection = goto.section; state.msView = "preview"; }
  }
  const hash = `#/p/${p.id}/${goto.step}`;
  if (location.hash === hash) route(); else location.hash = hash;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function issueRow(p, i, after) {
  const [cls, label] = SEVERITY[i.severity] || SEVERITY.info;
  const fix = i.fix || { actions: [] };
  const sectionOf = (a) => a.section || i.section || (fix.goto && fix.goto.section);
  const base = (a) => `/projects/${p.id}/manuscript/sections/${sectionOf(a)}`;
  const run = (btn, fn) => busy(btn, async () => { await fn(); toast("Готово"); await after(); });
  const handlers = {
    remove: (a) => api(`${base(a)}/replace`, { json: { find: a.find, replace: "" } }),
    whitelist: (a) => api(`${base(a)}/whitelist`, { json: { number: a.number } }),
    revise: (a) => api(`${base(a)}/revise`, { json: { instruction: a.instruction, selection: a.selection || null } }),
    accept: (a) => api(`${base(a)}/accept`, { method: "POST" }),
    auto_terms: () => api(`/projects/${p.id}/manuscript/terms/auto`, { method: "POST" }),
    citation: (a) => api(`/projects/${p.id}/literature/citations/${a.id}`, { json: { decision: a.decision } }),
    verify: (a) => api(`/projects/${p.id}/literature/citations/${a.id}/verify`, { method: "POST" }),
    remark: (a) => api(`/projects/${p.id}/manuscript/remarks/${a.id}`, { json: { status: a.status } }),
    humanize: (a) => api(`${base(a)}/humanize`, { method: "POST" }),
    consent: async () => {
      const cur = await api(`/projects/${p.id}/manuscript`);
      const inputs = { ...(cur.inputs || {}) };
      inputs.consent = { ...(inputs.consent || {}), status: "written" };
      return api(`/projects/${p.id}/manuscript/inputs`, { method: "PUT", json: inputs });
    },
  };
  const controls = fix.actions.map((a) => {
    if (a.type === "fill") {  // fill an [уточнить: …] gap right here
      const input = el("input", { placeholder: a.placeholder, style: "min-width:260px;flex:1" });
      const btn = el("button", { class: "small primary", text: a.label });
      btn.addEventListener("click", () => {
        if (!input.value.trim()) { input.focus(); return; }
        run(btn, () => api(`${base(a)}/replace`, { json: { find: a.find, replace: input.value } }));
      });
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") btn.click(); });
      return el("div", { class: "row", style: "flex:1" }, input, btn);
    }
    if (a.type === "goto") return el("button", { class: "small", text: a.label, onclick: () => gotoFix(p, a.goto) });
    const btn = el("button", { class: "small" + (["revise", "auto_terms", "humanize"].includes(a.type) ? " primary" : ""), text: a.label,
      disabled: ["revise", "auto_terms", "humanize"].includes(a.type) && !state.meta.llm.available,
      title: a.instruction || "" });
    btn.addEventListener("click", () => run(btn, () => handlers[a.type](a)));
    return btn;
  });
  const open = fix.goto ? el("button", { class: "small ghost", text: "Открыть место →", onclick: () => gotoFix(p, fix.goto) }) : null;
  return el("div", { class: "issue-row" },
    el("div", { class: "row", style: "gap:8px;align-items:baseline" }, el("span", { class: "badge " + cls, text: label }),
      el("strong", { class: "small", text: fix.where || i.heading || "" }), el("span", { text: i.message })),
    i.fragment ? el("div", { class: "small muted mono issue-fragment", text: i.fragment }) : null,
    i.suggestion ? el("div", { class: "small", text: "→ " + i.suggestion }) : null,
    controls.length || open ? el("div", { class: "row", style: "gap:6px;margin-top:4px" }, ...controls, open) : null);
}

function issuesByPlace(p, issues, after) {
  const groups = new Map();
  for (const i of issues) {
    const key = (i.fix && i.fix.where) || i.heading || "Прочее";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(i);
  }
  return [...groups.entries()].map(([place, items]) => el("div", { class: "stack", style: "gap:6px" },
    el("h4", { text: `${place} (${items.length})` }), items.map((i) => issueRow(p, i, after))));
}

function msFix(p, rerender) {
  const box = el("div", { class: "card stack" }, el("p", { class: "hint", text: "Собираю замечания…" }));
  api(`/projects/${p.id}/manuscript/export/status`).then((st) => {
    box.replaceChildren(
      el("h3", { text: st.blocking.length ? `Что нужно исправить перед подачей (${st.blocking.length})` : "Блокирующих замечаний нет — рукопись готова к подаче" }),
      el("p", { class: "hint small", text: "Замечания сгруппированы по месту. Исправляйте прямо здесь: впишите недостающее, подтвердите число, примите цитату или поручите ИИ переписать фрагмент; «Открыть место» переходит к нужному разделу или шагу." }),
      ...issuesByPlace(p, st.blocking, rerender),
      st.warnings.length ? el("details", {}, el("summary", { text: `Предупреждения — не блокируют подачу (${st.warnings.length})` }),
        el("div", { class: "stack", style: "margin-top:8px" }, ...issuesByPlace(p, st.warnings, rerender))) : null);
  }).catch((e) => box.replaceChildren(el("div", { class: "banner warn", text: e.message })));
  return box;
}

// BSI transliteration of Russian names (mirrors backend/app/manuscript/titlepage.py)
const BSI = { а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "i", к: "k", л: "l", м: "m", н: "n",
  о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "kh", ц: "ts", ч: "ch", ш: "sh", щ: "shch", ъ: '"', ы: "y", ь: "'", э: "e", ю: "iu", я: "ia" };
function bsi(text) {
  return [...text].map((ch) => {
    const t = BSI[ch.toLowerCase()];
    if (t === undefined) return ch;
    return ch !== ch.toLowerCase() && t ? t[0].toUpperCase() + t.slice(1) : t;
  }).join("");
}

/* ---------- review and consistency (backend/app/manuscript/review.py) ---------- */

const REC = { accept: ["ok", "принять"], minor_revision: ["exploratory", "незначительная доработка"],
  major_revision: ["danger", "серьёзная доработка"], reject: ["danger", "отклонить"] };

function remarkRow(p, x, reload, kind, headings) {
  const quote = el("div", { class: "small muted mono issue-fragment", text: x.quote });
  const revise = el("button", { class: "small primary", text: "Исправить с помощью ИИ", disabled: !state.meta.llm.available || !x.section || x.addressed });
  revise.addEventListener("click", () => busy(revise, async () => {
    reload(await api(`/projects/${p.id}/manuscript/sections/${x.section}/revise`, { json: { instruction: x.suggestion, selection: x.quote } }));
    toast("Раздел переписан — проверьте новую версию");
  }));
  const mark = (status, text) => el("button", { class: "small", text, onclick: async (e) => busy(e.target, async () =>
    reload(await api(`/projects/${p.id}/manuscript/remarks/${x.id}`, { json: { status } }))) });
  const open = x.section ? el("button", { class: "small ghost", text: "Открыть раздел →", onclick: () => gotoFix(p, { step: "draft", tab: "sections", section: x.section }) }) : null;
  const done = x.status !== "open" && x.status !== undefined;
  return el("div", { class: "issue-row", style: done || x.addressed ? "opacity:.6" : "" },
    el("div", { class: "row", style: "gap:8px;align-items:baseline" },
      kind === "review" ? el("span", { class: "badge " + (x.severity === "major" ? "danger" : "exploratory"), text: x.severity === "major" ? "существенное" : "мелкое" }) : null,
      el("strong", { class: "small", text: `${x.id} · ${x.heading || headings[x.section] || ""}` }),
      x.addressed ? el("span", { class: "badge ok", text: "фрагмент изменён — вероятно, исправлено" }) : null,
      x.status === "resolved" ? el("span", { class: "badge ok", text: "решено" }) : x.status === "dismissed" ? el("span", { class: "badge", text: "не согласен" }) : null),
    el("div", { text: x.comment || x.problem }), quote,
    x.suggestion ? el("div", { class: "small", text: "→ " + x.suggestion }) : null,
    el("div", { class: "row", style: "gap:6px;margin-top:4px" }, revise,
      done ? mark("open", "Вернуть") : mark("resolved", "Решено"), done ? null : mark("dismissed", "Не согласен"), open));
}
function msReview(p, m, reload) {
  const headings = Object.fromEntries(m.sections.map((s) => [s.key, s.heading]));
  const llmOn = state.meta.llm.available;
  const cons = m.consistency;
  const runCons = el("button", { class: cons ? "" : "primary", text: cons ? "Проверить заново" : "Проверить согласованность", disabled: !llmOn });
  runCons.addEventListener("click", () => busy(runCons, async () => reload(await api(`/projects/${p.id}/manuscript/consistency`, { method: "POST" }))));
  const strict = el("select", {}, [["mentor", "наставник (мягко)"], ["standard", "типичный рецензент журнала"], ["strict", "строгий «Рецензент 2»"]]
    .map(([v, t]) => el("option", { value: v, text: t, selected: (m.review?.strictness || "standard") === v })));
  const rev = m.review;
  const runRev = el("button", { class: rev ? "" : "primary", text: rev ? "Рецензировать заново" : "Получить рецензию", disabled: !llmOn });
  runRev.addEventListener("click", () => busy(runRev, async () => reload(await api(`/projects/${p.id}/manuscript/review`, { json: { strictness: strict.value } }))));
  const consItems = (cons?.issues || []);
  const revComments = (rev?.comments || []);
  const openMajor = revComments.filter((c) => c.severity === "major" && c.status === "open" && !c.addressed).length;
  return el("div", { class: "stack" },
    el("div", { class: "card stack" }, el("h3", { text: "Проверка согласованности" }),
      el("p", { class: "hint small", text: "ИИ сверяет резюме с результатами, выводы — с уровнем доказательности находок, текст — с таблицами, методы — с результатами. Каждое замечание указывает дословный фрагмент текста; замечания попадают и в «Что исправить»." }),
      el("div", { class: "row" }, runCons, cons ? el("span", { class: "small muted", text: `${fmtDate(cons.at)} · ${cons.model}` }) : null),
      cons ? (consItems.length ? el("div", {}, consItems.map((x) => remarkRow(p, x, reload, "consistency", headings))) : el("div", { class: "banner ok small", text: "Противоречий не найдено." })) : null),
    el("div", { class: "card stack" }, el("h3", { text: "Рецензент журнала (ИИ)" }),
      el("p", { class: "hint small", text: `Имитация рецензии ${m.template?.journal ? "журнала «" + m.template.journal + "»" : "целевого журнала"}: соответствие профилю и типу статьи, новизна (с учётом найденных похожих публикаций), методы, обоснованность выводов, ${m.case_report ? "полнота по CARE" : "уровни доказательности"}, ограничения. Перед подачей — чтобы закрыть очевидные вопросы заранее.` }),
      el("div", { class: "row" }, el("label", { class: "row small" }, "Строгость: ", strict), runRev,
        rev ? el("span", { class: "small muted", text: `${fmtDate(rev.at)} · ${rev.model}` }) : null),
      rev ? el("div", { class: "stack" },
        el("div", { class: "row" }, el("span", { class: "badge " + REC[rev.recommendation][0], text: "Рекомендация: " + REC[rev.recommendation][1] }),
          openMajor ? el("span", { class: "small", text: `открытых существенных замечаний: ${openMajor}` }) : el("span", { class: "small", text: "существенные замечания закрыты" })),
        el("p", { text: rev.summary }),
        rev.strengths.length ? el("div", {}, el("strong", { class: "small", text: "Сильные стороны:" }), el("ul", { class: "small" }, rev.strengths.map((t) => el("li", { text: t })))) : null,
        el("div", {}, revComments.map((x) => remarkRow(p, x, reload, "review", headings)))) : null));
}

/* ---------- response to reviewers (backend/app/manuscript/revision.py) ---------- */

const POINT_KIND = { major: ["danger", "существенное"], minor: ["exploratory", "мелкое"], editorial: ["", "редакторское"], editor: ["accent", "редактор"] };

function msRevision(p, m, reload) {
  const llmOn = state.meta.llm.available;
  const rv = m.revision;
  const letter = el("textarea", { style: "min-height:160px", placeholder: "Вставьте письмо редакции с замечаниями рецензентов (или загрузите файл .docx/.pdf/.txt)" });
  const fileIn = el("input", { type: "file", accept: ".docx,.doc,.pdf,.txt,.rtf", class: "hidden" });
  const fileBtn = el("button", { class: "small", text: "Загрузить письмо из файла", onclick: () => fileIn.click() });
  fileIn.addEventListener("change", () => busy(fileBtn, async () => {
    const r = await sendFiles(`/projects/${p.id}/manuscript/revision/letter-text`, [fileIn.files[0]], { field: "file" });
    letter.value = r.text; toast("Текст письма загружен — проверьте и нажмите «Разобрать замечания»");
  }));
  const parse = el("button", { class: "primary", text: rv ? "Новый раунд: разобрать замечания" : "Разобрать замечания", disabled: !llmOn });
  parse.addEventListener("click", () => busy(parse, async () => {
    if (letter.value.trim().length < 20) { letter.focus(); return; }
    if (rv && !confirm("Начать новый раунд? Текущий текст станет исходным для отметки изменений.")) return;
    reload(await api(`/projects/${p.id}/manuscript/revision`, { json: { letter: letter.value } }));
  }));
  const start = el("div", { class: "card stack" }, el("h3", { text: rv ? `Раунд ${rv.n_rounds} от ${fmtDate(rv.created_at)}` : "Ответ рецензентам" }),
    el("p", { class: "hint small", text: "После получения решения редакции: ИИ разбивает письмо на отдельные замечания (каждое — дословно из письма), готовит ответ и правку текста по каждому; правка вносится в раздел новой версией. В конце — письмо «Ответ рецензентам» и рукопись с выделенными изменениями относительно поданной версии." }),
    letter, el("div", { class: "row" }, fileBtn, fileIn, parse));
  if (!rv) return el("div", { class: "stack" }, start);
  const pts = rv.points;
  const draftAll = el("button", { class: "small", text: "Подготовить ответы на все без ответа", disabled: !llmOn });
  draftAll.addEventListener("click", () => busy(draftAll, async () => {
    let last = null;
    for (const x of pts.filter((x) => !x.response)) last = await api(`/projects/${p.id}/manuscript/revision/points/${x.id}/draft`, { method: "POST" });
    if (last) reload(last);
  }));
  const exportBtn = el("a", { class: "btn primary", href: `/api/projects/${p.id}/manuscript/revision/export.zip`, text: "Скачать ответ рецензентам и рукопись с изменениями (.zip)" });
  const items = pts.map((x) => {
    const [cls, label] = POINT_KIND[x.kind] || POINT_KIND.minor;
    const resp = el("textarea", { style: "min-height:90px" }, x.response || "");
    const save = el("button", { class: "small", text: "Сохранить ответ" });
    save.addEventListener("click", () => busy(save, async () => reload(await api(`/projects/${p.id}/manuscript/revision/points/${x.id}`, { method: "PUT", json: { response: resp.value } }))));
    const draftBtn = el("button", { class: "small" + (x.response ? "" : " primary"), text: x.response ? "Переписать ответ (ИИ)" : "Подготовить ответ (ИИ)", disabled: !llmOn });
    draftBtn.addEventListener("click", () => busy(draftBtn, async () => reload(await api(`/projects/${p.id}/manuscript/revision/points/${x.id}/draft`, { method: "POST" }))));
    let change = null;
    if (x.change) {
      const instr = el("input", { value: x.change.instruction, style: "flex:1;min-width:260px" });
      const apply = el("button", { class: "small primary", text: x.applied?.length ? "Внести правку ещё раз" : "Внести правку в текст (ИИ)", disabled: !llmOn || !x.change.section });
      apply.addEventListener("click", () => busy(apply, async () => {
        if (instr.value !== x.change.instruction) await api(`/projects/${p.id}/manuscript/revision/points/${x.id}`, { method: "PUT", json: { instruction: instr.value } });
        reload(await api(`/projects/${p.id}/manuscript/revision/points/${x.id}/apply`, { method: "POST" }));
        toast("Правка внесена — новая версия раздела");
      }));
      change = el("div", { class: "stack", style: "gap:4px" },
        el("div", { class: "small" }, el("strong", { text: `Правка: раздел «${x.change.heading}»` }), x.applied?.length ? el("span", { class: "badge ok", style: "margin-left:6px", text: `внесена (v${x.applied.at(-1).version})` }) : null),
        x.change.quote ? el("div", { class: "small muted mono issue-fragment", text: x.change.quote }) : null,
        el("div", { class: "row", style: "gap:6px" }, instr, apply,
          x.change.section ? el("button", { class: "small ghost", text: "Открыть раздел →", onclick: () => gotoFix(p, { step: "draft", tab: "sections", section: x.change.section }) }) : null));
    }
    return el("div", { class: "card stack", style: "gap:6px" },
      el("div", { class: "row", style: "gap:8px;align-items:baseline" }, el("strong", { text: `${x.id} · ${x.reviewer}` }), el("span", { class: "badge " + cls, text: label }),
        x.verbatim ? null : el("span", { class: "badge", title: "текст замечания не совпал с письмом дословно — сверьте", text: "не дословно" }),
        x.status === "done" ? el("span", { class: "badge ok", text: "готово" }) : x.response ? el("span", { class: "badge accent", text: "есть ответ" }) : null),
      el("div", { style: "font-style:italic", text: x.text }),
      el("label", { class: "field" }, el("span", { text: m.lang === "ru" ? "Ответ" : "Response (English)" }), resp),
      el("div", { class: "row", style: "gap:6px" }, draftBtn, save), change);
  });
  return el("div", { class: "stack" }, start,
    el("div", { class: "card row between" }, el("span", { text: `Замечаний: ${pts.length} · с ответом: ${pts.filter((x) => x.response).length} · правок внесено: ${pts.filter((x) => x.applied?.length).length}` }),
      el("div", { class: "row" }, draftAll, exportBtn)),
    ...items);
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
  // authors and affiliations (title page; bilingual for Russian journals)
  inp.authors = inp.authors || [];
  inp.affiliations = inp.affiliations || [];
  const ruJ = m.lang === "ru";
  const authorsBox = el("div", { class: "stack", style: "gap:8px" });
  const cell = (obj, key, ph, opts = {}) => {
    const i = el("input", { value: Array.isArray(obj[key]) ? obj[key].join(", ") : (obj[key] || ""), placeholder: ph, style: opts.w ? `width:${opts.w}` : "" });
    i.addEventListener("change", () => { obj[key] = opts.list ? i.value.split(",").map((x) => opts.num ? parseInt(x, 10) : x.trim()).filter((x) => opts.num ? x > 0 : x) : i.value.trim(); });
    return i;
  };
  // my authors: a personal library reused across projects (backend/app/authors_api.py)
  let library = [];
  const normName = (s) => (s || "").trim().toLowerCase();
  const toLibrary = (a) => ({ name: a.name || "", name_en: a.name_en || "", position: a.position || "", email: a.email || "",
    orcid: a.orcid || "", roles: a.roles || [], affiliations: (a.affiliations || []).map((i) => inp.affiliations[i - 1]).filter(Boolean)
      .map((x) => ({ name: x.name || "", name_en: x.name_en || "", address: x.address || "" })) });
  const fromLibrary = (s) => {
    const idx = (s.affiliations || []).map((aff) => {
      let i = inp.affiliations.findIndex((x) => normName(x.name) === normName(aff.name));
      if (i < 0) { inp.affiliations.push({ ...aff }); i = inp.affiliations.length - 1; }
      return i + 1;
    });
    inp.authors.push({ name: s.name, name_en: s.name_en, position: s.position, email: s.email, orcid: s.orcid, roles: [...(s.roles || [])], affiliations: idx });
  };
  const saveToLibrary = async (authors) => {
    for (const a of authors.filter((x) => (x.name || "").trim())) library = await api("/authors", { json: toLibrary(a) });
    toast(authors.length > 1 ? "Авторы сохранены в «Мои авторы»" : "Автор сохранён в «Мои авторы»");
    drawAuthors();
  };
  api("/authors").then((l) => { library = l; drawAuthors(); }).catch(() => {});
  const drawAuthors = () => {
    const affRows = inp.affiliations.map((a, i) => el("tr", {}, el("td", { class: "mono", text: String(i + 1) }),
      el("td", {}, cell(a, "name", "ФГБУ «…», Москва")), ruJ ? el("td", {}, cell(a, "name_en", "Official English name")) : null,
      el("td", {}, cell(a, "address", "адрес, город, индекс, страна")),
      el("td", {}, el("button", { class: "small ghost", text: "✕", onclick: () => { inp.affiliations.splice(i, 1); drawAuthors(); } }))));
    const autRows = inp.authors.map((a, i) => {
      const corr = el("input", { type: "radio", name: "corr", checked: !!a.corresponding, onchange: () => { inp.authors.forEach((x) => { x.corresponding = x === a; }); } });
      const translit = ruJ ? el("button", { class: "small ghost", text: "латиницей", title: "транслитерация BSI", onclick: async () => {
        a.name_en = bsi(a.name || ""); drawAuthors(); } }) : null;
      const star = el("button", { class: "small ghost", text: "★", title: "сохранить в «Мои авторы» (со всеми данными и учреждениями)", onclick: () => saveToLibrary([a]) });
      return el("tr", {}, el("td", {}, el("div", { class: "row", style: "gap:4px;flex-wrap:nowrap" }, cell(a, "name", "Иванов Иван Иванович"), star)),
        ruJ ? el("td", {}, el("div", { class: "row", style: "gap:4px;flex-wrap:nowrap" }, cell(a, "name_en", "Ivanov II"), translit)) : null,
        el("td", {}, cell(a, "position", "должность")), el("td", {}, cell(a, "affiliations", "1, 2", { list: true, num: true, w: "60px" })),
        el("td", {}, cell(a, "email", "e-mail")), el("td", {}, cell(a, "orcid", "0000-0000-0000-0000", { w: "150px" })),
        el("td", {}, cell(a, "roles", "Conceptualization, Formal analysis", { list: true })), el("td", { style: "text-align:center" }, corr),
        el("td", {}, el("button", { class: "small ghost", text: "✕", onclick: () => { inp.authors.splice(i, 1); drawAuthors(); } })));
    });
    authorsBox.replaceChildren(
      el("h4", { text: "Учреждения" }),
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["№", "Название" + (ruJ ? " (рус.)" : ""), ruJ ? "Название (англ.)" : null, "Адрес", ""].filter((x) => x !== null).map((h) => el("th", { text: h })))),
        el("tbody", {}, affRows))),
      el("div", {}, el("button", { class: "small", text: "+ учреждение", onclick: () => { inp.affiliations.push({}); drawAuthors(); } })),
      el("h4", { text: "Авторы (в порядке на титульном листе)" }),
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["ФИО", ruJ ? "ФИО латиницей (BSI)" : null, "Должность", "Учр. №", "E-mail", "ORCID", "Роли CRediT", "Переписка", ""].filter((x) => x !== null).map((h) => el("th", { text: h })))),
        el("tbody", {}, autRows))),
      el("div", { class: "row" },
        el("button", { class: "small", text: "+ автор", onclick: () => { inp.authors.push({ roles: [] }); drawAuthors(); } }),
        library.length ? el("select", { onchange: (e) => {
          const s = library.find((x) => String(x.id) === e.target.value);
          if (s) { fromLibrary(s); drawAuthors(); toast(`${s.name} добавлен(а) вместе с учреждениями — не забудьте «Сохранить»`); }
        } }, el("option", { value: "", text: `+ из «Моих авторов» (${library.length})` }),
          library.filter((s) => !inp.authors.some((a) => (s.orcid && a.orcid === s.orcid) || normName(a.name) === normName(s.name)))
            .map((s) => el("option", { value: s.id, text: s.name + (s.orcid ? ` · ${s.orcid}` : "") + (s.position ? ` · ${s.position}` : "") }))) : null,
        inp.authors.length ? el("button", { class: "small ghost", text: "Сохранить всех в «Мои авторы»", onclick: () => saveToLibrary(inp.authors) }) : null),
      library.length ? el("details", {}, el("summary", { class: "small", text: `Мои авторы (${library.length}) — общая для всех ваших проектов библиотека` }),
        el("ul", { class: "small" }, library.map((s) => el("li", {}, `${s.name}${s.orcid ? " · " + s.orcid : ""}${s.affiliations?.length ? " · " + s.affiliations.map((x) => x.name).join("; ") : ""} `,
          el("button", { class: "small ghost", text: "удалить", onclick: async () => { library = await api(`/authors/${s.id}`, { method: "DELETE" }); drawAuthors(); } }))))) : null,
      el("p", { class: "hint small", text: ruJ ? "Журнал требует титульный блок на русском и английском, транслитерацию фамилий по BSI, ORCID подающего автора и сведения об авторах на отдельной странице — всё это собирается при экспорте автоматически." : "Номера учреждений ставятся надстрочными индексами у фамилий; автор для переписки указывается на титульном листе." }));
  };
  drawAuthors();
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
  const caseCard = m.case_report ? el("div", { class: "card stack" }, el("h3", { text: "Case report (CARE)" }),
    el("p", { class: "hint small", text: "CARE 13: для публикации описания пациента нужно его письменное информированное согласие — укажите его ниже в «Заявлениях». Если согласие получить нельзя, выберите «не требовалось» и укажите обоснование (например, разрешение этического комитета)." }),
    field("Обоснование отсутствия согласия (если согласия нет)", "consent.details", { textarea: true }),
    field("Мнение пациента (CARE 12, необязательно) — как пациент воспринял лечение и исход, своими словами", "patient_perspective", { textarea: true })) : null;
  return el("div", { class: "stack" }, caseCard,
    m.case_report ? null : el("div", { class: "card stack" }, el("h3", { text: "Материал и дизайн (для Materials and methods)" }),
      el("div", { class: "form-grid" }, field("Учреждение / архив", "study.institution"), field("Период", "study.period", { placeholder: "2016–2022" }),
        field("Пересмотр препаратов", "study.review", { placeholder: "all cases reviewed by two pathologists" })),
      field("Критерии отбора", "study.selection", { textarea: true }),
      field("Определение follow-up / исходов", "study.followup", { textarea: true })),
    el("div", { class: "card stack" }, el("h3", { text: "Иммуногистохимия (FR-5.3: клоны, разведения, платформы)" }),
      markerRows("ihc", "ihc", [["clone", "Клон"], ["vendor", "Производитель"], ["dilution", "Разведение"], ["platform", "Платформа"], ["retrieval", "Демаскировка"], ["scoring", "Оценка"]])),
    el("div", { class: "card stack" }, el("h3", { text: "Молекулярные методы" }),
      markerRows("molecular", "molecular", [["method", "Метод"], ["panel", "Панель / праймеры"], ["platform", "Платформа"], ["details", "Детали"]])),
    el("div", { class: "card stack" }, el("h3", { text: "Авторы, учреждения и заявления" }),
      authorsBox,
      el("div", { class: "form-grid" }, field("Этический комитет", "ethics.committee"), field("Номер одобрения", "ethics.approval_number"),
        field("Дата", "ethics.approval_date")),
      el("label", { class: "row small" }, waiver, "одобрение не требовалось (waiver)"),
      el("label", { class: "field" }, el("span", { text: "Информированное согласие" }), consent),
      field("Конфликт интересов", "coi", { textarea: true, placeholder: "The authors declare no competing interests." }),
      field("Финансирование", "funding", { textarea: true }),
      field("Доступность данных", "data_availability", { textarea: true }),
      field("Благодарности", "acknowledgements", { textarea: true }),
      field("Образец вашего стиля — абзац из вашей опубликованной статьи (необязательно; ИИ подстроится под регистр и ритм, содержание не копируется)", "style_sample", { textarea: true }),
      el("div", { class: "banner warn small", text: "ИИ не может быть указан автором; авторы несут ответственность за содержание (FR-7.2). Заявление об использовании ИИ формируется автоматически по фактическому журналу обращений к моделям." })),
    el("div", {}, save));
}

/* ---------- 3. plan ---------- */

function msPlan(p, m, reload) {
  const plan = m.plan ? JSON.parse(JSON.stringify(m.plan)) : null;
  const canPlan = m.case_report || m.n_accepted_findings;
  const gen = el("button", { class: plan ? "" : "primary", text: plan ? "Сгенерировать заново" : "Сгенерировать план", disabled: !state.meta.llm.available || !canPlan,
    title: !canPlan ? "примите находки на шаге «Анализ»" : state.meta.llm.available ? state.meta.llm.reasoning_model : state.meta.llm.reason });
  gen.addEventListener("click", () => busy(gen, async () => reload(await api(`/projects/${p.id}/manuscript/plan`, { method: "POST" }))));
  if (!plan) {
    return el("div", { class: "card stack" },
      el("p", { text: m.case_report
        ? `Case report (пациентов: ${m.n_cases}). План строится по данным случая, документам и руководству CARE: ключевые уроки случая, тезисы по разделам (сведения о пациенте, клинические данные, хронология, диагностика, лечение, исход, обсуждение), бюджет слов.`
        : `Принятых находок: ${m.n_accepted_findings}. План строится из них, подтверждённой новизны и профиля журнала: ключевые сообщения, тезисы по разделам, бюджет слов, таблицы и рисунки (FR-5.1).` }),
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

/* micrographs (backend/app/manuscript/micro.py) */
function microCard(p, m, reload) {
  const images = m.images || [];
  const ruJ = m.lang === "ru";
  const fileIn = el("input", { type: "file", multiple: true, accept: ".jpg,.jpeg,.png,.tif,.tiff", class: "hidden" });
  const status = el("span", { class: "small muted" });
  const upload = async (files) => {
    if (!files.length) return;
    status.textContent = "Загрузка…";
    try {
      const r = await sendFiles(`/projects/${p.id}/manuscript/images`, [...files], { onProgress: (n, i, t) => { status.textContent = `Передача «${n}»: ${Math.round(100 * i / t)}%`; } });
      if (r.errors.length) toast(r.errors.map((e) => `${e.file}: ${e.error}`).join("; "), "error");
      reload(await api(`/projects/${p.id}/manuscript`));
    } catch (e) { toast(e.message, "error"); status.textContent = ""; }
  };
  fileIn.addEventListener("change", () => upload(fileIn.files));
  const drop = el("div", { class: "dropzone", style: "padding:14px", onclick: () => fileIn.click(),
    ondragover: (e) => { e.preventDefault(); drop.classList.add("drag"); }, ondragleave: () => drop.classList.remove("drag"),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove("drag"); upload(e.dataTransfer.files); } },
    el("strong", { text: "Микрофотографии (JPEG, PNG, TIFF)" }),
    el("div", { class: "hint small", text: "перетащите или нажмите; метаданные снимков (камера, программа, даты, GPS) удаляются при загрузке" }), status);
  const selected = new Set();
  const save = async (im, patch) => reload(await api(`/projects/${p.id}/manuscript/images/${im.id}`, { method: "PUT", json: patch }));
  const field = (im, key, ph, w) => {
    const i = el("input", { value: im[key] ?? "", placeholder: ph, style: w ? `width:${w}` : "" });
    i.addEventListener("change", () => save(im, { [key]: key === "um_per_px" ? (parseFloat(i.value.replace(",", ".")) || null) : i.value }));
    return i;
  };
  const check = (im, key, label) => el("label", { class: "row small", style: "gap:4px" },
    el("input", { type: "checkbox", checked: !!im[key], onchange: (e) => save(im, { [key]: e.target.checked }) }), label);
  const cards = images.map((im) => el("div", { class: "card stack", style: "gap:6px;padding:10px;width:280px" },
    el("div", { class: "row", style: "gap:6px;align-items:flex-start;flex-wrap:nowrap" },
      el("input", { type: "checkbox", title: "в рисунок", onchange: (e) => { e.target.checked ? selected.add(im.id) : selected.delete(im.id); } }),
      el("img", { src: `/api/projects/${p.id}/manuscript/images/${im.id}.jpg?size=480`, alt: im.name, style: "width:230px;border-radius:6px" })),
    el("div", { class: "small mono", text: `${im.id} · ${im.width}×${im.height}` }),
    field(im, "description", ruJ ? "что видно (рус.)" : "what it shows"), ruJ ? field(im, "description_en", "what it shows (English)") : null,
    el("div", { class: "row", style: "gap:4px;flex-wrap:nowrap" }, field(im, "stain", "окраска: H&E, CD117…", "60%"), field(im, "magnification", "×200", "38%")),
    el("div", { class: "row", style: "gap:4px;flex-wrap:nowrap" }, field(im, "um_per_px", "мкм/пиксель", "50%"), el("span", { class: "hint small", text: "→ отрезок нарисуется" })),
    check(im, "has_scale_bar", "масштабный отрезок уже есть на снимке"),
    check(im, "phi_checked", "надписей с данными пациента нет (этикетка, подписи)"),
    el("button", { class: "small ghost", text: "Удалить", onclick: async () => { if (confirm("Удалить изображение?")) reload(await api(`/projects/${p.id}/manuscript/images/${im.id}`, { method: "DELETE" })); } })));
  const cols = el("select", {}, [1, 2, 3].map((n) => el("option", { value: n, text: `${n} в ряд`, selected: n === 2 })));
  const make = el("button", { class: "primary small", text: "Собрать рисунок из отмеченных", disabled: !images.length });
  make.addEventListener("click", () => busy(make, async () => {
    if (!selected.size) { toast("Отметьте изображения галочками"); return; }
    const order = images.map((i) => i.id).filter((id) => selected.has(id));
    reload(await api(`/projects/${p.id}/manuscript/figures/micro`, { json: { panels: order, columns: Number(cols.value) } }));
  }));
  const micros = m.figures.filter((f) => f.kind === "micro");
  const figRows = micros.map((f) => el("div", { class: "row", style: "border-bottom:1px solid var(--border);padding:6px 0;align-items:flex-start;flex-wrap:nowrap" },
    el("img", { src: `/api/projects/${p.id}/manuscript/figures/${f.id}.png?v=${Date.now()}`, style: "width:200px;border:1px solid var(--border)" }),
    el("div", { class: "grow small" }, el("div", { class: "mono", text: `{{FIG:${f.id}}} · панели: ${f.panels.join(", ")}` }), el("div", { text: f.caption_effective })),
    el("button", { class: "small ghost", text: "Удалить", onclick: async () => reload(await api(`/projects/${p.id}/manuscript/figures/micro/${f.id}`, { method: "DELETE" })) })));
  return el("div", { class: "card stack" }, el("h3", { text: "Микрофотографии" }),
    el("p", { class: "hint small", text: "Загрузите снимки, опишите каждый (что видно, окраска, увеличение), отметьте нужные и соберите рисунок: панели A, B, C… с буквами, масштабным отрезком (если указан размер пикселя) и подписью из ваших описаний. Рисунок экспортируется в формате и разрешении журнала; в тексте на него ссылаются как {{FIG:…}}." }),
    drop, images.length ? el("div", { class: "row", style: "gap:10px;align-items:flex-start" }, cards) : null,
    images.length ? el("div", { class: "row" }, cols, make) : null,
    micros.length ? el("div", {}, el("h4", { text: "Рисунки из микрофотографий" }), figRows) : null);
}

function msAssets(p, m, reload) {
  const tables = m.tables.map((t) => ({ ...t }));
  const figures = m.figures.map((f) => ({ ...f }));
  let figureLanguage = m.figure_language || "en";
  const row = (it, kind) => {
    const inc = el("input", { type: "checkbox", checked: it.include, onchange: (e) => { it.include = e.target.checked; } });
    const cap = el("input", { value: it.caption || "", placeholder: it.caption_effective, onchange: (e) => { it.caption = e.target.value; } });
    const capEn = m.lang === "ru" ? el("input", { value: it.caption_en || "", placeholder: it.caption_en_effective || "English caption",
      style: "margin-top:4px", onchange: (e) => { it.caption_en = e.target.value; } }) : null;
    return el("tr", {}, el("td", {}, inc), el("td", { class: "mono small", text: `{{${kind}:${it.id}}}` }),
      el("td", { class: "small", text: it.kind }), el("td", { style: "min-width:340px" }, cap, capEn),
      kind === "FIG" ? el("td", {}, el("a", { href: `/api/projects/${p.id}/manuscript/figures/${it.id}.png?lang=${m.figure_language || "en"}`, target: "_blank", text: "просмотр" })) : el("td", {}));
  };
  const save = el("button", { class: "primary", text: "Сохранить" });
  save.addEventListener("click", () => busy(save, async () => reload(await api(`/projects/${p.id}/manuscript/assets`, { method: "PUT", json: { tables, figures, figure_language: figureLanguage } }))));
  const tbl = (items, kind) => el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["В статье", "Ссылка в тексте", "Тип", m.lang === "ru" ? "Подпись RU / EN (пусто — по умолчанию)" : "Подпись (пусто — по умолчанию)", ""].map((h) => el("th", { text: h })))),
    el("tbody", {}, items.map((it) => row(it, kind)))));
  return el("div", { class: "stack" }, microCard(p, m, reload),
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
  // signs of AI prose (backend/app/manuscript/style.py): 0 — none found
  const sc = s.style_score;
  const styleBadge = sc == null || !s.versions.length ? null : el("span", { class: "badge " + (sc >= 40 ? "danger" : sc >= 15 ? "exploratory" : "ok"),
    title: "Сколько в тексте примет ИИ-стиля (слова-маркеры, шаблоны, одинаковый ритм) на 1000 слов; подробности — во вкладке «Проверки»",
    text: `ИИ-стиль: ${sc}` });
  const humanize = s.versions.length && s.kind !== "statements" ? el("button", { class: "small", text: "Убрать ИИ-стиль", disabled: !llmOn,
    title: "переписать найденные места академическим языком, не меняя фактов, чисел и ссылок" }) : null;
  if (humanize) humanize.addEventListener("click", () => busy(humanize, () => post("/humanize")));

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
    body = s.issues.length ? el("div", { class: "stack" }, s.issues.map((i) =>
      issueRow(p, i, async () => reload(await api(`/projects/${p.id}/manuscript`)))))
      : el("p", { class: "hint", text: "Замечаний нет." });
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
      el("div", { class: "row" }, styleBadge, humanize, gen, accept, reopen)),
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
  const ru = m.lang === "ru";  // a Russian article carries its title and keywords in English too
  const titleEn = el("input", { value: f.title_en || "", placeholder: "Title in English" });
  const kwEn = el("input", { value: (f.keywords_en || []).join("; ") });
  const save = el("button", { class: "primary", text: "Сохранить" });
  save.addEventListener("click", () => busy(save, async () => reload(await api(`/projects/${p.id}/manuscript/front`, { method: "PUT",
    json: { title: title.value, running_title: running.value, keywords: kw.value.split(";"), highlights: hl.value.split("\n"),
      ...(ru ? { title_en: titleEn.value, keywords_en: kwEn.value.split(";") } : {}) } }))));
  const candidatesEn = f.title_candidates_en || [];
  return el("div", { class: "card stack" },
    el("div", { class: "row" }, gen, el("span", { class: "hint", text: "Варианты строятся по принятым разделам (Title → 3–5 вариантов)." })),
    (f.title_candidates || []).length ? el("div", {}, el("h4", { text: "Варианты названия" }), f.title_candidates.map((t, i) =>
      el("div", { class: "row small", style: "flex-wrap:nowrap" }, el("button", { class: "small", text: "выбрать", onclick: () => {
        title.value = t; title.dispatchEvent(new Event("input"));
        if (ru && candidatesEn[i]) titleEn.value = candidatesEn[i];
      } }), t, ru && candidatesEn[i] ? el("span", { class: "muted", text: ` / ${candidatesEn[i]}` }) : null))) : null,
    el("label", { class: "field" }, el("span", { text: "Название" }), title, counter),
    ru ? el("label", { class: "field" }, el("span", { text: "Название на английском (Title)" }), titleEn) : null,
    el("label", { class: "field" }, el("span", { text: ru ? "Краткое название (running title)" : "Running title" }), running),
    el("label", { class: "field" }, el("span", { text: `Ключевые слова (через «;»)${m.template?.keywords?.max ? `, до ${m.template.keywords.max}` : ""}` }), kw),
    ru ? el("label", { class: "field" }, el("span", { text: "Keywords — ключевые слова на английском (через «;»)" }), kwEn) : null,
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
  root.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "card stack" }, el("h2", { text: "Экспорт (FR-5.13)" }),
      el("p", { class: "hint", text: "Пакет: manuscript.docx (Times New Roman 12, двойной интервал, нумерация строк и страниц), manuscript.md, рисунки в формате и разрешении журнала, supplementary (анонимизированная таблица случаев, скрипт анализа, журнал гипотез)." }),
      el("div", { class: "row" }, draftBtn, finalBtn),
      st.ready ? el("div", { class: "banner ok", text: "Блокирующих проблем нет — рукопись готова к подаче." })
        : el("div", { class: "banner warn", text: `Экспорт для подачи заблокирован: ${st.blocking.length}. Черновик доступен всегда — с пометкой DRAFT и списком открытых вопросов.` })),
    st.blocking.length ? el("div", { class: "card stack" }, el("h3", { text: `Что нужно решить (${st.blocking.length})` }),
      el("p", { class: "hint small", text: "Исправляйте прямо здесь или нажмите «Открыть место»." }),
      ...issuesByPlace(p, st.blocking, () => viewExport(root, p))) : null,
    st.warnings.length ? el("details", { class: "card" }, el("summary", { text: `Предупреждения (${st.warnings.length})` }),
      el("div", { class: "stack", style: "margin-top:8px" }, ...issuesByPlace(p, st.warnings, () => viewExport(root, p)))) : null));
}
