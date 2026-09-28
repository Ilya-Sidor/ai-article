"""Anonymization eval (NFR-15): recall of direct identifiers and preservation of medical text.

Synthetic Russian/English pathology reports with labelled identifiers in many
surface forms (case forms of names, bare first names, titles, dates, phones,
SNILS, policy, addresses, record and block numbers, e-mail) and distractors
(eponyms and marker names that must survive).

Run: python -m evals.anonymization_eval  (from backend/)  → prints a table and
writes evals/results/anonymization.json. PRD acceptance on real reports:
≥ 99 % of direct identifiers; this synthetic set is the regression guard.
"""
import json
import random
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.anonymization import anonymize_frame  # noqa: E402

PEOPLE = [  # nominative, genitive, dative, instrumental; initials form
    ("Смирнова Ольга Николаевна", "Смирновой Ольги Николаевны", "Смирновой Ольге Николаевне",
     "Смирновой Ольгой Николаевной", "Смирнова О.Н.", "Смирнова", "Ольга"),
    ("Петров Андрей Сергеевич", "Петрова Андрея Сергеевича", "Петрову Андрею Сергеевичу",
     "Петровым Андреем Сергеевичем", "Петров А.С.", "Петров", "Андрей"),
    ("Кузнецова Мария Ивановна", "Кузнецовой Марии Ивановны", "Кузнецовой Марии Ивановне",
     "Кузнецовой Марией Ивановной", "Кузнецова М.И.", "Кузнецова", "Мария"),
    ("Волков Дмитрий Олегович", "Волкова Дмитрия Олеговича", "Волкову Дмитрию Олеговичу",
     "Волковым Дмитрием Олеговичем", "Волков Д.О.", "Волков", "Дмитрий"),
    ("Лебедева Елена Петровна", "Лебедевой Елены Петровны", "Лебедевой Елене Петровне",
     "Лебедевой Еленой Петровной", "Лебедева Е.П.", "Лебедева", "Елена"),
]
DOCTORS = [("Громов", "Громовым"), ("Белова", "Беловой"), ("Соколов", "Соколовым")]
EN_PEOPLE = ["John Smith", "Mary Johnson", "Robert Brown"]

EPONYMS = ["окраска по Ван Гизону", "болезнь Крона", "клетки Лангерганса", "лимфома Ходжкина",
           "саркома Юинга", "PAS-реакция (Шифф)", "окраска по Перлсу", "классификация Миеттинена",
           "метастаз Крукенберга", "пищевод Барретта"]
MARKERS = ["CD117", "DOG1", "Ki-67", "SDHB", "PDGFRA", "KIT"]

TEMPLATES = [
    # (template, {slot: category})
    ("Пациентка {nom}, {age} лет. Материал от {date}.", {"nom": "name", "date": "date"}),
    ("Направлен(а) от {gen}, и/б № {record}.", {"gen": "name", "record": "record"}),
    ("Выдано {dat} на руки, тел. {phone}.", {"dat": "name", "phone": "phone"}),
    ("Беседа проведена с {ins}; СНИЛС {snils}.", {"ins": "name", "snils": "snils"}),
    ("Больная {initials} обратилась {date_text}.", {"initials": "name", "date_text": "date"}),
    ("{surname} отметила ухудшение самочувствия.", {"surname": "name"}),
    ("Со слов дочери ({first}), боли беспокоят с весны.", {"first": "name"}),
    ("Консультирован проф. {doc_ins}, пересмотр блока {block}.", {"doc_ins": "name", "block": "block"}),
    ("Адрес: г. Самара, ул. Ленина, д. {house}, кв. {flat}; e-mail {email}.", {"addr": "address", "email": "email"}),
    ("Полис ОМС {policy}, дата рождения {birth}.", {"policy": "policy", "birth": "date"}),
    ("Patient: {en_name}, MRN {mrn}, seen on {en_date}.", {"en_name": "name", "mrn": "record", "en_date": "date"}),
]
RU_MONTHS = ["января", "марта", "мая", "июля", "сентября", "ноября"]


