"""Generate Augusta Search partnership PowerPoint + one-page handout."""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from lxml import etree
import os

try:
    from docx import Document
    from docx.shared import Inches as DocInches, Pt as DocPt, RGBColor as DocRGB
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn as doc_qn
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

NAVY = RGBColor(0x0B, 0x12, 0x20)
GOLD = RGBColor(0xC9, 0xA4, 0x5C)
GOLD_DARK = RGBColor(0x9A, 0x7A, 0x35)
CREAM = RGBColor(0xFB, 0xF7, 0xEF)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
SLATE = RGBColor(0x47, 0x55, 0x69)
SLATE_DARK = RGBColor(0x1E, 0x29, 0x3B)
LIGHT_LINE = RGBColor(0xE2, 0xE8, 0xF0)
MUTED = RGBColor(0x94, 0xA3, 0xB8)
SOFT = RGBColor(0xCB, 0xD5, 0xE1)

DOCS = os.path.join(r"c:\Users\contr\Documents\GitHub\Augusta-Australia", "docs")
TOTAL = 13

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)


def set_run(run, size=18, bold=False, color=SLATE_DARK, font="Calibri"):
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def add_bg(slide, color):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    spTree = slide.shapes._spTree
    sp = shape._element
    spTree.remove(sp)
    spTree.insert(2, sp)


def add_gold_bar(slide):
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, Inches(0.08))
    bar.fill.solid()
    bar.fill.fore_color.rgb = GOLD
    bar.line.fill.background()


def add_footer(slide, page):
    box = slide.shapes.add_textbox(Inches(0.6), Inches(7.05), Inches(10), Inches(0.3))
    tf = box.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = "Augusta Search  ·  Confidential"
    set_run(run, 11, False, SLATE)
    num = slide.shapes.add_textbox(Inches(11.5), Inches(7.05), Inches(1.3), Inches(0.3))
    ntf = num.text_frame
    ntf.clear()
    np = ntf.paragraphs[0]
    np.alignment = PP_ALIGN.RIGHT
    nr = np.add_run()
    nr.text = f"{page} / {TOTAL}"
    set_run(nr, 11, False, SLATE)


def title_block(slide, eyebrow, title, subtitle=None):
    eb = slide.shapes.add_textbox(Inches(0.7), Inches(0.45), Inches(11.5), Inches(0.35))
    tf = eb.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = eyebrow.upper()
    set_run(r, 12, True, GOLD_DARK)
    tb = slide.shapes.add_textbox(Inches(0.7), Inches(0.85), Inches(11.8), Inches(1.0))
    tf = tb.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = title
    set_run(r, 36, True, NAVY)
    if subtitle:
        sb = slide.shapes.add_textbox(Inches(0.7), Inches(1.7), Inches(11.5), Inches(0.5))
        tf = sb.text_frame
        tf.clear()
        p = tf.paragraphs[0]
        r = p.add_run()
        r.text = subtitle
        set_run(r, 16, False, SLATE)


def bullets(slide, items, left=0.7, top=2.4, width=11.5, size=18):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(4.2))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.level = 0
        p.space_after = Pt(12)
        r = p.add_run()
        r.text = "•  " + item
        set_run(r, size, False, SLATE_DARK)


