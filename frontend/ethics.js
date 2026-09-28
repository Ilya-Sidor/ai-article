"use strict";
/* Module 7 — ethics and transparency; hosts step 8 tabs. Uses helpers from app.js/manuscript.js. */

async function viewChecksHub(root, p) {
  if (!state.checksTab) state.checksTab = p.journal_id ? "journal" : "ethics";
  const bar = el("div", { class: "tabs" }, [["journal", "Чек-лист журнала"], ["ethics", "Этика и прозрачность"]].map(([id, label]) =>
    el("button", { class: "tab" + (state.checksTab === id ? " active" : ""), text: label, onclick: () => { state.checksTab = id; viewChecksHub(root, p); } })));
  const body = el("div", {}, el("p", { class: "hint", text: "Загрузка…" }));
  root.replaceChildren(bar, body);
  if (state.checksTab === "journal") await viewChecks(body, p);
  else await viewEthics(body, p);
}

const REMINDER = { ok: ["ok", "✓"], missing: ["danger", "✕"], attention: ["exploratory", "!"], na: ["", "—"] };

function issueList(items, empty) {
  if (!items.length) return el("p", { class: "hint", text: empty });
  return el("div", { class: "stack", style: "gap:0" }, items.map((i) => {
    const [cls, label] = SEVERITY[i.severity];
    return el("div", { class: "row", style: "align-items:flex-start;flex-wrap:nowrap;border-bottom:1px solid var(--border);padding:6px 0" },
      el("span", { class: "badge " + cls, text: label }),
      el("div", { class: "grow" }, el("div", {}, i.heading ? el("strong", { text: i.heading + ": " }) : null, i.message),
        i.fragment ? el("div", { class: "small muted", text: "«" + i.fragment + "»" }) : null,
        i.suggestion ? el("div", { class: "small", text: "→ " + i.suggestion }) : null));
  }));
}

