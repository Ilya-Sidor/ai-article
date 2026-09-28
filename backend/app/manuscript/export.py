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
from .render import Renderer


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
        run = p.add_run(seg["v"] if seg["t"] != "todo" else f"[{seg['v']}]")
        if seg["t"] in ("todo", "unknown") or (seg["t"] == "cite" and seg.get("bad")):
            run.font.color.rgb = RGBColor(0xB0, 0x10, 0x30)
            run.bold = True
    return p


def _add_section(doc, heading, source, renderer, draft, level=1):
    if heading:
        doc.add_heading(heading, level=level)
    for para in renderer.segments(source):
        if para["type"] == "heading":
            doc.add_heading(para["segments"][0]["v"], level=level + 1)
        else:
            _add_paragraph(doc, para["segments"], draft)


def _add_table(doc, number, table):
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    cap = doc.add_paragraph()
    cap.add_run(f"Table {number}. ").bold = True
    cap.add_run(table["caption"])
    t = doc.add_table(rows=1, cols=len(table["columns"]))
    t.style = "Table Grid"
    for i, c in enumerate(table["columns"]):
        t.rows[0].cells[i].text = c
        for r in t.rows[0].cells[i].paragraphs[0].runs:
            r.bold = True
    for row in table["rows"]:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = str(v)
    if table.get("footnote"):
        doc.add_paragraph(table["footnote"]).runs[0].font.size = Pt(10)


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
    doc.add_heading(front.get("title") or "[уточнить: название статьи]", level=0)
    if ctx.get("authors"):
        doc.add_paragraph(", ".join(a["name"] for a in ctx["authors"]))
    if front.get("running_title"):
        doc.add_paragraph(f"Running title: {front['running_title']}")
    if front.get("keywords"):
        doc.add_paragraph("Keywords: " + "; ".join(front["keywords"]))
    counts = ctx["counts"]
    doc.add_paragraph(f"Word count (main text): {counts.get('words') or '—'}; abstract: "
                      f"{counts.get('abstract_words') or '—'}; tables: {len(ctx['tables'])}; "
                      f"figures: {len(ctx['figures'])}")
    if front.get("highlights"):
        doc.add_heading("Highlights", level=1)
        for h in front["highlights"]:
            doc.add_paragraph(h, style="List Bullet")

    for heading, kind, source in ctx["sections"]:
        if kind == "abstract":
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        if kind == "statements":
            doc.add_heading("Declarations", level=1)
            _add_section(doc, None, source, r, draft, level=1)
            continue
        _add_section(doc, heading, source, r, draft)
        if kind == "abstract":
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    doc.add_heading("References", level=1)
    for e in r.bibliography:
        doc.add_paragraph(e["text"])
    for i, t in enumerate(ctx["tables"], 1):
        _add_table(doc, i, t)
    if ctx["figures"]:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        doc.add_heading("Figure legends", level=1)
        for i, f in enumerate(ctx["figures"], 1):
            p = doc.add_paragraph()
            p.add_run(f"Figure {i}. ").bold = True
            p.add_run(f["caption"])
    if draft and issues:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        doc.add_heading("Открытые вопросы (удалить перед подачей)", level=1)
        for it in issues:
            doc.add_paragraph(f"[{it['severity']}] {it.get('heading') or ''}: {it['message']}", style="List Bullet")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def markdown(ctx):
    r = ctx["renderer"]
    out = [f"# {ctx['front'].get('title') or '[title]'}"]
    for heading, kind, source in ctx["sections"]:
        out.append(f"## {heading}")
        for para in r.segments(source):
            text = "".join(s["v"] if s["t"] != "todo" else f"[{s['v']}]" for s in para["segments"])
            out.append(("### " + text) if para["type"] == "heading" else text)
    out.append("## References")
    out += [e["text"] for e in r.bibliography]
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
        for i, f in enumerate(ctx["figures"], 1):
            out = tmp / f"Figure_{i}.{fmt}"
            if f["kind"] == "finding":
                path = figs.render_finding(run_dir, f["finding"], spec, out_path=out, dpi=ctx["dpi"],
                                           terms=ctx.get("terms"))
            else:
                path = figs.render_overview(run_dir, f["kind"], spec, results.get("clustering"), out_path=out,
                                            dpi=ctx["dpi"], terms=ctx.get("terms"))
            if path:
                from ..storage import read_bytes
                z.writestr(f"figures/Figure_{i}.{fmt}", read_bytes(path))
        for name, data in extra_files.items():
            z.writestr(name, data)
    return buf.getvalue()
