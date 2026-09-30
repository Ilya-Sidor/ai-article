"""Export (FR-5.13): manuscript .docx, figures in journal format, tables, references, supplementary.

Built with python-docx (no external binaries). A draft export is always possible
and is watermarked with the list of open issues; the submission export requires
that no blocking issue remains (PRD 7.2).
"""
import io
import zipfile

from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from ..analysis import figures as figs
from . import lang as L
from .render import Renderer, plain_text as _t


def _base_document():
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(12)
    st.paragraph_format.line_spacing = 2.0
    st.paragraph_format.space_after = Pt(0)
    for name in ("Heading 1", "Heading 2", "Title"):
        doc.styles[name].font.name = "Times New Roman"
        doc.styles[name].font.color.rgb = RGBColor(0, 0, 0)
    sect = doc.sections[0]
    ln = OxmlElement("w:lnNumType")  # continuous line numbers, requested by most journals for review
    ln.set(qn("w:countBy"), "1")
    ln.set(qn("w:restart"), "continuous")
    sect._sectPr.append(ln)
    p = sect.footer.paragraphs[0]
    run = p.add_run()
    for tag, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if tag:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), tag)
        else:
            el = OxmlElement("w:instrText")
            el.text = text
        run._r.append(el)
    return doc


def _add_paragraph(doc, segments, draft):
    p = doc.add_paragraph()
    for seg in segments:
        run = p.add_run(_t(seg["v"] if seg["t"] != "todo" else f"[{seg['v']}]"))
        if seg["t"] in ("todo", "unknown") or (seg["t"] == "cite" and seg.get("bad")):
            run.font.color.rgb = RGBColor(0xB0, 0x10, 0x30)
            run.bold = True
    return p


def _add_section(doc, heading, source, renderer, draft, level=1, kind=None):
    if heading:
        doc.add_heading(_t(heading), level=level)
    for para in renderer.segments(source, kind):
        if para["type"] == "heading":
            doc.add_heading(para["segments"][0]["v"], level=level + 1)
        else:
            _add_paragraph(doc, para["segments"], draft)


def _add_table(doc, number, table, lang="en"):
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    cap = doc.add_paragraph()
    cap.add_run(f"{L.label('table', lang)} {number}. ").bold = True
    cap.add_run(_t(table["caption"]))
    if lang == "ru" and table.get("caption_en"):  # captions in both languages (Russian journals)
        cap_en = doc.add_paragraph()
        cap_en.add_run(f"Table {number}. ").bold = True
        cap_en.add_run(_t(table["caption_en"]))
    t = doc.add_table(rows=1, cols=len(table["columns"]))
    t.style = "Table Grid"
    for i, c in enumerate(table["columns"]):
        t.rows[0].cells[i].text = _t(str(c))
        for r in t.rows[0].cells[i].paragraphs[0].runs:
            r.bold = True
    for row in table["rows"]:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = _t(str(v))
    if table.get("footnote"):
        doc.add_paragraph(_t(table["footnote"])).runs[0].font.size = Pt(10)


