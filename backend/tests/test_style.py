"""Signs of AI prose: detection (en/ru), checks with a fix, prompt rules, style examples, automatic style pass."""
import pytest
from test_manuscript import Claude, _sec, project  # noqa: F401

AI_TEXT = ("GIST plays a pivotal role in the intricate landscape of mesenchymal tumours. Notably, our findings underscore "
           "the importance of meticulous assessment, highlighting the need for comprehensive evaluation. This study not "
           "only delves into the interplay of KIT and PDGFRA but also sheds light on their value. These results hold great "
           "promise. Furthermore, they pave the way for better classification.")


def test_analyze():
    from app.manuscript import style
    ai = style.analyze(AI_TEXT, "en")
    assert ai["score"] >= 60 and {"pivotal", "landscape", "Notably,"} <= {h["fragment"] for h in ai["hits"]}
    human = style.analyze("We reviewed 24 GISTs. KIT and PDGFRA mutations were mutually exclusive. Mitotic count "
                          "correlated with the Ki-67 index. The series is small; these associations need confirmation.", "en")
    assert human["score"] == 0
    ru = style.analyze("Опухоль играет ключевую роль. Стоит отметить, что данное исследование проливает свет на проблему. "
                       "Полученные данные согласуются с литературой.", "ru")
    frags = {h["fragment"] for h in ru["hits"]}
    assert {"играет ключевую роль", "Стоит отметить", "данное", "проливает свет"} <= frags
    assert not any("данные" in f for f in frags)  # "данные" (data) is an ordinary word
    instr = style.humanize_instruction(ai, "en")
    assert "«pivotal»" in instr and "плейсхолдеры" in instr


class StyleClaude(Claude):
    def respond(self, kw):
        user = kw["messages"][0]["content"]
        if "Revise the section" in user and "не звучал как текст языковой модели" in user:
            return self._r({"text": "GIST is a common mesenchymal tumour. We describe {{n}} cases.", "new_citations": [],
                            "questions": []})
        return super().respond(kw)


@pytest.fixture()
def claude(monkeypatch):
    from app import llm
    c = StyleClaude()
    monkeypatch.setattr(llm, "client", lambda: c)
    return c


def test_style_in_generation(api, project, claude):  # noqa: F811
    pid, kit, expl = project
    api.put(f"/api/projects/{pid}/manuscript/inputs",
            json={"style_sample": "We examined the archive of our department. Cases were reviewed by two pathologists."})
    api.post(f"/api/projects/{pid}/manuscript/terms/auto")
    api.post(f"/api/projects/{pid}/manuscript/plan")
    m = api.post(f"/api/projects/{pid}/manuscript/plan/approve").json()
    K = {s["kind"]: s["key"] for s in m["sections"]}
    claude.sections = {"introduction": AI_TEXT}
    m = api.post(f"/api/projects/{pid}/manuscript/sections/{K['introduction']}/generate").json()
    sec = _sec(m, "introduction")
    # the AI-sounding draft got an automatic style pass
    assert [v["note"] for v in sec["versions"]] == ["черновик агента", "стилистическая правка (автоматически)"]
    assert sec["style_score"] == 0 and "pivotal" not in sec["source"]
    gen_call = next(c for c in claude.calls if "(kind: introduction)" in c["messages"][0]["content"])
    system = gen_call["system"] if isinstance(gen_call["system"], str) else " ".join(b["text"] for b in gen_call["system"])
    assert "Avoid words that language models over-use" in system
    assert "We examined the archive of our department" in gen_call["messages"][0]["content"]  # style example

    # manual: a marker in the text → a warning with a one-click fix
    api.put(f"/api/projects/{pid}/manuscript/sections/{K['introduction']}",
            json={"source": "This study delves into GIST. " + "We describe {{n}} cases."})
    m = api.get(f"/api/projects/{pid}/manuscript").json()
    warn = next(i for i in _sec(m, "introduction")["issues"] if i["code"] == "ai_style")
    assert warn["fragment"] == "delves" and warn["fix"]["actions"][0]["type"] == "humanize"
    m = api.post(f"/api/projects/{pid}/manuscript/sections/{K['introduction']}/humanize").json()
    assert _sec(m, "introduction")["versions"][-1]["note"] == "убран ИИ-стиль"
