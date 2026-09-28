"""Regression guards from the eval suite (NFR-15): run on every change of rules, NER or prompts."""
from evals.anonymization_eval import run


def test_anonymization_eval_thresholds():
    with_ner = run(n=150, seed=3, use_ner=True)
    rules = run(n=150, seed=3, use_ner=False)
    assert with_ner["recall_direct"] >= 0.99, with_ner["examples_missed"]
    assert with_ner["medical_text_preserved"] >= 0.98
    for cat in ("date", "phone", "snils", "policy", "record", "block", "email", "address"):
        assert rules["recall_by_category"][cat] == 1.0, (cat, rules["examples_missed"])