def card(slide, left, top, width, height, title, body_lines):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height)
    )
    shape.adjustments[0] = 0.08
    shape.fill.solid()
    shape.fill.fore_color.rgb = WHITE
    shape.line.color.rgb = LIGHT_LINE
    strip = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(0.08), Inches(height)
    )
    strip.fill.solid()
    strip.fill.fore_color.rgb = GOLD
    strip.line.fill.background()
    tb = slide.shapes.add_textbox(Inches(left + 0.25), Inches(top + 0.2), Inches(width - 0.4), Inches(0.4))
    tf = tb.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = title
    set_run(r, 16, True, NAVY)
    bb = slide.shapes.add_textbox(
        Inches(left + 0.25), Inches(top + 0.65), Inches(width - 0.4), Inches(height - 0.85)
    )
    tf = bb.text_frame
    tf.clear()
    tf.word_wrap = True
    for i, line in enumerate(body_lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(6)
        r = p.add_run()
        r.text = line
        set_run(r, 14, False, SLATE)


# ---------------------------------------------------------------------------
# Slide 1 — Cover
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, NAVY)
acc = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.18), prs.slide_height)
acc.fill.solid()
acc.fill.fore_color.rgb = GOLD
acc.line.fill.background()
box = s.shapes.add_textbox(Inches(0.9), Inches(1.9), Inches(11), Inches(0.4))
tf = box.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "PARTNERSHIP DISCUSSION"
set_run(r, 14, True, GOLD)
box = s.shapes.add_textbox(Inches(0.9), Inches(2.4), Inches(11.5), Inches(1.1))
tf = box.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Augusta Search"
set_run(r, 48, True, WHITE)
box = s.shapes.add_textbox(Inches(0.9), Inches(3.55), Inches(11.2), Inches(0.9))
tf = box.text_frame
tf.clear()
tf.word_wrap = True
p = tf.paragraphs[0]
r = p.add_run()
r.text = "AI Powered Building Compliance and Standards Intelligence"
set_run(r, 22, False, SOFT)
box = s.shapes.add_textbox(Inches(0.9), Inches(5.1), Inches(11), Inches(0.9))
tf = box.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Presented by the Founder"
set_run(r, 18, False, GOLD)
p = tf.add_paragraph()
r = p.add_run()
r.text = "Confidential"
set_run(r, 12, False, MUTED)

# ---------------------------------------------------------------------------
# Slide 2 — The problem
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 2)
title_block(s, "The problem", "Compliance is getting harder")
card(s, 0.7, 2.4, 3.8, 3.6, "Thousands of pages", [
    "NCC, AS/NZS, and related codes",
    "Dense PDFs and clause hunting",
    "Cross-references across documents",
])
card(s, 4.75, 2.4, 3.8, 3.6, "Time sinks", [
    "Hours lost searching every week",
    "Senior time spent on basics",
    "Slow answers under project pressure",
])
card(s, 8.8, 2.4, 3.8, 3.6, "Risk of missing something", [
    "Wrong clearance or fire call",
    "Costly rework and delays",
    "Compliance exposure for the firm",
])

# ---------------------------------------------------------------------------
# Slide 3 — Why this matters
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 3)
title_block(s, "Why this matters", "Every engineering firm has the same pain")
bullets(
    s,
    [
        "Lost hours — practitioners burn time hunting clauses instead of designing",
        "Higher costs — inefficiency shows up in project delivery and overhead",
        "Compliance risk — missing a requirement can mean rework, liability, or delay",
        "This is not a niche issue — it sits at the centre of day-to-day professional work",
    ],
    top=2.4,
    size=20,
)

# ---------------------------------------------------------------------------
# Slide 4 — Solution
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 4)
title_block(s, "The solution", "Introducing Augusta Search")
box = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.7), Inches(2.4), Inches(11.9), Inches(2.0))
box.adjustments[0] = 0.06
box.fill.solid()
box.fill.fore_color.rgb = NAVY
box.line.fill.background()
tb = s.shapes.add_textbox(Inches(1.1), Inches(2.9), Inches(11.1), Inches(1.2))
tf = tb.text_frame
tf.word_wrap = True
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Ask a question in plain English — get cited answers in seconds."
set_run(r, 26, False, WHITE)
bullets(
    s,
    [
        "Answers grounded in the selected standard or document — not a generic chatbot",
        "Built for Australian building compliance and standards workflows",
        "Evidence you can check — so professionals can move faster with confidence",
    ],
    top=4.8,
    size=17,
)

# ---------------------------------------------------------------------------
# Slide 5 — Live demo
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 5)
title_block(s, "Live demo", "Show, don't tell")
# Optional subtitle for the comparison framing
sub = s.shapes.add_textbox(Inches(0.7), Inches(1.75), Inches(11.5), Inches(0.4))
tf = sub.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Same questions — Augusta Search first, then ChatGPT"
set_run(r, 15, False, SLATE)

