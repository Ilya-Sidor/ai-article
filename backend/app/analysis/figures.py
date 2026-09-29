"""Figures for findings (FR-2.8): rendered from the run's analysis_data.csv."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.cluster import hierarchy  # noqa: E402

import io  # noqa: E402

from ..storage import read_bytes, write_bytes  # noqa: E402
from . import sandbox_lib  # noqa: E402


def _data(run_dir, variables):
    return sandbox_lib.load_data(io.BytesIO(read_bytes(run_dir / "analysis_data.csv")), variables)


def _savefig(fig, path, **kw):
    buf = io.BytesIO()
    fig.savefig(buf, format=str(path).rsplit(".", 1)[-1].replace("tif", "tiff"), **kw)
    write_bytes(path, buf.getvalue())

INK = "#1f2933"
MUTED = "#8a94a6"
PALETTE = ["#3b6fb6", "#d98a2b", "#4f9d69", "#b0506a", "#7a65b0", "#4aa3a8"]
POSITIVE_LEVELS = {"positive", "detected", "yes"}

plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": INK,
                     "ytick.color": INK, "axes.spines.top": False, "axes.spines.right": False,
                     "font.family": "DejaVu Sans"})


_DPI = {"value": 150}
LANGUAGES = ("ru", "en")
# fixed words drawn on the figures; variable names and categories come from the data (ru) or the terminology (en)
_WORDS = {"ru": {"pct": "% случаев ({})"}, "en": {"pct": "% of cases ({})"}}
_LANG = {"value": "ru"}


def _save(fig, path):
    fig.tight_layout()
    _savefig(fig, path, dpi=_DPI["value"])
    plt.close(fig)


def _levels_present(series, levels):
    present = set(series.dropna())
    return [lvl for lvl in levels if lvl in present]


def bar_cat(df, a, b, levels_a, levels_b, path):
    sub = df[[a, b]].dropna()
    la, lb = _levels_present(sub[a], levels_a), _levels_present(sub[b], levels_b)
    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    bottom = np.zeros(len(la))
    for i, y in enumerate(lb):
        vals = np.array([((sub[a] == x) & (sub[b] == y)).sum() for x in la], dtype=float)
        tot = np.array([(sub[a] == x).sum() for x in la], dtype=float)
        share = np.divide(vals, tot, out=np.zeros_like(vals), where=tot > 0) * 100
        ax.bar(la, share, bottom=bottom, color=PALETTE[i % len(PALETTE)], label=str(y), width=0.6,
               edgecolor="white")
        for j, (s, v) in enumerate(zip(share, vals)):
            if v:
                ax.text(j, bottom[j] + s / 2, f"{int(v)}", ha="center", va="center", color="white", fontsize=8)
        bottom += share
    ax.set_ylabel(_WORDS[_LANG["value"]]["pct"].format(b))
    ax.set_xlabel(a)
    ax.set_ylim(0, 100)
    ax.legend(title=b, fontsize=8, title_fontsize=8, frameon=False, bbox_to_anchor=(1.02, 1), loc="upper left")
    _save(fig, path)


def box_group(df, group, value, levels_group, value_levels, path):
    sub = df[[group, value]].dropna()
    lg = _levels_present(sub[group], levels_group)
    data = [sub.loc[sub[group] == g, value].astype(float).values for g in lg]
    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    ax.boxplot(data, widths=0.5, showfliers=False, medianprops={"color": PALETTE[1], "linewidth": 2},
               boxprops={"color": MUTED}, whiskerprops={"color": MUTED}, capprops={"color": MUTED})
    rng = np.random.default_rng(0)
    for i, v in enumerate(data, 1):
        ax.scatter(i + rng.uniform(-0.12, 0.12, len(v)), v, s=16, color=PALETTE[0], alpha=0.8, zorder=3)
    ax.set_xticks(range(1, len(lg) + 1), [f"{g}\n(n={len(v)})" for g, v in zip(lg, data)])
    ax.set_xlabel(group)
    ax.set_ylabel(value)
    if value_levels:
        ax.set_yticks(range(len(value_levels)), value_levels)
    _save(fig, path)


def box_single(df, value, path):
    v = df[value].dropna().astype(float).values
    fig, ax = plt.subplots(figsize=(3.4, 3.2))
    ax.boxplot([v], widths=0.4, showfliers=False, medianprops={"color": PALETTE[1], "linewidth": 2})
    rng = np.random.default_rng(0)
    ax.scatter(1 + rng.uniform(-0.1, 0.1, len(v)), v, s=16, color=PALETTE[0], alpha=0.8, zorder=3)
    ax.set_xticks([1], [value])
    _save(fig, path)


def scatter(df, a, b, levels, path):
    sub = df[[a, b]].dropna().astype(float)
    fig, ax = plt.subplots(figsize=(4.2, 3.4))
    rng = np.random.default_rng(0)
    jit = lambda col: rng.uniform(-0.08, 0.08, len(sub)) if col in levels else 0  # noqa: E731
    ax.scatter(sub[a] + jit(a), sub[b] + jit(b), s=20, color=PALETTE[0], alpha=0.85)
    ax.set_xlabel(a)
    ax.set_ylabel(b)
    for col, setter in ((a, ax.set_xticks), (b, ax.set_yticks)):
        if col in levels:
            setter(range(len(levels[col])), levels[col])
    _save(fig, path)


def _encode(df, cols, variables):
    mat = np.full((len(df), len(cols)), np.nan)
    for j, c in enumerate(cols):
        spec = variables[c]
        s = df[c]
        if spec["vtype"] in ("quantitative", "ordinal"):
            v = s.astype(float)
            lo, hi = v.min(), v.max()
            mat[:, j] = (v - lo) / (hi - lo) if hi > lo else 0.5
        else:
            levels = spec.get("levels") or sorted(s.dropna().unique())
            idx = {lvl: i for i, lvl in enumerate(levels)}
            denom = max(len(levels) - 1, 1)
            mat[:, j] = [idx[x] / denom if x in idx else np.nan for x in s]
    return mat


def heatmap(df, clustering, variables, path):
    cols = clustering["variables"]
    order = clustering["order"]
    z = np.array(clustering["linkage"], dtype=float)
    mat = _encode(df, cols, variables)[order]
    h = max(3.0, 0.22 * len(df) + 1.2)
    w = max(5.0, 0.32 * len(cols) + 2.6)
    fig = plt.figure(figsize=(w, h))
    ax_d = fig.add_axes([0.02, 0.22, 0.14, 0.7])
    hierarchy.dendrogram(z, orientation="left", ax=ax_d, no_labels=True, color_threshold=0,
                         above_threshold_color=MUTED, link_color_func=lambda k: MUTED)
    ax_d.invert_yaxis()
    ax_d.axis("off")
    ax = fig.add_axes([0.17, 0.22, 0.66, 0.7])
    cmap = matplotlib.colormaps["Blues"].with_extremes(bad="#eeeeee")
    ax.imshow(np.ma.masked_invalid(mat), aspect="auto", cmap=cmap, vmin=0, vmax=1, interpolation="nearest")
    ax.set_yticks(range(len(order)), [clustering["case_ids"][i] for i in order], fontsize=7)
    ax.yaxis.tick_right()
    ax.set_xticks(range(len(cols)), cols, rotation=60, ha="right", fontsize=7)
    for s in ax.spines.values():
        s.set_visible(False)
    _savefig(fig, path, dpi=_DPI["value"], bbox_inches="tight")
    plt.close(fig)


def oncoprint(df, cols, variables, path):
    """Binary IHC / molecular profile per case (FR-2.5)."""
    if not cols:
        return False
    order = sorted(range(len(df)), key=lambda i: tuple(
        0 if df[c].iloc[i] in POSITIVE_LEVELS or df[c].iloc[i] == (variables[c]["levels"] or [None, None])[-1] else 1
        for c in cols))
    fig, ax = plt.subplots(figsize=(max(5, 0.22 * len(df) + 2), 0.35 * len(cols) + 1.2))
    for yi, c in enumerate(cols):
        pos_level = variables[c]["levels"][-1]
        for xi, i in enumerate(order):
            v = df[c].iloc[i]
            color = "#eeeeee" if v != v or v is None else (PALETTE[3] if v == pos_level else "#d5dbe3")
            ax.add_patch(plt.Rectangle((xi, yi), 0.9, 0.8, color=color))
    ax.set_xlim(0, len(order))
    ax.set_ylim(len(cols), -0.2)
    ax.set_yticks([y + 0.4 for y in range(len(cols))], cols)
    ax.set_xticks([x + 0.45 for x in range(len(order))], [df["case_id"].iloc[i] for i in order], rotation=90,
                  fontsize=6)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    _save(fig, path)
    return True


_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def latin(text):
    """Last resort for an English figure: a label with no English term is transliterated, never left in
    Cyrillic (the manuscript checks list such terms until the author translates them)."""
    if not isinstance(text, str):
        return text
    out = []
    for ch in text:
        t = _TRANSLIT.get(ch.lower())
        out.append(ch if t is None else (t.capitalize() if ch.isupper() else t))
    return "".join(out)


def _translate(df, spec, finding, terms):
    """English labels for export: rename columns and category values (ordinal too), adapt the finding result."""
    import copy
    names = {k: latin((terms.get(k) or {}).get("en") or k) for k in spec["variables"]}
    lv = {k: (terms.get(k) or {}).get("levels") or {} for k in spec["variables"]}

    def level(col, x):
        return latin(lv[col].get(x, x)) if isinstance(x, str) else x
    for col, spec_v in spec["variables"].items():
        if col in df.columns and spec_v["vtype"] in ("binary", "categorical"):
            df[col] = df[col].map(lambda x, c=col: level(c, x))
    df = df.rename(columns=names)
    spec = copy.deepcopy(spec)
    spec["variables"] = {names[k]: dict(v, levels=[level(k, x) for x in v["levels"]] if v.get("levels") else v.get("levels"))
                         for k, v in spec["variables"].items()}
    if finding and finding.get("result"):
        finding = copy.deepcopy(finding)
        r = finding["result"]
        a, b = r["a"], r["b"]
        if "levels_a" in r:
            r["levels_a"] = [level(a, x) for x in r["levels_a"]]
            r["levels_b"] = [level(b, x) for x in r["levels_b"]]
        for g in r.get("groups", []):
            g["level"] = level(a, g["level"])
        r["a"], r["b"] = names[a], names[b]
        if (finding.get("figure_args") or {}).get("variable") in names:
            finding["figure_args"] = dict(finding["figure_args"], variable=names[finding["figure_args"]["variable"]])
    return df, spec, finding


def render_finding(run_dir, finding, spec, out_path=None, dpi=150, terms=None, lang="ru"):
    """Render (and cache) the figure for a finding; returns the file path or None.

    ``lang``: labels in Russian (variable names and categories as in the data) or English (from ``terms``,
    the manuscript terminology). With ``out_path`` the figure is written there at ``dpi`` (journal export)."""
    fig_dir = run_dir / "figures"
    fig_dir.mkdir(exist_ok=True)
    path = out_path or fig_dir / f"{finding['id']}.{lang}.png"
    if path.exists() and out_path is None:
        return path
    _DPI["value"], _LANG["value"] = dpi, lang
    try:
        return _render_finding(run_dir, finding, spec, path, (terms or {}) if lang == "en" else None)
    finally:
        _DPI["value"], _LANG["value"] = 150, "ru"


def _render_finding(run_dir, finding, spec, path, terms=None):
    df = _data(run_dir, spec["variables"])
    if terms is not None:
        df, spec, finding = _translate(df, spec, finding, terms)
    variables = spec["variables"]
    ordinal_levels = {k: v["levels"] for k, v in variables.items() if v["vtype"] == "ordinal"}
    kind, r = finding.get("figure"), finding.get("result")
    if kind == "bar":
        bar_cat(df, r["a"], r["b"], r["levels_a"], r["levels_b"], path)
    elif kind == "box":
        box_group(df, r["a"], r["b"], [g["level"] for g in r["groups"]], ordinal_levels.get(r["b"]), path)
    elif kind == "scatter":
        scatter(df, r["a"], r["b"], ordinal_levels, path)
    elif kind == "box_single":
        box_single(df, finding["figure_args"]["variable"], path)
    else:
        return None
    return path


def render_overview(run_dir, name, spec, clustering, out_path=None, dpi=150, terms=None, lang="ru"):
    fig_dir = run_dir / "figures"
    fig_dir.mkdir(exist_ok=True)
    path = out_path or fig_dir / f"{name}.{lang}.png"
    if path.exists() and out_path is None:
        return path
    _DPI["value"], _LANG["value"] = dpi, lang
    try:
        return _render_overview(run_dir, name, spec, clustering, path, (terms or {}) if lang == "en" else None)
    finally:
        _DPI["value"], _LANG["value"] = 150, "ru"


def _render_overview(run_dir, name, spec, clustering, path, terms=None):
    df = _data(run_dir, spec["variables"])
    raw = _data(run_dir, {})
    orig_spec = spec
    if terms is not None:
        df, spec, _ = _translate(df, orig_spec, None, terms)
        raw, _, _ = _translate(raw, orig_spec, None, terms)
        names = {k: latin((terms.get(k) or {}).get("en") or k) for k in orig_spec["variables"]}
        if clustering:
            clustering = dict(clustering, variables=[names.get(c, c) for c in clustering["variables"]])
    variables = spec["variables"]
    if name == "heatmap":
        if not clustering or clustering.get("status") != "ok":
            return None
        heatmap(df, clustering, variables, path)
        return path
    if name == "oncoprint":
        cols = [c for c, v in variables.items() if v["vtype"] == "binary"
                and set(v["levels"]) & POSITIVE_LEVELS]
        return path if oncoprint(raw, cols, variables, path) else None
    return None