def build_docx(ctx, draft, issues):
    """ctx: title/front, sections [(heading, kind, source)], renderer, tables, figures, bibliography."""
    r: Renderer = ctx["renderer"]
    doc = _base_document()
    if draft:
        hdr = doc.sections[0].header.paragraphs[0]
        run = hdr.add_run("ЧЕРНОВИК — НЕ ДЛЯ ПОДАЧИ / DRAFT — NOT FOR SUBMISSION")
        run.bold = True
        run.font.color.rgb = RGBColor(0xB0, 0x10, 0x30)
    front = ctx["front"]
    lang = ctx.get("lang", "en")
    ru = lang == "ru"
    doc.add_heading(_t(front.get("title")) or L.label("title_todo", lang), level=0)
    if ru:  # the title block of a Russian article is given in both languages
        doc.add_paragraph(_t(front.get("title_en")) or "[уточнить: название статьи на английском]").runs[0].bold = True
    if ctx.get("authors"):
        doc.add_paragraph(_t(", ".join(a["name"] for a in ctx["authors"])))
    if front.get("running_title"):
        doc.add_paragraph(_t(f"{L.label('running_title', lang)}: {front['running_title']}"))
    if front.get("keywords"):
        doc.add_paragraph(_t(f"{L.label('keywords', lang)}: " + "; ".join(front["keywords"])))
    if ru and front.get("keywords_en"):
        doc.add_paragraph(_t("Keywords: " + "; ".join(front["keywords_en"])))
    counts = ctx["counts"]
    if ru:
        doc.add_paragraph(f"Объём: основной текст — {counts.get('words') or '—'} слов; резюме — "
                          f"{counts.get('abstract_words') or '—'}; таблиц — {len(ctx['tables'])}; "
                          f"рисунков — {len(ctx['figures'])}")
    else:
        doc.add_paragraph(f"Word count (main text): {counts.get('words') or '—'}; abstract: "
                          f"{counts.get('abstract_words') or '—'}; tables: {len(ctx['tables'])}; "
                          f"figures: {len(ctx['figures'])}")
    if front.get("highlights"):
        doc.add_heading("Основные положения" if ru else "Highlights", level=1)
        for h in front["highlights"]:
            doc.add_paragraph(_t(h), style="List Bullet")

    kinds = [k for _, k, _ in ctx["sections"]]
    for heading, kind, source in ctx["sections"]:
        if kind == "abstract":
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        if kind == "statements":
            doc.add_heading(L.label("declarations", lang), level=1)
            _add_section(doc, None, source, r, draft, level=1, kind=kind)
            continue
        _add_section(doc, heading, source, r, draft, kind=kind)
        # the abstract (and the English abstract of a Russian article) stand on their own page
        if kind == "abstract_en" or (kind == "abstract" and "abstract_en" not in kinds):
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    doc.add_heading(L.label("references", lang), level=1)
    for e in r.bibliography:
        doc.add_paragraph(_t(e["text"]))
    for i, t in enumerate(ctx["tables"], 1):
        _add_table(doc, i, t, lang)
    if ctx["figures"]:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        doc.add_heading(L.label("legends", lang), level=1)
        for i, f in enumerate(ctx["figures"], 1):
            p = doc.add_paragraph()
            p.add_run(f"{L.label('figure', lang)} {i}. ").bold = True
            p.add_run(_t(f["caption"]))
            if ru and f.get("caption_en"):
                p_en = doc.add_paragraph()
                p_en.add_run(f"Fig. {i}. ").bold = True
                p_en.add_run(_t(f["caption_en"]))
    if draft and issues:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        doc.add_heading("Открытые вопросы (удалить перед подачей)", level=1)
        for it in issues:
            doc.add_paragraph(_t(f"[{it['severity']}] {it.get('heading') or ''}: {it['message']}"), style="List Bullet")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def markdown(ctx):
    r = ctx["renderer"]
    lang = ctx.get("lang", "en")
    out = [f"# {_t(ctx['front'].get('title')) or '[title]'}"]
    if lang == "ru" and ctx["front"].get("title_en"):
        out.append(f"**{_t(ctx['front']['title_en'])}**")
    for heading, kind, source in ctx["sections"]:
        out.append(f"## {heading}")
        for para in r.segments(source, kind):
            text = "".join(s["v"] if s["t"] != "todo" else f"[{s['v']}]" for s in para["segments"])
            out.append(("### " + text) if para["type"] == "heading" else text)
    out.append(f"## {L.label('references', lang)}")
    out += [_t(e["text"]) for e in r.bibliography]
    return "\n\n".join(out)


def package(ctx, draft, issues, run_dir, spec, results, extra_files):
    """Zip: manuscript.docx/.md, figures at journal resolution, supplementary data and script."""
    fmt = ctx["figure_format"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manuscript.docx", build_docx(ctx, draft, issues))
        z.writestr("manuscript.md", markdown(ctx))
        tmp = run_dir / "export_figures"
        tmp.mkdir(exist_ok=True)
        ru = ctx.get("lang") == "ru"
        main = ctx.get("figure_language", "en")
        # a Russian journal wants the labels on figures in both languages: "Рис-1" plus an English "Рис-1_en"
        versions = [(main, "")] + ([("en" if main == "ru" else "ru", "_" + ("en" if main == "ru" else "ru"))]
                                   if ru else [])
        for i, f in enumerate(ctx["figures"], 1):
            for flang, suffix in versions:
                name = f"Рис-{i}{suffix}.{fmt}" if ru else f"Figure_{i}.{fmt}"
                out = tmp / f"fig_{i}{suffix}.{fmt}"
                if f["kind"] == "finding":
                    path = figs.render_finding(run_dir, f["finding"], spec, out_path=out, dpi=ctx["dpi"],
                                               terms=ctx.get("terms"), lang=flang)
                else:
                    path = figs.render_overview(run_dir, f["kind"], spec, results.get("clustering"), out_path=out,
                                                dpi=ctx["dpi"], terms=ctx.get("terms"), lang=flang)
                if path:
                    from ..storage import read_bytes
                    z.writestr(f"figures/{name}", read_bytes(path))
        for name, data in extra_files.items():
            z.writestr(name, data)
    return buf.getvalue()