steps = [
    ("01", "Augusta Search", "Ask a real standards question — cited answer"),
    ("02", "Augusta Search", "Second example — grounded in the source"),
    ("03", "ChatGPT", "Ask the same question — compare the answer"),
    ("04", "ChatGPT", "Second comparison — notice the difference"),
]
for i, (num, title, body) in enumerate(steps):
    left = 0.7 + i * 3.1
    is_augusta = i < 2
    shape = s.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(2.35), Inches(2.9), Inches(3.65)
    )
    shape.adjustments[0] = 0.08
    shape.fill.solid()
    shape.fill.fore_color.rgb = NAVY if is_augusta else WHITE
    shape.line.color.rgb = GOLD if is_augusta else LIGHT_LINE
    title_c = WHITE if is_augusta else NAVY
    body_c = SOFT if is_augusta else SLATE
    num_c = GOLD if is_augusta else MUTED
    nb = s.shapes.add_textbox(Inches(left + 0.2), Inches(2.6), Inches(2.5), Inches(0.5))
    tf = nb.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = num
    set_run(r, 28, True, num_c)
    tb = s.shapes.add_textbox(Inches(left + 0.2), Inches(3.35), Inches(2.5), Inches(1.0))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = title
    set_run(r, 16, True, title_c)
    bb = s.shapes.add_textbox(Inches(left + 0.2), Inches(4.4), Inches(2.5), Inches(1.3))
    tf = bb.text_frame
    tf.word_wrap = True
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = body
    set_run(r, 13, False, body_c)

# ---------------------------------------------------------------------------
# Slide 6 — Demo questions
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 6)
title_block(s, "Live demo", "Questions we will run")
sub = s.shapes.add_textbox(Inches(0.7), Inches(1.75), Inches(11.5), Inches(0.35))
tf = sub.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Run in Augusta Search first — then the same questions in ChatGPT"
set_run(r, 15, False, SLATE)

demo_qs = [
    ("AS/NZS 3000", "When is RCD protection required for final subcircuits?"),
    ("AS 1670.1", "What is the maximum spacing between smoke detectors on a flat ceiling?"),
    ("NCC Volume 1", "What are the accessible toilet requirements for a Class 6 building?"),
    ("NCC Volume 3", "When is backflow prevention required?"),
]
for i, (std, q) in enumerate(demo_qs):
    row = i // 2
    col = i % 2
    left = 0.7 + col * 6.2
    top = 2.25 + row * 1.7
    shape = s.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(top), Inches(5.9), Inches(1.5)
    )
    shape.adjustments[0] = 0.08
    shape.fill.solid()
    shape.fill.fore_color.rgb = WHITE
    shape.line.color.rgb = LIGHT_LINE
    strip = s.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(0.08), Inches(1.5)
    )
    strip.fill.solid()
    strip.fill.fore_color.rgb = GOLD
    strip.line.fill.background()
    tb = s.shapes.add_textbox(Inches(left + 0.3), Inches(top + 0.2), Inches(5.4), Inches(0.35))
    tf = tb.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = std
    set_run(r, 13, True, GOLD_DARK)
    qb = s.shapes.add_textbox(Inches(left + 0.3), Inches(top + 0.55), Inches(5.4), Inches(0.8))
    tf = qb.text_frame
    tf.word_wrap = True
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = f'"{q}"'
    set_run(r, 14, False, SLATE_DARK)

# Bonus strip
bonus = s.shapes.add_shape(
    MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.7), Inches(5.75), Inches(11.9), Inches(0.95)
)
bonus.adjustments[0] = 0.08
bonus.fill.solid()
bonus.fill.fore_color.rgb = NAVY
bonus.line.fill.background()
tb = s.shapes.add_textbox(Inches(1.0), Inches(5.9), Inches(11.3), Inches(0.7))
tf = tb.text_frame
tf.word_wrap = True
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Bonus  ·  "
set_run(r, 14, True, GOLD)
r = p.add_run()
r.text = '"Show me every clause related to emergency lighting in a Class 9a hospital."'
set_run(r, 14, False, WHITE)

# ---------------------------------------------------------------------------
# Slide 7 — Market
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 7)
title_block(s, "Market", "Who uses it?")
box = s.shapes.add_textbox(Inches(0.7), Inches(1.75), Inches(11.5), Inches(0.4))
tf = box.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Built for practising engineers — discipline by discipline"
set_run(r, 15, False, SLATE)

disciplines = [
    ("Electrical", "AS/NZS wiring, switchboards, cable & earthing standards"),
    ("Fire", "Detection, suppression, egress and fire-safety codes"),
    ("Mechanical", "HVAC, plant, and mechanical services standards"),
    ("Hydraulic", "Plumbing, drainage, and hydraulic design codes"),
]
for i, (title, body) in enumerate(disciplines):
    left = 0.55 + i * 3.15
    card(s, left, 2.4, 3.0, 3.8, title, [body])

