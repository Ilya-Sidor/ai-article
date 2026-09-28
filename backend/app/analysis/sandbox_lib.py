"""Statistical routines executed inside the analysis sandbox.

This module is embedded verbatim into every generated ``analysis.py`` script
(FR-2.10), so it must depend only on numpy / pandas / scipy, must not import
anything from the application and must stay deterministic: every resampling
procedure receives an explicit seed.

All numbers shown to the author are produced here. The LLM never computes
statistics (PRD 1.3 "Числа считает код, а не LLM").
"""
import json
import math

import numpy as np
import pandas as pd
from scipy import stats
from scipy.cluster import hierarchy
from scipy.spatial.distance import squareform

N_BOOT = 2000
MC_RESAMPLES = 20000
Z95 = 1.959963984540054


def _f(x, nd=6):
    """JSON-safe rounded float (None for NaN / inf)."""
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    return round(x, nd)


def _skip(reason, **extra):
    out = {"status": "skipped", "reason": reason}
    out.update(extra)
    return out


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------

def load_data(path, variables):
    """Read the analysis table and apply types from the data dictionary.

    ``variables`` maps column name -> {"vtype": ..., "levels": [...]}.
    Ordinal columns are converted to integer codes following ``levels``.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df.replace({"": np.nan})
    for col, spec in variables.items():
        vtype = spec["vtype"]
        if vtype == "quantitative":
            df[col] = pd.to_numeric(df[col], errors="coerce")
        elif vtype == "ordinal":
            codes = {lvl: i for i, lvl in enumerate(spec["levels"])}
            df[col] = df[col].map(codes).astype(float)
    return df


# --------------------------------------------------------------------------
# Descriptive statistics (FR-2.1)
# --------------------------------------------------------------------------

def describe_var(df, col, vtype, levels=None):
    s = df[col]
    n_total = int(len(s))
    n_missing = int(s.isna().sum())
    out = {"variable": col, "vtype": vtype, "n": n_total - n_missing, "missing": n_missing}
    if vtype == "quantitative":
        v = s.dropna().astype(float).values
        if len(v) == 0:
            return out
        q1, med, q3 = np.percentile(v, [25, 50, 75])
        out.update({
            "median": _f(med), "q1": _f(q1), "q3": _f(q3),
            "min": _f(v.min()), "max": _f(v.max()),
            "mean": _f(v.mean()), "sd": _f(v.std(ddof=1)) if len(v) > 1 else None,
        })
    else:
        counts = []
        present = s.dropna()
        denom = len(present)
        if vtype == "ordinal":
            for i, lvl in enumerate(levels or []):
                k = int((present == i).sum())
                counts.append({"level": lvl, "count": k, "percent": _f(100.0 * k / denom, 1) if denom else None})
        else:
            order = list(levels or []) + sorted(set(present) - set(levels or []))
            for lvl in order:
                k = int((present == lvl).sum())
                counts.append({"level": lvl, "count": k, "percent": _f(100.0 * k / denom, 1) if denom else None})
        out["counts"] = counts
    return out


# --------------------------------------------------------------------------
# Association tests (FR-2.2). Test choice is made by the application from the
# data dictionary (PRD 2.2), not by the LLM.
# --------------------------------------------------------------------------

def _cramers_v(table):
    table = np.asarray(table, dtype=float)
    n = table.sum()
    r, c = table.shape
    if n == 0 or min(r, c) < 2:
        return float("nan")
    expected = np.outer(table.sum(1), table.sum(0)) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        chi2 = np.nansum((table - expected) ** 2 / np.where(expected == 0, np.nan, expected))
    return math.sqrt(chi2 / (n * (min(r, c) - 1)))


def _crosstab(a_vals, b_vals, la, lb):
    return np.array([[int(np.sum((a_vals == x) & (b_vals == y))) for y in lb] for x in la])


def cat_cat(df, a, b, levels_a, levels_b, allow_chi2=False, seed=0):
    """Two categorical/binary variables: Fisher exact (2x2 or RxC) or chi-square.

    Effect size: odds ratio with Woolf 95% CI for 2x2 tables (Haldane–Anscombe
    correction when a cell is zero), otherwise Cramér's V with a bootstrap CI.
    """
    sub = df[[a, b]].dropna()
    present_a, present_b = set(sub[a]), set(sub[b])
    la = [x for x in levels_a if x in present_a]
    lb = [x for x in levels_b if x in present_b]
    if len(la) < 2 or len(lb) < 2:
        return _skip("одна из переменных не варьирует среди случаев с полными данными", n=int(len(sub)))
    if len(sub) < 5:
        return _skip("менее 5 случаев с полными данными", n=int(len(sub)))
    av, bv = sub[a].values, sub[b].values
    table = _crosstab(av, bv, la, lb)
    n = int(table.sum())
    expected = np.outer(table.sum(1), table.sum(0)) / n
    out = {
        "status": "ok", "kind": "cat_cat", "a": a, "b": b, "n": n,
        "levels_a": la, "levels_b": lb, "table": table.tolist(),
        "min_expected": _f(expected.min(), 3),
    }
    if table.shape == (2, 2):
        res = stats.fisher_exact(table)
        out["test"] = "Fisher exact test (two-sided)"
        out["p"] = _f(res.pvalue, 8)
        t = table.astype(float)
        corrected = bool((t == 0).any())
        if corrected:
            t = t + 0.5
        # OR: odds of b == lb[1] for a == la[1] versus a == la[0]
        or_ = (t[1, 1] * t[0, 0]) / (t[1, 0] * t[0, 1])
        se = math.sqrt((1.0 / t).sum())
        out["effect"] = {
            "name": "OR", "value": _f(or_, 4),
            "ci_low": _f(math.exp(math.log(or_) - Z95 * se), 4),
            "ci_high": _f(math.exp(math.log(or_) + Z95 * se), 4),
            "method": "Woolf logit CI" + (", Haldane–Anscombe +0.5" if corrected else ""),
        }
        row_tot = table.sum(1)
        out["row_percent"] = [_f(100.0 * table[i, 1] / row_tot[i], 1) if row_tot[i] else None for i in range(2)]
    else:
        if allow_chi2 and expected.min() >= 5:
            chi2, p, dof, _ = stats.chi2_contingency(table, correction=False)
            out["test"] = "Pearson chi-square test"
            out["statistic"] = _f(chi2, 4)
        else:
            # For R×C tables scipy estimates the p-value by Monte Carlo; seed it for reproducibility.
            mc = stats.MonteCarloMethod(n_resamples=MC_RESAMPLES, rng=np.random.default_rng(seed + 1))
            p = stats.fisher_exact(table, method=mc).pvalue
            out["test"] = "Fisher–Freeman–Halton test (Monte Carlo, %d tables)" % MC_RESAMPLES
        out["p"] = _f(p, 8)
        rng = np.random.default_rng(seed)
        idx = np.arange(n)
        boots = []
        for _ in range(N_BOOT):
            s = rng.choice(idx, size=n, replace=True)
            boots.append(_cramers_v(_crosstab(av[s], bv[s], la, lb)))
        boots = np.array(boots)
        boots = boots[np.isfinite(boots)]
        out["effect"] = {
            "name": "Cramér's V", "value": _f(_cramers_v(table), 4),
            "ci_low": _f(np.percentile(boots, 2.5), 4) if len(boots) else None,
            "ci_high": _f(np.percentile(boots, 97.5), 4) if len(boots) else None,
            "method": "percentile bootstrap, B=%d, seed=%d" % (N_BOOT, seed),
        }
    return out


def _group_summary(v):
    q1, med, q3 = np.percentile(v, [25, 50, 75])
    return {"n": int(len(v)), "median": _f(med, 4), "q1": _f(q1, 4), "q3": _f(q3, 4),
            "min": _f(v.min(), 4), "max": _f(v.max(), 4)}


def _hodges_lehmann(x1, x0):
    return float(np.median(np.subtract.outer(x1, x0)))


def group_numeric(df, group, value, levels_group, seed=0):
    """Numeric/ordinal value compared across groups.

    Two groups: Mann–Whitney U, effect = Hodges–Lehmann shift (group 2 minus
    group 1) with bootstrap CI. More groups: Kruskal–Wallis, effect = epsilon².
    """
    sub = df[[group, value]].dropna()
    groups, names = [], []
    for g in levels_group:
        v = sub.loc[sub[group] == g, value].astype(float).values
        if len(v) > 0:
            groups.append(v)
            names.append(g)
    if len(groups) < 2:
        return _skip("меньше двух групп с данными", n=int(len(sub)))
    if min(len(v) for v in groups) < 2:
        return _skip("в одной из групп меньше 2 наблюдений", n=int(len(sub)))
    allv = np.concatenate(groups)
    if np.all(allv == allv[0]):
        return _skip("нет вариабельности значений", n=int(len(allv)))
    rng = np.random.default_rng(seed)
    out = {
        "status": "ok", "kind": "group_numeric", "a": group, "b": value, "n": int(len(allv)),
        "groups": [dict(level=nm, **_group_summary(v)) for nm, v in zip(names, groups)],
    }
    if len(groups) == 2:
        x0, x1 = groups
        res = stats.mannwhitneyu(x1, x0, alternative="two-sided")
        out["test"] = "Mann–Whitney U test (two-sided)"
        out["statistic"] = _f(res.statistic, 4)
        out["p"] = _f(res.pvalue, 8)
        boots = [
            _hodges_lehmann(rng.choice(x1, len(x1), replace=True), rng.choice(x0, len(x0), replace=True))
            for _ in range(N_BOOT)
        ]
        out["effect"] = {
            "name": "Hodges–Lehmann shift (%s − %s)" % (names[1], names[0]),
            "value": _f(_hodges_lehmann(x1, x0), 4),
            "ci_low": _f(np.percentile(boots, 2.5), 4), "ci_high": _f(np.percentile(boots, 97.5), 4),
            "method": "percentile bootstrap, B=%d, seed=%d" % (N_BOOT, seed),
        }
    else:
        h, p = stats.kruskal(*groups)
        n = len(allv)
        eps = h * (n + 1) / (n ** 2 - 1)
        boots = []
        for _ in range(N_BOOT):
            bs = [rng.choice(v, len(v), replace=True) for v in groups]
            cat = np.concatenate(bs)
            if np.all(cat == cat[0]):
                boots.append(0.0)
                continue
            hb = stats.kruskal(*bs).statistic
            boots.append(hb * (n + 1) / (n ** 2 - 1))
        out["test"] = "Kruskal–Wallis test"
        out["statistic"] = _f(h, 4)
        out["p"] = _f(p, 8)
        out["effect"] = {
            "name": "epsilon²", "value": _f(eps, 4),
            "ci_low": _f(np.percentile(boots, 2.5), 4), "ci_high": _f(np.percentile(boots, 97.5), 4),
            "method": "percentile bootstrap, B=%d, seed=%d" % (N_BOOT, seed),
        }
    return out


def spearman(df, a, b, seed=0):
    """Spearman rank correlation with Fisher-z CI (Bonett–Wright SE)."""
    sub = df[[a, b]].dropna().astype(float)
    n = int(len(sub))
    if n < 5:
        return _skip("менее 5 случаев с полными данными", n=n)
    if sub[a].nunique() < 2 or sub[b].nunique() < 2:
        return _skip("нет вариабельности значений", n=n)
    res = stats.spearmanr(sub[a], sub[b])
    rho = float(res.statistic)
    if abs(rho) >= 1.0:
        lo = hi = rho
    else:
        z = math.atanh(rho)
        se = math.sqrt((1 + rho ** 2 / 2.0) / (n - 3))
        lo, hi = math.tanh(z - Z95 * se), math.tanh(z + Z95 * se)
    return {
        "status": "ok", "kind": "spearman", "a": a, "b": b, "n": n,
        "test": "Spearman rank correlation", "p": _f(res.pvalue, 8),
        "effect": {"name": "Spearman ρ", "value": _f(rho, 4), "ci_low": _f(lo, 4), "ci_high": _f(hi, 4),
                   "method": "Fisher z, Bonett–Wright SE"},
    }


TESTS = {"cat_cat": cat_cat, "group_numeric": group_numeric, "spearman": spearman}


def bh_fdr(pvalues):
    """Benjamini–Hochberg adjusted q-values (same order as input)."""
    p = np.asarray(pvalues, dtype=float)
    m = len(p)
    if m == 0:
        return []
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    q_sorted = np.minimum.accumulate(ranked[::-1])[::-1]
    q = np.empty(m)
    q[order] = np.minimum(q_sorted, 1.0)
    return [float(x) for x in q]


# --------------------------------------------------------------------------
# Pattern search (FR-2.3, FR-2.5)
# --------------------------------------------------------------------------

def gower_distance(df, cols, variables):
    """Gower distance for mixed data types; missing values are skipped pairwise."""
    n = len(df)
    num = np.zeros((n, n))
    den = np.zeros((n, n))
    for col in cols:
        spec = variables[col]
        s = df[col]
        mask = s.notna().values
        both = np.outer(mask, mask)
        if spec["vtype"] in ("quantitative", "ordinal"):
            v = s.astype(float).values
            rng_ = np.nanmax(v) - np.nanmin(v) if mask.any() else 0
            if not rng_ or not np.isfinite(rng_):
                continue
            d = np.abs(np.subtract.outer(v, v)) / rng_
        else:
            v = s.astype(object).values
            d = (np.not_equal.outer(v, v)).astype(float)
        d = np.where(both, d, 0.0)
        num += np.nan_to_num(d)
        den += both
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = num / den
    dist[~np.isfinite(dist)] = 1.0
    np.fill_diagonal(dist, 0.0)
    return dist


def _cluster_labels(dist, k):
    z = hierarchy.linkage(squareform(dist, checks=False), method="average")
    return hierarchy.fcluster(z, t=k, criterion="maxclust"), z


def cluster_cases(df, cols, variables, case_col="case_id", ks=(2, 3, 4), stability=False, n_boot=100, seed=0):
    """Average-linkage hierarchical clustering on Gower distance.

    With ``stability`` the cluster-wise Jaccard bootstrap (Hennig, 2007) is
    computed: mean Jaccard similarity of each original cluster with the most
    similar cluster in bootstrap resamples (>= 0.75 stable, < 0.6 unstable).
    """
    cols = [c for c in cols if df[c].notna().sum() >= 2]
    if len(df) < 5 or len(cols) < 2:
        return _skip("недостаточно случаев или признаков для кластеризации")
    dist = gower_distance(df, cols, variables)
    z = hierarchy.linkage(squareform(dist, checks=False), method="average")
    order = [int(i) for i in hierarchy.leaves_list(z)]
    out = {"status": "ok", "method": "average-linkage hierarchical clustering, Gower distance",
           "variables": cols, "case_ids": df[case_col].tolist(), "order": order,
           "linkage": [[_f(x, 6) for x in row] for row in z.tolist()], "partitions": []}
    rng = np.random.default_rng(seed)
    n = len(df)
    for k in ks:
        if k >= n:
            continue
        labels = hierarchy.fcluster(z, t=k, criterion="maxclust")
        part = {"k": int(k), "labels": [int(x) for x in labels]}
        if stability:
            jac = {int(c): [] for c in set(labels)}
            for _ in range(n_boot):
                idx = np.unique(rng.choice(n, size=n, replace=True))
                if len(idx) <= k:
                    continue
                sub = dist[np.ix_(idx, idx)]
                bl, _ = _cluster_labels(sub, k)
                for c in jac:
                    orig = set(idx[labels[idx] == c])
                    if not orig:
                        continue
                    best = 0.0
                    for bc in set(bl):
                        bset = set(idx[bl == bc])
                        best = max(best, len(orig & bset) / len(orig | bset))
                    jac[c].append(best)
            part["stability"] = {str(c): _f(np.mean(v), 3) if v else None for c, v in jac.items()}
        out["partitions"].append(part)
    return out


def outliers(df, col, case_col="case_id"):
    """Tukey fences (1.5 × IQR) for a quantitative variable."""
    s = df[[case_col, col]].dropna()
    if len(s) < 5:
        return None
    v = s[col].astype(float)
    q1, q3 = np.percentile(v, [25, 75])
    iqr = q3 - q1
    if iqr == 0:
        return None
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    flagged = s[(v < lo) | (v > hi)]
    if flagged.empty:
        return None
    return {"variable": col, "median": _f(np.median(v), 4), "q1": _f(q1, 4), "q3": _f(q3, 4),
            "n": int(len(s)), "cases": [{"case_id": r[case_col], "value": _f(r[col], 4)} for _, r in flagged.iterrows()]}


def atypical_profiles(df, cols, case_col="case_id", dominance=0.8):
    """Cases that deviate from the dominant level of several near-uniform markers."""
    dominant = {}
    for col in cols:
        s = df[col].dropna()
        if len(s) < 5:
            continue
        top = s.value_counts()
        if top.iloc[0] / len(s) >= dominance and len(top) > 1:
            dominant[col] = top.index[0]
    if not dominant:
        return []
    threshold = 2 if len(dominant) >= 3 else 1
    found = []
    for _, row in df.iterrows():
        dev = [{"variable": c, "value": row[c], "typical": lvl}
               for c, lvl in dominant.items() if pd.notna(row[c]) and row[c] != lvl]
        if len(dev) >= threshold:
            found.append({"case_id": row[case_col], "deviations": dev})
    return found


def shared_and_discordant(df, cols):
    """For very small series: features shared by all cases vs. differing ones."""
    shared, discordant = [], []
    for col in cols:
        s = df[col].dropna()
        if len(s) < 2:
            continue
        vals = sorted(set(str(x) for x in s))
        if len(vals) == 1:
            shared.append({"variable": col, "value": vals[0], "n": int(len(s))})
        else:
            discordant.append({"variable": col, "values": vals, "n": int(len(s))})
    return {"shared": shared, "discordant": discordant}


# --------------------------------------------------------------------------
# Entry point used by the generated script
# --------------------------------------------------------------------------

def run_spec(df, spec):
    variables = spec["variables"]
    out = {"table1": [], "tests": [], "clustering": None, "outliers": [], "atypical": [], "shared": None}
    for col, v in variables.items():
        out["table1"].append(describe_var(df, col, v["vtype"], v.get("levels")))
    for t in spec["tests"]:
        res = TESTS[t["kind"]](df, **t["args"])
        res["id"] = t["id"]
        res["spec_key"] = t["key"]
        out["tests"].append(res)
    ok = [r for r in out["tests"] if r.get("status") == "ok" and r.get("p") is not None]
    for r, q in zip(ok, bh_fdr([r["p"] for r in ok])):
        r["q"] = _f(q, 8)
    cl = spec.get("clustering")
    if cl:
        out["clustering"] = cluster_cases(df, cl["variables"], variables, ks=tuple(cl["ks"]),
                                          stability=cl["stability"], seed=cl["seed"])
    for col in spec.get("outlier_variables", []):
        o = outliers(df, col)
        if o:
            out["outliers"].append(o)
    if spec.get("profile_variables"):
        out["atypical"] = atypical_profiles(df, spec["profile_variables"])
    if spec.get("shared_variables"):
        out["shared"] = shared_and_discordant(df, spec["shared_variables"])
    return out


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else "analysis_data.csv"
    df = load_data(path, SPEC["variables"])  # noqa: F821 — SPEC is defined by the generated script
    json.dump(run_spec(df, SPEC), sys.stdout, ensure_ascii=False, indent=1)  # noqa: F821
