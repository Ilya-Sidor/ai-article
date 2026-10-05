"""Upload → anonymization report → author confirmation → unified case table."""
import uuid

import pandas as pd

from .anonymization import Anonymizer, anonymize_frame
from .ingest import build_dictionary, prepare_analysis_frame, read_table
from .storage import ProjectStore, now_iso, read_json, write_json


class DataError(ValueError):
    pass


def safe_filename(filename: str, link_map: dict, upload_id: str) -> str:
    """The name of the file is kept in the report and the provenance. A report of one patient is often saved
    under the patient's name ("Смирнов.pdf") — a bare surname no detector can tell from a word — so documents
    get a neutral name; the names of tables are anonymised like any text."""
    from .documents import DOCUMENT_EXT
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    if f".{ext.lower()}" in DOCUMENT_EXT:
        return f"документ-{upload_id}.{ext.lower()}"
    clean = Anonymizer(filename, link_map).scrub_text(stem, "имя файла", 0)
    return clean + (f".{ext}" if dot else "")


def upload(store: ProjectStore, pid: str, filename: str, content: bytes) -> dict:
    raw = read_table(filename, content)
    if raw.empty:
        raise DataError("файл не содержит строк")
    link_map = store.link_map(pid)
    upload_id = uuid.uuid4().hex[:8]
    original_name, filename = filename, safe_filename(filename, link_map, upload_id)
    clean, report = anonymize_frame(raw, filename, link_map)
    if filename != original_name:
        report["notes"].append(f"Имя файла не сохраняется (может содержать ФИО): файл называется «{filename}».")
    store.save_link_map(pid, link_map)
    del raw  # the original never touches the disk

    d = store.dir(pid) / "pending"
    store.save_frame(d / f"{upload_id}.csv", clean)
    report.update({"upload_id": upload_id, "uploaded_at": now_iso()})
    write_json(d / f"{upload_id}.json", report)
    store.update(pid, stage="data_pending", anonymization={"status": "pending", "confirmed_at": None})
    store.audit(pid, "system", "data.upload.anonymized",
                {"file": filename, "rows": report["n_rows"], "counts": report["counts"]})
    return report


def pending_reports(store: ProjectStore, pid: str) -> list:
    d = store.dir(pid) / "pending"
    if not d.exists():
        return []
    return sorted((read_json(p) for p in d.glob("*.json")), key=lambda r: r["uploaded_at"])


def discard(store: ProjectStore, pid: str, upload_id: str = None):
    d = store.dir(pid) / "pending"
    for p in list(d.glob("*")) if d.exists() else []:
        if upload_id is None or p.stem == upload_id:
            p.unlink()
    _refresh_status(store, pid)
    store.audit(pid, "author", "data.upload.discarded", {"upload_id": upload_id or "all"})


def _refresh_status(store, pid):
    p = store.get(pid)
    if pending_reports(store, pid):
        status = "pending"
    elif store.dataset(pid) is not None:
        status = "confirmed"
    else:
        status = "none"
    anon = dict(p["anonymization"], status=status)
    store.update(pid, anonymization=anon)


def _merge(base: pd.DataFrame, new: pd.DataFrame, link: str, filename: str) -> pd.DataFrame:
    """Rows of a new file join existing cases by internal record ID when both have one;
    otherwise they are appended as new cases."""
    new = new.drop(columns=["_source_row"])
    if link:
        return base.merge(new, on=link, how="outer", suffixes=("", f" [{filename}]"))
    return pd.concat([base, new], ignore_index=True, sort=False)