def _sample(rng, i):
    p = rng.choice(PEOPLE)
    doc = rng.choice(DOCTORS)
    values = {
        "nom": p[0], "gen": p[1], "dat": p[2], "ins": p[3], "initials": p[4], "surname": p[5], "first": p[6],
        "doc_ins": doc[1], "age": str(rng.randint(30, 85)),
        "date": f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.20{rng.randint(15, 24)}",
        "date_text": f"{rng.randint(1, 28)} {rng.choice(RU_MONTHS)} 20{rng.randint(15, 24)} г.",
        "birth": f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.19{rng.randint(40, 90)}",
        "record": f"{rng.randint(10000, 99999)}/{rng.randint(15, 24)}",
        "block": f"S{rng.randint(15, 24)}-{rng.randint(1000, 99999)}",
        "phone": f"+7 (9{rng.randint(10, 99)}) {rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}",
        "snils": f"{rng.randint(100, 999)}-{rng.randint(100, 999)}-{rng.randint(100, 999)} {rng.randint(10, 99)}",
        "policy": "".join(str(rng.randint(0, 9)) for _ in range(16)),
        "house": str(rng.randint(1, 99)), "flat": str(rng.randint(1, 300)),
        "email": f"user{i}@mail.ru", "en_name": rng.choice(EN_PEOPLE),
        "mrn": f"{rng.randint(100000, 999999)}", "en_date": f"March {rng.randint(1, 28)}, 20{rng.randint(15, 24)}",
    }
    tpl, slots = rng.choice(TEMPLATES)
    text = tpl.format(**values)
    eponym = rng.choice(EPONYMS)
    marker = rng.choice(MARKERS)
    text += f" Опухоль, {eponym}; {marker} положительный."
    truths = []
    for slot, cat in slots.items():
        if slot == "addr":
            truths.append(("address", "Ленина"))
            continue
        v = values[slot]
        if cat == "name":  # a name counts as removed when its surname/first-name token is gone
            v = v.split()[0].rstrip(".,")
        truths.append((cat, v))
    return text, truths, [eponym.split()[-1].strip("()"), marker]


def run(n=300, seed=11, use_ner=True):
    rng = random.Random(seed)
    samples = [_sample(rng, i) for i in range(n)]
    df = pd.DataFrame({"Заключение": [s[0] for s in samples]})
    out, _ = anonymize_frame(df, "eval.csv", {}, use_ner=use_ner)
    by_cat, kept = {}, [0, 0]
    misses = []
    for (text, truths, keep), clean in zip(samples, out["Заключение"]):
        for cat, value in truths:
            c = by_cat.setdefault(cat, [0, 0])
            c[1] += 1
            if value not in clean:
                c[0] += 1
            elif len(misses) < 15:
                misses.append({"category": cat, "value": value, "output": clean})
        for k in keep:
            kept[1] += 1
            kept[0] += k in clean
    total = [sum(v[0] for v in by_cat.values()), sum(v[1] for v in by_cat.values())]
    return {"n_reports": n, "ner": use_ner,
            "recall_by_category": {k: round(v[0] / v[1], 4) for k, v in sorted(by_cat.items())},
            "recall_direct": round(total[0] / total[1], 4), "medical_text_preserved": round(kept[0] / kept[1], 4),
            "examples_missed": misses}


def main():
    results = {"rules_only": run(use_ner=False), "rules_plus_ner": run(use_ner=True)}
    cats = sorted(results["rules_only"]["recall_by_category"])
    print(f"{'категория':<12}{'правила':>10}{'+ NER':>10}")
    for c in cats + ["ВСЕГО"]:
        a = results["rules_only"]["recall_direct"] if c == "ВСЕГО" else results["rules_only"]["recall_by_category"][c]
        b = results["rules_plus_ner"]["recall_direct"] if c == "ВСЕГО" else results["rules_plus_ner"]["recall_by_category"][c]
        print(f"{c:<12}{a:>10.1%}{b:>10.1%}")
    for k, r in results.items():
        print(f"{k}: медицинский текст сохранён {r['medical_text_preserved']:.1%}")
    out = Path(__file__).parent / "results"
    out.mkdir(exist_ok=True)
    (out / "anonymization.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return results


if __name__ == "__main__":
    main()