# ---------------------------------------------------------------------------
# Slide 8 — Why us
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 8)
title_block(s, "Why us", "Purpose-built for standards")
bullets(
    s,
    [
        "Purpose-built for standards and compliance workflows — not a general AI wrapper",
        "Faster retrieval of relevant clauses and context",
        "Focused on helping professionals find the right information quickly",
        "Designed around how engineers and related professions actually work",
    ],
    top=2.4,
    size=20,
)

# ---------------------------------------------------------------------------
# Slide 9 — Business model
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 9)
title_block(s, "Business model", "Subscription SaaS")
plans = [
    ("Sole", "Free", "NCC + SIR entry", "Awareness / trial"),
    ("Professional", "$49 / mo", "Full disciplines + uploads", "Individual practitioners"),
    ("Company Small", "$599 / mo", "Up to 25 seats", "SME firms"),
    ("Company Large", "$1,099 / mo", "Up to 50 seats", "Larger teams"),
]
for i, (name, price, feat, who) in enumerate(plans):
    left = 0.55 + i * 3.15
    shape = s.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(2.35), Inches(3.0), Inches(3.7)
    )
    shape.adjustments[0] = 0.08
    highlight = i in (1, 2)
    shape.fill.solid()
    shape.fill.fore_color.rgb = NAVY if highlight else WHITE
    shape.line.color.rgb = GOLD if highlight else LIGHT_LINE
    title_c = WHITE if highlight else NAVY
    body_c = SOFT if highlight else SLATE
    price_c = GOLD if highlight else GOLD_DARK
    tb = s.shapes.add_textbox(Inches(left + 0.2), Inches(2.55), Inches(2.6), Inches(0.4))
    tf = tb.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = name
    set_run(r, 16, True, title_c)
    pb = s.shapes.add_textbox(Inches(left + 0.2), Inches(3.1), Inches(2.6), Inches(0.55))
    tf = pb.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = price
    set_run(r, 22, True, price_c)
    fb = s.shapes.add_textbox(Inches(left + 0.2), Inches(3.9), Inches(2.6), Inches(1.0))
    tf = fb.text_frame
    tf.word_wrap = True
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = feat
    set_run(r, 14, False, body_c)
    wb = s.shapes.add_textbox(Inches(left + 0.2), Inches(5.2), Inches(2.6), Inches(0.6))
    tf = wb.text_frame
    tf.word_wrap = True
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = who
    set_run(r, 13, True, body_c)

note = s.shapes.add_textbox(Inches(0.7), Inches(6.3), Inches(11.5), Inches(0.4))
tf = note.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Monthly plans  ·  Recurring revenue  ·  Company seats for teams"
set_run(r, 14, False, SLATE)

# ---------------------------------------------------------------------------
# Slide 10 — Price comparison
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 10)
title_block(s, "Price comparison", "Same seat count — general AI vs Augusta")
sub = s.shapes.add_textbox(Inches(0.7), Inches(1.75), Inches(11.5), Inches(0.35))
tf = sub.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Indicative monthly cost for 25 and 50 seats (published list rates where available)"
set_run(r, 14, False, SLATE)

# Table header row
headers = ["", "25 seats / mo", "50 seats / mo", "Notes"]
cols = [0.7, 4.0, 7.0, 10.0]
widths = [3.1, 2.8, 2.8, 2.6]
header_bar = s.shapes.add_shape(
    MSO_SHAPE.RECTANGLE, Inches(0.7), Inches(2.25), Inches(11.9), Inches(0.55)
)
header_bar.fill.solid()
header_bar.fill.fore_color.rgb = NAVY
header_bar.line.fill.background()
for j, h in enumerate(headers):
    tb = s.shapes.add_textbox(Inches(cols[j]), Inches(2.35), Inches(widths[j]), Inches(0.4))
    tf = tb.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = h
    set_run(r, 13, True, WHITE if j else GOLD)

