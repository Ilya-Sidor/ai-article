"""Build a real, public, de-identified GIST case table for testing from cBioPortal.

Source: "Gastrointestinal Stromal Tumors (MSK, Clin Cancer Res 2023)", cBioPortal study
gist_msk_2023 (PMID 37477937). cBioPortal data are publicly available for research; cite the
original study and cBioPortal (Cerami et al. 2012; Gao et al. 2013) when using them.

Output (one row per patient, first primary-tumour sample preferred):
  samples/public/gist_msk_2023_all.xlsx     — all patients
  samples/public/gist_msk_2023_quick.xlsx   — random 60 patients (fast test)

Usage: python samples/build_public_gist.py
"""
import json
import random
import re
import urllib.request
from pathlib import Path

import pandas as pd

API = "https://www.cbioportal.org/api"
STUDY = "gist_msk_2023"
OUT = Path(__file__).parent / "public"


def get(path):
    with urllib.request.urlopen(f"{API}{path}", timeout=60) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def clinical(kind):
    rows = get(f"/studies/{STUDY}/clinical-data?clinicalDataType={kind}&projection=SUMMARY&pageSize=100000")
    key = "patientId" if kind == "PATIENT" else "sampleId"
    table = {}
    for r in rows:
        table.setdefault(r[key], {})[r["clinicalAttributeId"]] = r["value"]
    return table


def exon(gene, protein):
    m = re.search(r"(\d+)", protein or "")
    if not m:
        return "другая"
    pos = int(m.group(1))
    ranges = {"KIT": [(550, 592, "экзон 11"), (500, 510, "экзон 9"), (627, 664, "экзон 13"), (665, 710, "экзон 14"),
                      (788, 828, "экзон 17"), (449, 514, "экзон 8–9")],
              "PDGFRA": [(814, 854, "экзон 18"), (561, 571, "экзон 12"), (650, 680, "экзон 14")]}
    for lo, hi, name in ranges.get(gene, []):
        if lo <= pos <= hi:
            return name
    return "другая"


def main():
    samples = get(f"/studies/{STUDY}/samples?pageSize=100000")
    sample_clin = clinical("SAMPLE")
    patient_clin = clinical("PATIENT")
    muts = post(f"/molecular-profiles/{STUDY}_mutations/mutations/fetch?projection=DETAILED",
                {"sampleListId": f"{STUDY}_all"})
    by_sample = {}
    for m in muts:
        by_sample.setdefault(m["sampleId"], []).append((m["gene"]["hugoGeneSymbol"], m.get("proteinChange", "")))

    chosen = {}
    for s in samples:  # one sample per patient, primary tumour first
        pid, sid = s["patientId"], s["sampleId"]
        st = sample_clin.get(sid, {}).get("SAMPLE_TYPE", "")
        if pid not in chosen or (st == "Primary" and sample_clin.get(chosen[pid], {}).get("SAMPLE_TYPE") != "Primary"):
            chosen[pid] = sid

    rows = []
    for pid, sid in chosen.items():
        pc, sc = patient_clin.get(pid, {}), sample_clin.get(sid, {})
        genes = by_sample.get(sid, [])
        kit = [p for g, p in genes if g == "KIT"]
        pdgfra = [p for g, p in genes if g == "PDGFRA"]
        rfs = pc.get("RFS_STATUS", "")
        os_ = pc.get("OS_STATUS", "")
        rows.append({
            "Возраст": pc.get("AGE_AT_DIAGNOSIS") or pc.get("AGE_AT_SEQ_REPORTED_YEARS") or "",
            "Пол": {"Male": "М", "Female": "Ж"}.get(pc.get("SEX", ""), ""),
            "Локализация": {"Gastric": "желудок", "Small Bowel": "тонкая кишка", "Rectum": "прямая кишка",
                            "Esophagus": "пищевод", "Colon": "ободочная кишка"}.get(sc.get("PRIMARY_SITE", ""),
                                                                                   sc.get("PRIMARY_SITE", "")),
            "Тип образца": {"Primary": "первичная опухоль", "Metastasis": "метастаз",
                            "Local Recurrence": "местный рецидив"}.get(sc.get("SAMPLE_TYPE", ""), sc.get("SAMPLE_TYPE", "")),
            "Размер опухоли, см": pc.get("FIRST_TREATMENT_TUMOR_SIZE_CM", ""),
            "Митозы на 50 ПЗБУ": pc.get("FIRST_TREATMENT_MIOTIC_RATE_50HPF", ""),
            "Группа риска": {"Very Low": "очень низкий", "Low": "низкий", "Moderate": "умеренный",
                             "High": "высокий"}.get(pc.get("RISK_GROUP", ""), ""),
            "Стадия при диагнозе": {"Localized": "локализованная", "Metastatic": "метастатическая"}.get(
                pc.get("STAGE_AT_DIAGNOSIS", ""), pc.get("STAGE_AT_DIAGNOSIS", "")),
            "KIT мутация": "да" if kit else "нет",
            "KIT экзон": exon("KIT", kit[0]) if kit else "",
            "PDGFRA мутация": "да" if pdgfra else "нет",
            "PDGFRA экзон": exon("PDGFRA", pdgfra[0]) if pdgfra else "",
            "SDH-мутация (SDHA/B/C/D)": "да" if any(g in ("SDHA", "SDHB", "SDHC", "SDHD") for g, _ in genes) else "нет",
            "NF1 мутация": "да" if any(g == "NF1" for g, _ in genes) else "нет",
            "TP53 мутация": "да" if any(g == "TP53" for g, _ in genes) else "нет",
            "Число мутаций": sc.get("MUTATION_COUNT", ""),
            "Рецидив/прогрессирование": "да" if rfs.startswith("1") else ("нет" if rfs.startswith("0") else ""),
            "Безрецидивная выживаемость, мес": pc.get("RFS_MONTHS", ""),
            "Умер": "да" if os_.startswith("1") else ("нет" if os_.startswith("0") else ""),
            "Общая выживаемость, мес": pc.get("OS_MONTHS", ""),
        })
    df = pd.DataFrame(rows)
    OUT.mkdir(exist_ok=True)
    df.to_excel(OUT / "gist_msk_2023_all.xlsx", index=False)
    random.seed(2023)
    quick = df.loc[sorted(random.sample(range(len(df)), min(60, len(df))))]
    quick.to_excel(OUT / "gist_msk_2023_quick.xlsx", index=False)
    print(f"{len(df)} patients → {OUT}")
    print(df.notna().mean().round(2).to_dict())


if __name__ == "__main__":
    main()
