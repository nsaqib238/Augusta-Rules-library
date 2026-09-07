# Augusta Search — quarter page print ad

## File

`augusta-search-ad-quarter-page.html`

## Spec (from National Emergency Response media kit)

| Item | Value |
|------|--------|
| Size | **93 × 131 mm** (trim) |
| Bleed | **None** |
| Format to send | **PDF** |

## Export to PDF (Windows)

1. Open `docs/marketing/augusta-search-ad-quarter-page.html` in **Chrome** or **Edge** (needs internet once so the QR code loads).
2. Wait ~1 second for the QR square to appear bottom-right.
3. **Ctrl+P** → Destination: **Save as PDF**
4. **More settings:**
   - Paper size: **Custom** → Width **93 mm**, Height **131 mm**  
     *(If custom size is awkward, use A4 and set Scale so the ad box fills exactly — see below.)*
   - Margins: **None**
   - Scale: **100%**
   - Enable **Background graphics**
5. Save as: `AugustaSearch_QuarterPage_NER.pdf`

### If Chrome won’t accept 93 × 131 mm custom paper

1. Print to PDF on **A4** with **Background graphics** on.
2. Open the PDF in **Adobe Acrobat**, **Foxit**, or an online crop tool.
3. Crop to exactly **93 × 131 mm** with no bleed.

Or ask the publisher: *“We have a 93×131 mm PDF — can you place as-is?”* (Usually yes.)

## What to email the publisher

**Subject:** Augusta Search — quarter page artwork

**Body:**

> Please find attached our quarter-page advertisement (93 × 131 mm, no bleed) for [issue name / date].  
> Advertiser: Augusta Search · https://augustasearch.com  
> Contact: [your name, email, phone]

**Attachment:** `AugustaSearch_QuarterPage_NER.pdf`

## Still ask them on the call

- Do you prefer **CMYK** PDF or is RGB OK?
- Should fonts be **outlined** / embedded?
- Exact **artwork deadline** for your chosen issue

## Logo

Uses `NewOfficetools.png` in the same folder (`docs/marketing/`). Keep HTML + PNG together when moving files.

## Edit copy

Open the HTML file in any text editor. Main blocks:

- `.eyebrow` — top gold line
- `h1` — headline
- `.sub` — one-sentence pitch
- `ul li` — three bullets
- `.cta-url` — website (currently `augustasearch.com`)
