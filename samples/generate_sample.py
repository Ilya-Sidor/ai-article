"""Synthetic GIST series for demos and tests.

All names, numbers and dates are fictitious. The table deliberately contains
personal data columns and identifiers in free text so that the anonymization
step has something to find. Planted signal:
  * PDGFRA mutations ⇔ epithelioid histotype, gastric site, weak/negative CD117;
  * KIT and PDGFRA mutations are mutually exclusive;
  * Ki-67 increases with mitotic count; recurrences concentrate in high-risk cases.

Usage: python samples/generate_sample.py  →  samples/gist_series.xlsx / .csv
"""
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SURNAMES_M = ["Иванов", "Петров", "Смирнов", "Кузнецов", "Попов", "Соколов", "Лебедев", "Козлов", "Новиков",
              "Морозов", "Волков", "Алексеев"]
SURNAMES_F = ["Иванова", "Петрова", "Смирнова", "Кузнецова", "Попова", "Соколова", "Лебедева", "Козлова",
              "Новикова", "Морозова", "Волкова", "Алексеева"]
NAMES_M = [("Сергей", "Иванович"), ("Андрей", "Петрович"), ("Николай", "Сергеевич"), ("Олег", "Андреевич")]
NAMES_F = [("Анна", "Сергеевна"), ("Мария", "Ивановна"), ("Ольга", "Николаевна"), ("Елена", "Олеговна")]
PATHOLOGISTS = ["Соколова Е.В.", "Громов А.А.", "Р.Т. Белова"]


def generate(n=24, seed=7):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        sex = "Ж" if rng.random() < 0.5 else "М"
        if sex == "Ж":
            first, patr = NAMES_F[rng.integers(len(NAMES_F))]
            fio = f"{SURNAMES_F[rng.integers(len(SURNAMES_F))]} {first} {patr}"
        else:
            first, patr = NAMES_M[rng.integers(len(NAMES_M))]
            fio = f"{SURNAMES_M[rng.integers(len(SURNAMES_M))]} {first} {patr}"
        age = int(rng.integers(38, 82))
        surgery = date(2016, 1, 1) + timedelta(days=int(rng.integers(0, 6 * 365)))
        birth = surgery - timedelta(days=int(age * 365.25 + rng.integers(0, 300)))

        pdgfra = i % 5 == 0  # ~20% PDGFRA-mutant
        kit = (not pdgfra) and rng.random() < 0.85
        if pdgfra:
            site = "желудок" if rng.random() < 0.9 else "тонкая кишка"
            histotype = "эпителиоидный" if rng.random() < 0.8 else "смешанный"
            cd117 = "отрицательный" if rng.random() < 0.6 else "положительный"
            kit_txt = "нет"
            pdgfra_txt = "да"
        else:
            site = rng.choice(["желудок", "тонкая кишка", "прямая кишка"], p=[0.5, 0.4, 0.1])
            histotype = rng.choice(["веретеноклеточный", "смешанный", "эпителиоидный"], p=[0.75, 0.2, 0.05])
            cd117 = "положительный" if rng.random() < 0.95 else "отрицательный"
            kit_txt = "да" if kit else "нет"
            pdgfra_txt = "нет"
        dog1 = "положительный" if rng.random() < 0.93 else "отрицательный"
        size = int(np.clip(rng.lognormal(3.6, 0.5), 12, 190))
        mitoses = int(np.clip(rng.poisson(3 if pdgfra else 5) + (6 if rng.random() < 0.2 else 0), 0, 40))
        ki67 = int(np.clip(round(2 + 1.4 * mitoses + rng.normal(0, 2.5)), 1, 60))
        necrosis = "да" if (size > 60 or mitoses > 8) and rng.random() < 0.8 else "нет"
        if mitoses <= 5 and size <= 50:
            risk = "низкий"
        elif mitoses <= 5 and size <= 100:
            risk = "умеренный"
        else:
            risk = "высокий"
        if size <= 20 and mitoses <= 5:
            risk = "очень низкий"
        recurrence = "да" if (risk == "высокий" and rng.random() < 0.6) or rng.random() < 0.05 else "нет"
        follow = surgery + timedelta(days=int(rng.integers(8, 72) * 30.4))
        block = f"S{surgery.year % 100:02d}-{rng.integers(1000, 99999)}"
        record = f"{rng.integers(10000, 99999)}/{surgery.year % 100:02d}"
        conclusion = (
            f"Пациент{'ка' if sex == 'Ж' else ''} {fio.split()[0]} {fio.split()[1][0]}.{fio.split()[2][0]}., "
            f"и/б № {record}. Материал от {surgery.strftime('%d.%m.%Y')}, блок {block}. "
            f"ГИСО {histotype} типа, митозы {mitoses} на 5 мм2. Заключение подписал врач {PATHOLOGISTS[i % 3]}, "
            f"тел. +7 (912) {rng.integers(100, 999)}-{rng.integers(10, 99)}-{rng.integers(10, 99)}."
        )
        rows.append({
            "ФИО": fio,
            "Дата рождения": birth.strftime("%d.%m.%Y"),
            "№ истории болезни": record,
            "Телефон": f"8 9{rng.integers(10, 99)} {rng.integers(100, 999)}-{rng.integers(10, 99)}-{rng.integers(10, 99)}",
            "СНИЛС": f"{rng.integers(100, 999)}-{rng.integers(100, 999)}-{rng.integers(100, 999)} {rng.integers(10, 99)}",
            "Номер блока": block,
            "Дата операции": surgery.strftime("%d.%m.%Y"),
            "Дата последнего контакта": follow.strftime("%d.%m.%Y"),
            "Возраст": age,
            "Пол": sex,
            "Локализация": site,
            "Размер, мм": size,
            "Гистотип": histotype,
            "Митозы на 5 мм2": mitoses,
            "Некроз": necrosis,
            "Риск (Miettinen)": risk,
            "CD117": cd117,
            "DOG1": dog1,
            "Ki-67, %": ki67,
            "KIT мутация": kit_txt,
            "PDGFRA мутация": pdgfra_txt,
            "Рецидив": recurrence,
            "Заключение": conclusion,
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = Path(__file__).parent
    df = generate()
    df.to_excel(out / "gist_series.xlsx", index=False)
    df.to_csv(out / "gist_series.csv", index=False)
    df.head(4).to_csv(out / "gist_small_n4.csv", index=False)
    print(f"written {len(df)} cases to {out}")