async function viewEthics(root, p) {
  const e = await api(`/projects/${p.id}/ethics`);
  const reload = () => viewEthics(root, p);

  // FR-7.1 / 7.2: AI use
  const usage = e.ai_usage;
  const aiCard = el("div", { class: "card stack" },
    el("h2", { text: "Использование ИИ (FR-7.1, 7.2)" }),
    el("p", { class: "hint", text: "Журнал фактических обращений проекта к языковым моделям (без содержимого запросов). Из него автоматически собирается заявление об использовании ИИ." }),
    usage.summary.length ? el("div", { class: "table-wrap", style: "max-height:none" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Задача", "Вызовов", "Модели", "Объём, симв."].map((h) => el("th", { text: h })))),
      el("tbody", {}, usage.summary.map((s) => el("tr", {}, el("td", { text: s.task }), el("td", { class: "num", text: s.calls }),
        el("td", { class: "small mono", text: s.models.join(", ") }), el("td", { class: "num", text: s.chars })))))) :
      el("p", { class: "hint", text: "В этом проекте языковые модели ещё не вызывались." }),
    el("h4", { text: "Заявление (войдёт в раздел Statements)" }),
    el("blockquote", { style: "margin:0;padding:8px 12px;border-left:3px solid var(--accent);background:var(--surface-2)", text: e.ai_statement }),
    el("div", { class: "small" }, "Требование журнала: ", el("strong", { text: e.ai_requirement ? { required: "обязательно", recommended: "рекомендуется", optional: "по желанию", not_accepted: "не принимается" }[e.ai_requirement] : "не указано в профиле" }),
      e.ai_policy ? el("div", { class: "muted", style: "margin-top:4px", text: "Политика: " + e.ai_policy }) : null),
    el("div", { class: "banner warn small", text: "ИИ не может быть указан автором; авторы несут ответственность за всё содержание рукописи." }),
    issueList(e.authorship, "Среди авторов нет ИИ-инструментов."));

  // FR-7.3
  const remCard = el("div", { class: "card stack" }, el("h2", { text: "Этические требования (FR-7.3)" }),
    e.reminders.map((r) => {
      const [cls, mark] = REMINDER[r.status];
      return el("div", { class: "row", style: "align-items:flex-start;flex-wrap:nowrap" }, el("span", { class: "badge " + cls, style: "min-width:26px;justify-content:center", text: mark }),
        el("div", {}, el("div", { text: r.text }), el("div", { class: "small muted", text: r.detail })));
    }),
    el("p", { class: "hint small" }, "Данные для заявлений вводятся на шаге 7 → ", el("a", { href: `#/p/${p.id}/draft`, text: "«Данные автора»" }), "."));

  // FR-7.4 data
  let dataCard = null;
  const d = e.data_risk;
  if (d) {
    const s = { ...d.settings };
    const bands = el("input", { type: "checkbox", checked: s.age_bands, onchange: (ev) => { s.age_bands = ev.target.checked; } });
    const width = el("select", { style: "width:auto", onchange: (ev) => { s.age_band_width = parseInt(ev.target.value, 10); } },
      [5, 10, 20].map((w) => el("option", { value: w, selected: s.age_band_width === w, text: `${w} лет` })));
    const text = el("input", { type: "checkbox", checked: s.exclude_text, onchange: (ev) => { s.exclude_text = ev.target.checked; } });
    const drops = d.qi_columns.map((q) => el("label", { class: "row small" },
      el("input", { type: "checkbox", checked: s.drop_columns.includes(q.name), onchange: (ev) => {
        s.drop_columns = ev.target.checked ? [...s.drop_columns, q.name] : s.drop_columns.filter((c) => c !== q.name); } }),
      `исключить «${q.name}»`));
    const apply = el("button", { class: "primary small", text: "Применить к supplementary" });
    apply.addEventListener("click", () => busy(apply, async () => { await api(`/projects/${p.id}/ethics/settings`, { method: "PUT", json: s }); reload(); }));
    const kline = (label, k) => el("div", { class: "row small" }, el("strong", { text: label }),
      el("span", { class: "badge " + (k.unique_cases?.length ? "danger" : "ok"), text: k.k_min == null ? "нет квазиидентификаторов" : `k-min = ${k.k_min}; уникальных случаев: ${k.unique_cases.length} из ${k.n}` }));
    dataCard = el("div", { class: "card stack" }, el("h2", { text: "Реидентификация: таблица случаев в supplementary (FR-7.4)" }),
      el("p", { class: "hint", text: "Квазиидентификаторы: " + (d.qi_columns.map((q) => q.name).join(", ") || "не найдены") + ". k — сколько случаев неразличимы по их сочетанию; k = 1 означает, что случай уникален и его легче узнать (редкий диагноз + точный возраст + локализация)." }),
      kline("Без обобщений:", d.before), kline("В экспорте:", d.after),
      el("div", { class: "row small" }, bands, "возраст диапазонами по", width),
      el("label", { class: "row small" }, text, "не выгружать свободный текст заключений"), drops,
      el("div", {}, apply),
      el("details", {}, el("summary", { text: "Столбцы, которые будут выгружены" }), el("p", { class: "small", text: d.exported_columns.join(", ") })));
  }

  // FR-7.4 text, FR-7.5
  const statRows = Object.entries(e.overlap_stats || {});
  const textCard = el("div", { class: "card stack" },
    el("h2", { text: "Реидентификация в тексте (FR-7.4)" }),
    el("p", { class: "hint", text: "Предложения, где сходятся несколько квазиидентификаторов пациента: точный возраст, пол, дата, место, конкретный вариант, профессия." }),
    issueList(e.text_risks, "Рискованных сочетаний в тексте не найдено."));
  const overlapCard = el("div", { class: "card stack" },
    el("h2", { text: "Текстовые заимствования (FR-7.5)" }),
    el("p", { class: "hint", text: "Дословные совпадения текста рукописи и cover letter с фрагментами загруженных источников (от 12 слов подряд — предупреждение, от 25 — блокирует экспорт). Текст в кавычках не учитывается." }),
    statRows.length ? el("div", { class: "row small" }, statRows.map(([k, v]) => el("span", { class: "badge " + (v.percent > 15 ? "danger" : v.percent > 5 ? "exploratory" : ""), text: `${k}: ${v.percent}% (${v.matched}/${v.words} слов)` }))) : el("p", { class: "hint small", text: "Нет текста или база литературы пуста." }),
    issueList(e.overlap, "Заметных совпадений нет."));

  root.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "banner " + (e.blocking ? "warn" : "ok"), text: e.blocking ? `Блокирующих проблем этики: ${e.blocking} — экспорт для подачи запрещён` : "Блокирующих проблем этики нет." }),
    aiCard, remCard, dataCard, textCard, overlapCard));
}
