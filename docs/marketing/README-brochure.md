# Augusta Search — 6-page marketing brochure

## File

`augusta-search-brochure-6pp.html`

## Contents (6 pages)

| Page | Topic |
|------|--------|
| 1 | Cover — value proposition |
| 2 | Why Augusta vs generic AI |
| 3 | Modules & features (Q&A, NCC, SIR, export, save, subscriptions) |
| 4 | Sample: AS/NZS 3000 — mixed circuits |
| 5 | Sample: NCC 2022 — emergency lighting Class 6 |
| 6 | Sample: Victorian SIR 2025 + contact / CTA |

Sample Q&A text is taken from your exports in `CSV/Q&A/*.pdf`.

## Print to PDF (Windows)

1. Open `augusta-search-brochure-6pp.html` in **Chrome** or **Edge**.
2. **Ctrl+P** → Destination: **Save as PDF**.
3. Paper: **A4**, Margins: **Default**, Scale: **100%**.
4. Enable **Background graphics**.
5. Save as `Augusta-Search-Brochure.pdf`.

## Add real screenshots

To use actual UI screenshots from your exports:

1. Open each PDF in `CSV/Q&A/` and export page 1 as PNG (or screenshot the app).
2. Save images to `docs/marketing/images/` (e.g. `as3000-sample.png`).
3. In the HTML, replace a sample block with:

```html
<img src="images/as3000-sample.png" alt="AS3000 Q&A sample" style="width:100%; border-radius:8px; border:1px solid #e2e8f0;" />
```

## Logo

The brochure uses your brand file:

`NewOfficetools.png` in the same folder as the HTML (`docs/marketing/`).

Open `docs/marketing/augusta-search-brochure-6pp.html` in Chrome so the image loads. Cover logo width is **78mm** (~full banner width on A4).

To change size, edit the `width: 78mm` on the cover `<img>` in the HTML.
