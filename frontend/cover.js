"use strict";
/* Module 6 — cover letter. Uses helpers from app.js/manuscript.js: el, api, toast, busy, fmtDate, state, renderSegments, SEVERITY. */

async function viewCover(root, p) {
  renderCover(root, p, await api(`/projects/${p.id}/cover`));
}

function renderCover(root, p, v) {
  const reload = (fresh) => renderCover(root, p, fresh);
  const s = { ...v.settings };
  const field = (label, key, opts = {}) => {
    const node = opts.textarea ? el("textarea", { style: "min-height:64px", placeholder: opts.placeholder || "" }, s[key] || "")
      : el("input", { value: s[key] || "", placeholder: opts.placeholder || "" });
    node.addEventListener("change", () => { s[key] = node.value; });
    return el("label", { class: "field" }, el("span", { text: label }), node);
  };
  const tone = el("select", { onchange: (e) => { s.tone = e.target.value; } },
    Object.entries({ formal: "формальный", personal: "более персональный" }).map(([k, l]) => el("option", { value: k, selected: s.tone === k, text: l })));
  const saveSettings = el("button", { class: "primary small", text: "Сохранить настройки" });
  saveSettings.addEventListener("click", () => busy(saveSettings, async () => { reload(await api(`/projects/${p.id}/cover/settings`, { method: "PUT", json: s })); toast("Сохранено"); }));

  const settingsCard = el("div", { class: "card stack" },
    el("h3", { text: "Адресат, тон, подпись (FR-6.1, 6.4)" }),
    el("div", { class: "form-grid" }, field("Главный редактор (из профиля, можно изменить)", "editor"),
      field("Обращение (необязательно)", "salutation", { placeholder: "Dear Professor Smith," }),
      el("label", { class: "field" }, el("span", { text: "Тон" }, ), tone)),
    el("div", { class: "form-grid" }, field("Автор для переписки", "corresponding_name"), field("Учреждение", "corresponding_affiliation"),
      field("E-mail", "corresponding_email")),
    el("h3", { text: "Рецензенты (FR-6.5)" + (v.suggested_reviewers_requirement === "required" ? " — журнал требует" : "") }),
    el("div", { class: "form-grid" },
      field("Предлагаемые рецензенты (по строке: ФИО, учреждение, e-mail, обоснование)", "suggested_reviewers", { textarea: true }),
      field("Исключить из рецензирования", "excluded_reviewers", { textarea: true })),
    field("Переподача / перевод из другого журнала (необязательно)", "previous_submission",
      { textarea: true, placeholder: "e.g. transferred from Modern Pathology with reviewers' comments" }),
    el("p", { class: "hint small", text: "Подпись и списки рецензентов добавляются в письмо программно и не передаются в языковую модель." }),
    el("div", {}, saveSettings));

  if (!v.has_journal || v.prerequisites.length) {
    root.replaceChildren(el("div", { class: "stack" },
      el("div", { class: "banner warn" }, el("strong", { text: "Cover letter пишется после финализации статьи (FR-6.1)." }),
        el("ul", {}, [...(!v.has_journal ? ["выберите целевой журнал на шаге «Проект»"] : []), ...v.prerequisites].map((x) => el("li", { text: x })))),
      settingsCard));
    return;
  }

  const llmOn = state.meta.llm.available;
  const gen = el("button", { class: v.versions.length ? "" : "primary", text: v.versions.length ? "Написать заново" : "Написать cover letter",
    disabled: !llmOn, title: llmOn ? state.meta.llm.reasoning_model : state.meta.llm.reason });
  gen.addEventListener("click", () => busy(gen, async () => reload(await api(`/projects/${p.id}/cover/generate`, { method: "POST" }))));
  const accept = el("button", { class: v.status === "accepted" ? "active-ok" : "ok", text: v.status === "accepted" ? "✓ Принят" : "Принять письмо",
    disabled: !v.versions.length || v.status === "accepted" });
  accept.addEventListener("click", () => busy(accept, async () => reload(await api(`/projects/${p.id}/cover/accept`, { method: "POST" }))));
  const reopen = v.status === "accepted" ? el("button", { class: "small ghost", text: "вернуть в работу",
    onclick: async () => reload(await api(`/projects/${p.id}/cover/reopen`, { method: "POST" })) }) : null;
  const dl = v.versions.length ? el("a", { class: "btn small", href: `/api/projects/${p.id}/cover/letter.docx?draft=${v.status !== "accepted"}`, text: "Скачать .docx" }) : null;

  // requirements checklist (FR-6.3)
  const cov = Object.fromEntries((v.coverage || []).map((c) => [c.requirement, c]));
  const reqCard = v.requirements.length ? el("div", { class: "card stack" }, el("h3", { text: `Требования ${v.journal} к cover letter (FR-6.3)` }),
    v.requirements.map((r) => {
      const c = cov[r];
      return el("label", { class: "row", style: "align-items:flex-start;flex-wrap:nowrap" },
        el("input", { type: "checkbox", checked: !!v.confirmed[r], onchange: async (e) => reload(await api(`/projects/${p.id}/cover/confirm`, { json: { requirement: r, done: e.target.checked } })) }),
        el("div", {}, el("div", { text: r }),
          c ? el("div", { class: "small muted", text: (c.addressed ? "агент: учтено — «" + c.where + "»" : "агент: не учтено") }) : null));
    }), el("p", { class: "hint small", text: "Отметьте каждое требование после проверки текста." })) : null;

  if (!state.coverView) state.coverView = "preview";
  const views = [["preview", "Письмо"], ["edit", "Редактор"], ["issues", `Проверки (${v.issues.length})`], ["versions", `Версии (${v.versions.length})`]];
  const vbar = el("div", { class: "tabs" }, views.map(([id, label]) => el("button", { class: "tab" + (state.coverView === id ? " active" : ""), text: label,
    onclick: () => { state.coverView = id; reload(v); } })));
  const textarea = el("textarea", { style: "min-height:360px;font-family:var(--mono);font-size:12.5px;line-height:1.6" }, v.body);
  let body;
  if (!v.versions.length) {
    body = el("p", { class: "hint", text: "Письмо ещё не написано." });
  } else if (state.coverView === "edit") {
    const save = el("button", { class: "primary", text: "Сохранить версию" });
    save.addEventListener("click", () => busy(save, async () => reload(await api(`/projects/${p.id}/cover/body`, { method: "PUT", json: { body: textarea.value } }))));
    body = el("div", { class: "stack" }, el("p", { class: "hint small", text: "Здесь только тело письма; обращение, рецензенты и подпись добавляются автоматически. Числа — ссылками {{…}}." }), textarea, el("div", {}, save));
  } else if (state.coverView === "issues") {
    body = v.issues.length ? el("div", { class: "stack" }, v.issues.map((i) => {
      const [cls, label] = SEVERITY[i.severity];
      return el("div", { class: "row", style: "align-items:flex-start;flex-wrap:nowrap;border-bottom:1px solid var(--border);padding:6px 0" },
        el("span", { class: "badge " + cls, text: label }),
        el("div", { class: "grow" }, el("div", { text: i.message }), i.fragment ? el("div", { class: "small muted", text: i.fragment }) : null,
          i.suggestion ? el("div", { class: "small", text: "→ " + i.suggestion }) : null));
    })) : el("p", { class: "hint", text: "Замечаний нет." });
  } else if (state.coverView === "versions") {
    const out = el("pre", { class: "small", style: "white-space:pre-wrap;max-height:300px;overflow:auto" });
    body = el("div", { class: "stack" }, el("div", { class: "table-wrap", style: "max-height:260px" }, el("table", {}, el("tbody", {},
      [...v.versions].reverse().map((ver) => el("tr", {}, el("td", { class: "mono", text: "v" + ver.n }), el("td", { class: "small", text: fmtDate(ver.ts) }),
        el("td", { text: { agent: "агент", author: "автор" }[ver.actor] || ver.actor }), el("td", { class: "small", text: ver.note }),
        el("td", {}, ver.n > 1 ? el("button", { class: "small ghost", text: "diff", onclick: async () => {
          const d = await api(`/projects/${p.id}/cover/diff?a=${ver.n - 1}&b=${ver.n}`);
          out.replaceChildren(...d.diff.map((l) => el("div", { style: l.startsWith("+") ? "color:var(--ok)" : l.startsWith("-") ? "color:var(--danger)" : null, text: l })));
        } }) : null, ver.n !== v.versions.length ? el("button", { class: "small ghost", text: "откатить",
          onclick: async () => reload(await api(`/projects/${p.id}/cover/rollback`, { json: { n: ver.n } })) }) : null)))))), out);
  } else {
    body = el("div", { class: "manuscript", style: "max-width:720px" }, v.letter.map((b) =>
      b.kind === "li" ? el("div", { style: "margin-left:18px", text: "• " + b.text })
        : el("p", { style: "white-space:pre-line" + (b.text.includes("[уточнить") ? ";color:var(--danger)" : ""), text: b.text })));
  }
  const instr = el("input", { placeholder: "Инструкция агенту: «подчеркни соответствие scope журнала», «короче»… (выделите фрагмент — применится к нему)" });
  const apply = el("button", { class: "small primary", text: "Применить", disabled: !llmOn || !v.versions.length });
  apply.addEventListener("click", () => busy(apply, async () => {
    if (!instr.value.trim()) return;
    const sel = state.coverView === "edit" ? textarea.value.slice(textarea.selectionStart, textarea.selectionEnd) : String(window.getSelection() || "");
    reload(await api(`/projects/${p.id}/cover/revise`, { json: { instruction: instr.value.trim(), selection: sel.trim() || null } }));
  }));

  root.replaceChildren(el("div", { class: "stack" },
    v.sections_not_accepted.length ? el("div", { class: "banner warn small", text: "Не все разделы статьи приняты: " + v.sections_not_accepted.join(", ") + ". Письмо должно соответствовать итоговому тексту." }) : null,
    el("div", { class: "card stack" },
      el("div", { class: "row between" }, el("h2", { text: `Cover letter — ${v.journal}` }),
        el("div", { class: "row" }, gen, accept, reopen, dl, el("span", { class: "badge " + (v.blocking ? "danger" : "ok"), text: v.blocking ? `блокирующих: ${v.blocking}` : "готово" }))),
      v.questions.length ? el("div", { class: "banner info small" }, el("strong", { text: "Агент просит уточнить:" }), el("ul", {}, v.questions.map((q) => el("li", { text: q })))) : null,
      vbar, body,
      el("div", { class: "row", style: "flex-wrap:nowrap" }, instr, apply)),
    reqCard, settingsCard));
}
