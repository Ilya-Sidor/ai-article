import pandas as pd

from app.anonymization import anonymize_frame, parse_date


def _all_text(df):
    return " ".join(df.astype(str).values.ravel())


def test_direct_identifiers_removed(sample_df):
    clean, report = anonymize_frame(sample_df, "gist.xlsx", {})
    text = _all_text(clean)
    for col in ("ФИО", "Телефон", "СНИЛС", "№ истории болезни", "Номер блока", "Дата рождения", "Дата операции"):
        for value in sample_df[col].astype(str):
            assert value not in text, f"{col}: {value} leaked"
    for fio in sample_df["ФИО"]:
        assert fio.split()[0] not in text  # surnames also appear in free-text conclusions
    assert {"ФИО", "Телефон", "СНИЛС", "Дата рождения"}.isdisjoint(clean.columns)
    assert report["counts"]["name"] >= len(sample_df)  # patient + signing pathologist in every conclusion


def test_dates_become_intervals(sample_df):
    clean, report = anonymize_frame(sample_df, "gist.xlsx", {})
    assert report["index_date_column"] == "Дата операции"
    col = "Дата последнего контакта (мес. от индексной даты)"
    months = pd.to_numeric(clean[col])
    assert (months > 0).all() and (months < 80).all()
    assert "[+0.0 мес. от индексной даты]" in clean["Заключение"].iloc[0]


def test_record_numbers_map_to_stable_internal_ids(sample_df):
    link_map = {}
    first, _ = anonymize_frame(sample_df, "a.csv", link_map)
    second, _ = anonymize_frame(sample_df.iloc[::-1].reset_index(drop=True), "b.csv", link_map)
    col = "№ истории болезни (внутр. ID)"
    assert sorted(first[col]) == sorted(second[col])
    assert first[col].iloc[0] == second[col].iloc[-1]


def test_free_text_rules_ru_en():
    df = pd.DataFrame({"Описание": [
        "Больная Петрова Анна Сергеевна, тел. 8 912 345-67-89, СНИЛС 123-456-789 01, "
        "проживает г. Самара, ул. Ленина, д. 5, кв. 7; e-mail anna@mail.ru",
        "Patient: John Smith, MRN 44-1234, seen by Dr. Brown on March 3, 2021, 12 Baker Street",
        "Блок № 12345/21, стекло S21-00731, дата 03.04.2021, полис 1234567890123456",
    ]})
    clean, report = anonymize_frame(df, "t.csv", {})
    text = _all_text(clean)
    for leaked in ("Петрова", "Анна", "345-67-89", "123-456-789", "Самара", "Ленина", "anna@mail.ru", "John",
                   "Smith", "44-1234", "Brown", "2021", "Baker", "12345/21", "S21-00731", "1234567890123456"):
        assert leaked not in text, leaked
    kinds = set(report["counts"])
    assert {"name", "phone", "snils", "address", "email", "record", "date", "block", "policy"} <= kinds


def test_clinical_values_untouched(sample_df):
    clean, _ = anonymize_frame(sample_df, "gist.xlsx", {})
    for col in ("Возраст", "Размер, мм", "Ki-67, %", "Гистотип", "CD117", "Митозы на 5 мм2"):
        assert list(clean[col]) == list(sample_df[col].astype(str))


def test_quasi_identifiers_flagged():
    df = pd.DataFrame({"Возраст": ["54", "93"], "Диагноз": ["ГИСО", "ГИСО"]})
    _, report = anonymize_frame(df, "q.csv", {})
    reasons = " ".join(q["reason"] for q in report["quasi"])
    assert "квазиидентификатор" in reasons and "≥ 90" in reasons


def test_parse_date_formats():
    assert parse_date("03.04.2021").month == 4
    assert parse_date("2021-04-03 00:00:00").day == 3
    assert parse_date("3 апреля 2021 г.").month == 4
    assert parse_date("April 3, 2021").day == 3
    assert parse_date("pT2") is None