rows = [
    ("Augusta Search", "$599", "$1,099", "Purpose-built standards", True),
    ("ChatGPT Business", "$625", "$1,250", "$25 / seat (monthly)", False),
    ("Claude Enterprise", "$500+", "$1,000+", "$20 / seat + usage", False),
    ("ChatGPT Enterprise", "~$1,500*", "~$3,000*", "~$60 / seat · quote-only*", False),
]
for i, (name, c25, c50, note_txt, highlight) in enumerate(rows):
    top = 2.85 + i * 0.75
    row_bg = s.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0.7), Inches(top), Inches(11.9), Inches(0.7)
    )
    row_bg.fill.solid()
    row_bg.fill.fore_color.rgb = NAVY if highlight else WHITE
    row_bg.line.color.rgb = GOLD if highlight else LIGHT_LINE
    vals = [name, c25, c50, note_txt]
    for j, val in enumerate(vals):
        tb = s.shapes.add_textbox(Inches(cols[j] + 0.1), Inches(top + 0.18), Inches(widths[j] - 0.15), Inches(0.4))
        tf = tb.text_frame
        tf.clear()
        p = tf.paragraphs[0]
        r = p.add_run()
        r.text = val
        color = GOLD if highlight and j > 0 else (WHITE if highlight else SLATE_DARK)
        if j == 0 and highlight:
            color = WHITE
        set_run(r, 14, True if j < 3 else False, color)

foot = s.shapes.add_textbox(Inches(0.7), Inches(6.05), Inches(11.9), Inches(0.7))
tf = foot.text_frame
tf.word_wrap = True
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "* ChatGPT Enterprise is quote-only (often ~$45–$75/seat; ~$60 typical) and commonly quoted with large seat minimums — so Business is the realistic peer for 25–50 seats. Claude seat fee excludes token usage."
set_run(r, 11, False, SLATE)
p = tf.add_paragraph()
r = p.add_run()
r.text = "Augusta includes cited standards Q&A for engineering teams — not a general chatbot seat."
set_run(r, 12, True, GOLD_DARK)

# ---------------------------------------------------------------------------
# Slide 11 — Why a marketing partner
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 11)
title_block(s, "Why a marketing partner", "Tech is built — now focus is growth")
card(
    s,
    0.7,
    2.4,
    5.9,
    4.0,
    "What we need",
    [
        "Drive awareness in the market",
        "Open partnerships and introductions",
        "Build recurring revenue with firms",
        "A credible face for industry relationships",
    ],
)
card(
    s,
    6.9,
    2.4,
    5.7,
    4.0,
    "What is already in place",
    [
        "Product live and working",
        "Subscription billing",
        "Company seats & shared docs",
        "Referral attribution for partners",
    ],
)

# ---------------------------------------------------------------------------
# Slide 12 — Why join now
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, CREAM)
add_gold_bar(s)
add_footer(s, 12)
title_block(s, "Why join now", "Ground-floor opportunity")
bullets(
    s,
    [
        "Early stage — shape the growth story while the market is still open",
        "Influence positioning, partnerships, and go-to-market",
        "Shared upside — aligned around building something valuable together",
        "Not joining a finished company to push a finished product — joining to grow it",
    ],
    top=2.4,
    size=20,
)

# ---------------------------------------------------------------------------
# Slide 12 — Discussion
# ---------------------------------------------------------------------------
s = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(s, NAVY)
acc = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.18), prs.slide_height)
acc.fill.solid()
acc.fill.fore_color.rgb = GOLD
acc.line.fill.background()
box = s.shapes.add_textbox(Inches(0.9), Inches(1.9), Inches(11), Inches(0.4))
tf = box.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "DISCUSSION"
set_run(r, 14, True, GOLD)
box = s.shapes.add_textbox(Inches(0.9), Inches(2.5), Inches(11.5), Inches(1.0))
tf = box.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Your feedback, ideas, and questions"
set_run(r, 36, True, WHITE)
box = s.shapes.add_textbox(Inches(0.9), Inches(3.8), Inches(11), Inches(1.6))
tf = box.text_frame
tf.word_wrap = True
tf.clear()
for i, line in enumerate([
    "What resonates — and what doesn’t",
    "Where you see the strongest path to market",
    "How we might work together",
]):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    p.space_after = Pt(8)
    r = p.add_run()
    r.text = "•  " + line
    set_run(r, 18, False, SOFT)
