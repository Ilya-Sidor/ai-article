"""Statistical guardrails by series size (PRD 2.2).

The test for a pair of variables is chosen here from the data dictionary types,
never by the LLM.
"""
from dataclasses import dataclass, field

CATEGORICAL = {"binary", "categorical"}
RANKED = {"ordinal", "quantitative"}
ANALYZABLE = CATEGORICAL | RANKED


@dataclass(frozen=True)
class Tier:
    code: str
    label: str
    n_range: str
    tests_allowed: bool
    kinds: frozenset
    allow_chi2: bool
    clustering: str  # "none" | "visual" | "stability"
    max_evidence: str  # "descriptive" | "exploratory" | "significant"
    rules: list = field(default_factory=list)

    def as_dict(self):
        return {
            "code": self.code, "label": self.label, "n_range": self.n_range,
            "tests_allowed": self.tests_allowed, "kinds": sorted(self.kinds),
            "allow_chi2": self.allow_chi2, "clustering": self.clustering,
            "max_evidence": self.max_evidence, "rules": self.rules,
        }


TIERS = {
    "descriptive": Tier(
        "descriptive", "Только описание", "n = 2–4", False, frozenset(), False, "none", "descriptive",
        ["Разрешены только описательная статистика и сравнение случаев между собой.",
         "Тесты значимости, p-values и кластеризация не выполняются."],
    ),
    "minimal": Tier(
        "minimal", "Разведочный анализ", "n = 5–9", True, frozenset({"cat_cat_2x2"}), False, "visual", "exploratory",
        ["Разрешён только точный тест Фишера для бинарных признаков, все результаты — exploratory.",
         "Кластеризация — только как визуализация.",
         "Утверждения о значимости в Abstract и Conclusion запрещены; многофакторные модели запрещены."],
    ),
    "small": Tier(
        "small", "Малая серия", "n = 10–29", True,
        frozenset({"cat_cat_2x2", "cat_cat", "group_numeric", "spearman"}), False, "stability", "significant",
        ["Fisher, Mann–Whitney, Kruskal–Wallis, Spearman.",
         "Кластеризация с оценкой устойчивости (bootstrap).",
         "Многофакторная регрессия не выполняется."],
    ),
    "full": Tier(
        "full", "Полный набор", "n ≥ 30", True,
        frozenset({"cat_cat_2x2", "cat_cat", "group_numeric", "spearman"}), True, "stability", "significant",
        ["Полный набор тестов, включая χ² при ожидаемых частотах ≥ 5."],
    ),
}

COMMON_RULES = [
    "Поправка Benjamini–Hochberg (FDR) по всему журналу проверенных гипотез; показываются p и q.",
    "Для каждой ассоциации — размер эффекта с 95% ДИ.",
    "Уровень доказательности: описательная / exploratory / значимая после поправки.",
]


def tier_for(n: int) -> Tier:
    if n < 5:
        return TIERS["descriptive"]
    if n < 10:
        return TIERS["minimal"]
    if n < 30:
        return TIERS["small"]
    return TIERS["full"]


def select_test(var_a: dict, var_b: dict, tier: Tier):
    """Return (kind, args, None) or (None, None, reason)."""
    ta, tb = var_a["vtype"], var_b["vtype"]
    if ta not in ANALYZABLE or tb not in ANALYZABLE:
        return None, None, "тип признака не участвует в анализе"
    if not tier.tests_allowed:
        return None, None, "guardrail: при n < 5 тесты значимости не выполняются"

    if ta in CATEGORICAL and tb in CATEGORICAL:
        is_2x2 = ta == "binary" and tb == "binary"
        needed = "cat_cat_2x2" if is_2x2 else "cat_cat"
        if needed not in tier.kinds:
            return None, None, f"guardrail ({tier.n_range}): допускается только тест Фишера 2×2"
        return "cat_cat", {
            "a": var_a["name"], "b": var_b["name"],
            "levels_a": var_a["levels"], "levels_b": var_b["levels"],
            "allow_chi2": tier.allow_chi2,
        }, None

    if "group_numeric" not in tier.kinds:
        return None, None, f"guardrail ({tier.n_range}): допускается только тест Фишера 2×2"

    if ta in CATEGORICAL and tb in RANKED:
        return "group_numeric", {"group": var_a["name"], "value": var_b["name"], "levels_group": var_a["levels"]}, None
    if tb in CATEGORICAL and ta in RANKED:
        return "group_numeric", {"group": var_b["name"], "value": var_a["name"], "levels_group": var_b["levels"]}, None
    return "spearman", {"a": var_a["name"], "b": var_b["name"]}, None


def evidence_level(result: dict, tier: Tier, alpha: float = 0.05) -> str:
    if result.get("p") is None:
        return "descriptive"
    q = result.get("q")
    if tier.max_evidence == "significant" and q is not None and q < alpha:
        return "significant"
    return "exploratory"