def confirm(store: ProjectStore, pid: str) -> dict:
    reports = pending_reports(store, pid)
    if not reports:
        raise DataError("нет загрузок, ожидающих подтверждения")
    d = store.dir(pid)
    dataset = store.dataset(pid)
    provenance = read_json(d / "provenance.json", {})
    for rep in reports:
        frame = store.load_frame(d / "pending" / f"{rep['upload_id']}.csv")
        rows = frame["_source_row"].tolist()
        if dataset is None:
            dataset = frame.drop(columns=["_source_row"])
            first = max([_case_key(x["case_id"]) for x in deleted_cases(store, pid)] + [0])  # ids in the trash too
            dataset.insert(0, "case_id", ["C-%03d" % (first + i + 1) for i in range(len(dataset))])
            for cid, r in zip(dataset["case_id"], rows):
                provenance.setdefault(cid, []).append({"file": rep["file"], "row": int(r)})
            continue
        before = set(dataset["case_id"].dropna())
        link = rep.get("link_column")
        if link and link in dataset.columns:
            lookup = dict(zip(dataset[link], dataset["case_id"]))
            for key, r in zip(frame[link], rows):
                if key in lookup:
                    provenance.setdefault(lookup[key], []).append({"file": rep["file"], "row": int(r)})
        else:
            link = None
        dataset = _merge(dataset, frame, link, rep["file"])
        missing_ids = dataset["case_id"].isna()
        start = max([int(c[2:]) for c in before if str(c)[2:].isdigit()]  # never reuse an id, even one in the trash
                    + [_case_key(x["case_id"]) for x in deleted_cases(store, pid)] + [0])
        new_ids = ["C-%03d" % (start + i + 1) for i in range(int(missing_ids.sum()))]
        dataset.loc[missing_ids, "case_id"] = new_ids
        if not link:
            for cid, r in zip(new_ids, rows):
                provenance.setdefault(cid, []).append({"file": rep["file"], "row": int(r)})
    dataset = dataset.fillna("")
    store.save_frame(d / "dataset.csv", dataset)
    write_json(d / "provenance.json", provenance)

    old = {v["name"]: v for v in read_json(d / "dictionary.json", [])}
    fresh = build_dictionary(dataset)
    dictionary = [old.get(v["name"], v) if old.get(v["name"], {}).get("edited") else v for v in fresh]
    write_json(d / "dictionary.json", dictionary)

    discard_files = list((d / "pending").glob("*"))
    for p in discard_files:
        p.unlink()
    p = store.update(pid, stage="data_confirmed", n_cases=int(len(dataset)),
                     anonymization={"status": "confirmed", "confirmed_at": now_iso()})
    store.audit(pid, "author", "anonymization.confirmed",
                {"files": [r["file"] for r in reports], "n_cases": int(len(dataset))})
    return p


def dataset_view(store: ProjectStore, pid: str) -> dict:
    df = store.dataset(pid)
    if df is None:
        return {"columns": [], "rows": [], "provenance": {}, "problems": [], "dictionary": [],
                "deleted": _trash_view(store, pid)}
    d = store.dir(pid)
    dictionary = read_json(d / "dictionary.json", [])
    _, problems = prepare_analysis_frame(df, dictionary)
    return {
        "columns": list(df.columns), "rows": df.to_dict(orient="records"),
        "provenance": read_json(d / "provenance.json", {}), "problems": problems, "dictionary": dictionary,
        "extracted": read_json(d / "extracted.json", {}), "conflicts": read_json(d / "extraction_conflicts.json", []),
        "text_columns": [v["name"] for v in dictionary if v.get("vtype") == "text"],
        "deleted": _trash_view(store, pid),
    }


def _trash_view(store, pid):
    out = []
    for x in deleted_cases(store, pid):
        values = [v for k, v in x["row"].items() if k != "case_id" and v and len(v) < 40][:4]
        out.append({"case_id": x["case_id"], "deleted_at": x["deleted_at"], "preview": values})
    return out


def edit_cell(store: ProjectStore, pid: str, case_id: str, column: str, value: str):
    df = store.dataset(pid)
    if df is None or column not in df.columns or column == "case_id":
        raise DataError("неизвестный столбец")
    mask = df["case_id"] == case_id
    if not mask.any():
        raise DataError("неизвестный случай")
    old = df.loc[mask, column].iloc[0]
    df.loc[mask, column] = value
    store.save_frame(store.dir(pid) / "dataset.csv", df)
    extracted = read_json(store.dir(pid) / "extracted.json", {})
    if extracted.get(case_id, {}).pop(column, None) is not None:  # checked by the author: no longer "from AI"
        write_json(store.dir(pid) / "extracted.json", extracted)
    store.audit(pid, "author", "dataset.cell_edited", {"case_id": case_id, "column": column, "old": old, "new": value})


TRASH = "deleted_cases.json"


def deleted_cases(store, pid):
    return read_json(store.dir(pid) / TRASH, [])