box = s.shapes.add_textbox(Inches(0.9), Inches(6.2), Inches(11), Inches(0.5))
tf = box.text_frame
tf.clear()
p = tf.paragraphs[0]
r = p.add_run()
r.text = "Augusta Search  ·  ausstd.augustasearch.com"
set_run(r, 14, False, MUTED)

out_pptx = os.path.join(DOCS, "Augusta_Search_Partnership_Presentation.pptx")
os.makedirs(DOCS, exist_ok=True)
prs.save(out_pptx)
print(out_pptx)


# ---------------------------------------------------------------------------
# One-page handout (Word)
# ---------------------------------------------------------------------------
def build_handout():
    if not HAS_DOCX:
        print("python-docx not installed — skipping handout. Run: pip install python-docx")
        return

    doc = Document()
    section = doc.sections[0]
    section.page_width = DocInches(8.5)
    section.page_height = DocInches(11)
    section.left_margin = DocInches(0.75)
    section.right_margin = DocInches(0.75)
    section.top_margin = DocInches(0.6)
    section.bottom_margin = DocInches(0.6)

    def set_run_doc(run, size=11, bold=False, color=(30, 41, 59), font="Calibri"):
        run.font.name = font
        run._element.rPr.rFonts.set(doc_qn("w:eastAsia"), font)
        run.font.size = DocPt(size)
        run.font.bold = bold
        run.font.color.rgb = DocRGB(*color)

    def add_heading_line(text, size=22, color=(11, 18, 32)):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = DocPt(0)
        p.paragraph_format.space_after = DocPt(2)
        r = p.add_run(text)
        set_run_doc(r, size, True, color)
        return p

    def add_sub(text, size=12, color=(154, 122, 53)):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = DocPt(0)
        p.paragraph_format.space_after = DocPt(8)
        r = p.add_run(text)
        set_run_doc(r, size, True, color)
        return p

    def add_section(title):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = DocPt(10)
        p.paragraph_format.space_after = DocPt(2)
        r = p.add_run(title.upper())
        set_run_doc(r, 10, True, (154, 122, 53))

    def add_bullets(items, size=10):
        for item in items:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = DocPt(0)
            p.paragraph_format.space_after = DocPt(1)
            p.paragraph_format.left_indent = DocInches(0.15)
            r = p.add_run("•  " + item)
            set_run_doc(r, size, False, (30, 41, 59))

    add_heading_line("Augusta Search")
    add_sub("AI Powered Building Compliance and Standards Intelligence")
    p = doc.add_paragraph()
    p.paragraph_format.space_after = DocPt(4)
    r = p.add_run("One-page overview  ·  Partnership discussion  ·  Confidential")
    set_run_doc(r, 9, False, (71, 85, 105))

    add_section("The problem")
    add_bullets([
        "Compliance is getting harder — thousands of pages, cross-references, time sinks",
        "Risk of missing something in dense standards work",
        "Every engineering firm feels the same pain: lost hours, higher costs, compliance risk",
    ])

    add_section("The solution")
    add_bullets([
        "Augusta Search — ask in plain English, get cited answers in seconds",
        "Purpose-built for standards and compliance workflows",
        "Faster retrieval so professionals find relevant information quickly",
    ])

    add_section("Who uses it")
    add_bullets([
        "Practising engineers — electrical, fire, mechanical, and hydraulic",
        "Focused on engineering compliance and standards workflows",
    ])

    add_section("Business model")
    add_bullets([
        "Subscription SaaS with monthly plans and recurring revenue",
        "Sole (free) · Professional ($49/mo) · Company Small ($599/mo, ≤25 seats) · Company Large ($1,099/mo, ≤50 seats)",
        "Vs ChatGPT Business (~$625 / $1,250 for 25 / 50 seats) or Claude Enterprise seats ($500+ / $1,000+ plus usage)",
    ])

    add_section("Why a marketing partner — and why now")
    add_bullets([
        "Tech is built — focus shifts to growth: awareness, partnerships, recurring revenue",
        "Early-stage, ground-floor opportunity to shape growth with shared upside",
    ])

    add_section("Discussion")
    add_bullets([
        "Your feedback, ideas, and questions",
        "ausstd.augustasearch.com",
    ])

    out_docx = os.path.join(DOCS, "Augusta_Search_Partnership_Handout.docx")
    doc.save(out_docx)
    print(out_docx)


build_handout()