def delete_case(store: ProjectStore, pid: str, case_id: str) -> dict:
    """Move one case (patient) out of the case table into the project's trash (restorable): the row, its
    provenance and AI-extraction marks go together, so a restore brings everything back."""
    df = store.dataset(pid)
    if df is None or not (df["case_id"] == case_id).any():
        raise DataError("случай не найден")
    d = store.dir(pid)
    entry = {"case_id": case_id, "deleted_at": now_iso(),
             "row": {k: ("" if pd.isna(v) else str(v)) for k, v in df[df["case_id"] == case_id].iloc[0].items()}}
    df = df[df["case_id"] != case_id].reset_index(drop=True)
    for name in ("provenance.json", "extracted.json"):
        data = read_json(d / name, {})
        removed = data.pop(case_id, None)
        if removed is not None:
            entry[name] = removed
            write_json(d / name, data)
    conflicts = read_json(d / "extraction_conflicts.json", [])
    entry["conflicts"] = [c for c in conflicts if c.get("case_id") == case_id]
    write_json(d / "extraction_conflicts.json", [c for c in conflicts if c.get("case_id") != case_id])
    write_json(d / TRASH, [x for x in deleted_cases(store, pid) if x["case_id"] != case_id] + [entry])
    if df.empty:  # the last case: back to the upload step
        for name in ("dataset.csv", "dictionary.json"):
            if (d / name).exists():
                (d / name).unlink()
        p = store.update(pid, stage="created", n_cases=0, anonymization={"status": "none", "confirmed_at": None})
    else:
        store.save_frame(d / "dataset.csv", df)
        old = {v["name"]: v for v in read_json(d / "dictionary.json", [])}
        write_json(d / "dictionary.json", [old.get(v["name"], v) if old.get(v["name"], {}).get("edited") else v
                                           for v in build_dictionary(df)])
        p = store.update(pid, n_cases=int(len(df)))
    store.audit(pid, "author", "dataset.case_deleted", {"case_id": case_id, "n_cases": int(len(df))})
    return p


def _case_key(cid):
    return int(cid[2:]) if str(cid)[2:].isdigit() else 10**9


def restore_case(store: ProjectStore, pid: str, case_id: str) -> dict:
    """Put a deleted case back (columns that appeared since are left empty for it)."""
    trash = deleted_cases(store, pid)
    entry = next((x for x in trash if x["case_id"] == case_id), None)
    if entry is None:
        raise DataError("в корзине нет такого случая")
    d = store.dir(pid)
    df = store.dataset(pid)
    row = pd.DataFrame([entry["row"]])
    df = row if df is None else pd.concat([df, row], ignore_index=True, sort=False)
    cols = ["case_id"] + [c for c in df.columns if c != "case_id"]
    df = df[cols].fillna("")
    df = df.iloc[sorted(range(len(df)), key=lambda i: _case_key(df["case_id"].iloc[i]))].reset_index(drop=True)
    store.save_frame(d / "dataset.csv", df)
    for name in ("provenance.json", "extracted.json"):
        if name in entry:
            data = read_json(d / name, {})
            data[case_id] = entry[name]
            write_json(d / name, data)
    if entry.get("conflicts"):
        write_json(d / "extraction_conflicts.json", read_json(d / "extraction_conflicts.json", []) + entry["conflicts"])
    old = {v["name"]: v for v in read_json(d / "dictionary.json", [])}
    write_json(d / "dictionary.json", [old.get(v["name"], v) if old.get(v["name"], {}).get("edited") else v
                                       for v in build_dictionary(df)])
    write_json(d / TRASH, [x for x in trash if x["case_id"] != case_id])
    project = store.get(pid)
    changes = {"n_cases": int(len(df))}
    if project["stage"] == "created":  # the data had been confirmed before it was deleted
        changes.update(stage="data_confirmed", anonymization={"status": "confirmed", "confirmed_at": now_iso()})
    p = store.update(pid, **changes)
    store.audit(pid, "author", "dataset.case_restored", {"case_id": case_id, "n_cases": int(len(df))})
    return p


def purge_case(store: ProjectStore, pid: str, case_id: str):
    """Delete a case from the trash for good."""
    trash = deleted_cases(store, pid)
    if not any(x["case_id"] == case_id for x in trash):
        raise DataError("в корзине нет такого случая")
    write_json(store.dir(pid) / TRASH, [x for x in trash if x["case_id"] != case_id])
    store.audit(pid, "author", "dataset.case_purged", {"case_id": case_id})


ALLOWED_VTYPES = {"binary", "categorical", "ordinal", "quantitative", "identifier", "text"}
ALLOWED_GROUPS = {"clinical", "morphology", "ihc", "molecular", "followup", "other"}


def save_dictionary(store: ProjectStore, pid: str, items: list) -> list:
    d = store.dir(pid)
    current = {v["name"]: v for v in read_json(d / "dictionary.json", [])}
    for item in items:
        v = current.get(item.get("name"))
        if v is None:
            raise DataError(f"неизвестный признак: {item.get('name')}")
        if item.get("vtype") not in ALLOWED_VTYPES or item.get("group") not in ALLOWED_GROUPS:
            raise DataError(f"недопустимый тип или группа для «{v['name']}»")
        changed = {k: item[k] for k in ("vtype", "group", "levels", "include", "unit", "label") if k in item}
        if changed != {k: v.get(k) for k in changed}:
            v.update(changed)
            v["edited"] = True
    result = list(current.values())
    write_json(d / "dictionary.json", result)
    store.audit(pid, "author", "dictionary.saved", {"n": len(items)})
    return result
