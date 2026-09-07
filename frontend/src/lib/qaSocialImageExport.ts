/**
 * Social / Facebook JPG export — branded Q&A card (1200px wide, JPEG).
 * Layout inspired by docs/ChatGPT Image Jun 4, 2026, 09_10_22 AM.png
 */

import { toJpeg } from 'html-to-image';
import { headerBrandLogoUrl } from './brandLogo';
import {
  QaExportPayload,
  QaExportProfile,
  PROFILE_META,
  resolveProfile,
  formatExportDate,
} from './qaAnswerExport';

const CARD_WIDTH = 1200;
const JPEG_QUALITY = 0.92;

const SECTION_HEADERS = [
  'practitioner summary',
  'what the cited clauses require',
  'exceptions, notes, and edge cases',
  'required actions or sequencing',
  'confirm on site or in design records',
  'citations',
  'you might also ask',
] as const;

type SectionKey = (typeof SECTION_HEADERS)[number] | '_other';

interface ParsedSections {
  practitionerSummary: string;
  outcome: string;
  clauseRequirements: string[];
  exceptions: string[];
  requiredActions: string[];
  confirmOnSite: string[];
  citations: Array<{ ref: string; detail: string }>;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function normalizeHeader(line: string): string {
  return line
    .replace(/\*\*/g, '')
    .trim()
    .toLowerCase()
    .replace(/:$/, '');
}

function parseAnswerSections(answerMarkdown: string): ParsedSections {
  const lines = (answerMarkdown || '').split('\n');
  const buckets = new Map<SectionKey, string[]>();
  let current: SectionKey = '_other';

  const pushLine = (line: string) => {
    if (!buckets.has(current)) buckets.set(current, []);
    buckets.get(current)!.push(line);
  };

  for (const raw of lines) {
    const normalized = normalizeHeader(raw);
    const matched = SECTION_HEADERS.find((h) => normalized === h || normalized.startsWith(h));
    if (matched) {
      current = matched;
      continue;
    }
    pushLine(raw);
  }

  const textOf = (key: SectionKey) => (buckets.get(key) || []).join('\n').trim();

  const practitionerSummary = textOf('practitioner summary') || textOf('_other');
  const outcomeMatch = practitionerSummary.match(/Outcome:\s*(.+?)(?:\n|$)/i);
  const outcome = outcomeMatch?.[1]?.trim() || '';
  const summaryBody = practitionerSummary
    .replace(/Outcome:\s*.+?(?:\n|$)/i, '')
    .replace(/\*\*Outcome\*\*:?\s*.+?(?:\n|$)/i, '')
    .trim();

  const parseBullets = (block: string, max = 6): string[] =>
    block
      .split('\n')
      .map((l) => l.replace(/^\s*[-•*]\s+/, '').replace(/^\d+\.\s+/, '').trim())
      .filter(Boolean)
      .slice(0, max);

  const parseCitations = (block: string, max = 8): Array<{ ref: string; detail: string }> => {
    const out: Array<{ ref: string; detail: string }> = [];
    for (const line of block.split('\n')) {
      const cleaned = line.replace(/^\s*[-•*]\s+/, '').trim();
      if (!cleaned) continue;
      const m = cleaned.match(/^([A-Za-z0-9][A-Za-z0-9._\s]{0,24}?)\s*[-–—]\s*(.+)$/);
      if (m) {
        out.push({ ref: m[1].trim(), detail: m[2].trim() });
      } else if (/^[A-Za-z0-9][A-Za-z0-9._]+$/.test(cleaned.split(/\s+/)[0])) {
        const [ref, ...rest] = cleaned.split(/\s+/);
        out.push({ ref, detail: rest.join(' ') });
      }
      if (out.length >= max) break;
    }
    return out;
  };

  return {
    practitionerSummary: summaryBody,
    outcome,
    clauseRequirements: parseBullets(textOf('what the cited clauses require')),
    exceptions: parseBullets(textOf('exceptions, notes, and edge cases'), 4),
    requiredActions: parseBullets(textOf('required actions or sequencing'), 5),
    confirmOnSite: parseBullets(textOf('confirm on site or in design records'), 5),
    citations: parseCitations(textOf('citations')),
  };
}

function truncateText(text: string, maxLen: number): string {
  const t = text.replace(/\s+/g, ' ').trim();
  if (t.length <= maxLen) return t;
  return `${t.slice(0, maxLen - 1).trimEnd()}…`;
}

function bulletsHtml(items: string[], checkColor = '#16a34a'): string {
  if (!items.length) return '<p class="muted">—</p>';
  return `<ul class="bullets">${items
    .map(
      (item) =>
        `<li><span class="check" style="color:${checkColor}">✓</span>${escapeHtml(truncateText(item, 220))}</li>`,
    )
    .join('')}</ul>`;
}

function citationCardsHtml(citations: Array<{ ref: string; detail: string }>, sources?: QaExportPayload['sources']): string {
  const cards: Array<{ ref: string; detail: string }> = [...citations];
  if (sources?.length) {
    for (const s of sources.slice(0, 8)) {
      const ref = s.clause_number || s.text || '';
      if (!ref || cards.some((c) => c.ref === ref)) continue;
      cards.push({ ref: String(ref), detail: s.text && s.text !== ref ? truncateText(s.text, 80) : '' });
    }
  }
  if (!cards.length) return '<p class="muted">See full answer in Augusta Search.</p>';
  return `<div class="citation-cards">${cards
    .slice(0, 8)
    .map(
      (c) =>
        `<div class="citation-card"><span class="citation-ref">${escapeHtml(c.ref)}</span>${
          c.detail ? `<span class="citation-detail">${escapeHtml(truncateText(c.detail, 100))}</span>` : ''
        }</div>`,
    )
    .join('')}</div>`;
}

function profileFooterNote(_profile: QaExportProfile): string {
  return 'Information is evidence-bound from the cited clauses only. | Use professional judgment. | Always verify against official sources.';
}

function buildSocialCardHtml(payload: QaExportPayload, logoUrl: string): string {
  const profile = resolveProfile(payload);
  const profileMeta = PROFILE_META[profile];
  const title = payload.productTitle || profileMeta.defaultTitle;
  const sections = parseAnswerSections(payload.answerMarkdown);

  const metaParts: string[] = [`Exported ${formatExportDate(payload.answeredAt)}`];
  if (payload.confidence != null && payload.confidence > 0) {
    metaParts.push(`Confidence ${(payload.confidence * 100).toFixed(1)}%`);
  }
  if (payload.retrievalNote) {
    metaParts.push(truncateText(payload.retrievalNote, 80));
  }

  const exceptionsBlock =
    sections.exceptions.length > 0
      ? `<div class="subsection"><h4>Exceptions &amp; edge cases</h4>${bulletsHtml(sections.exceptions, '#d97706')}</div>`
      : '';

  const outcomeBlock = sections.outcome
    ? `<div class="outcome-box"><span class="outcome-icon">ℹ</span><div><strong>Outcome</strong><p>${escapeHtml(truncateText(sections.outcome, 280))}</p></div></div>`
    : '';

  return `<div class="social-card" xmlns="http://www.w3.org/1999/xhtml">
<style>
  .social-card {
    width: ${CARD_WIDTH}px;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    background: #f1f5f9;
    color: #0f172a;
    line-height: 1.45;
    box-sizing: border-box;
  }
  .social-card * { box-sizing: border-box; }
  .header {
    background: linear-gradient(135deg, #0a1628 0%, #1e293b 100%);
    color: #fff;
    padding: 28px 32px 22px;
    display: flex;
    gap: 20px;
    align-items: flex-start;
  }
  .logo-wrap {
    background: #fff;
    border-radius: 12px;
    padding: 10px 14px;
    flex-shrink: 0;
  }
  .logo-wrap img { display: block; height: 52px; width: auto; }
  .header-text h1 {
    margin: 0 0 6px;
    font-size: 26px;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  .header-text .doc {
    margin: 0 0 10px;
    font-size: 15px;
    color: #cbd5e1;
    font-weight: 500;
  }
  .meta-row {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    font-size: 12px;
    color: #94a3b8;
  }
  .meta-item { display: flex; align-items: center; gap: 6px; }
  .question-box {
    margin: 0;
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%);
    border-left: 6px solid #eab308;
    padding: 22px 32px 24px;
    display: flex;
    gap: 18px;
    align-items: flex-start;
  }
  .q-icon {
    width: 44px; height: 44px;
    border-radius: 50%;
    background: #eab308;
    color: #0f172a;
    font-size: 26px;
    font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  .q-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.14em;
    color: #eab308;
    margin-bottom: 8px;
  }
  .q-text {
    font-size: 20px;
    font-weight: 600;
    color: #fff;
    line-height: 1.35;
  }
  .body-grid {
    display: grid;
    grid-template-columns: 1fr 340px;
    gap: 0;
    background: #fff;
  }
  .answer-col { padding: 24px 28px 28px 32px; border-right: 1px solid #e2e8f0; }
  .sidebar { padding: 24px 24px 28px 20px; background: #f8fafc; }
  .section-label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: #16a34a;
    margin-bottom: 14px;
  }
  .section-label.answer { color: #16a34a; }
  .summary {
    font-size: 14px;
    color: #334155;
    margin: 0 0 18px;
    line-height: 1.55;
  }
  .subsection h4 {
    margin: 0 0 8px;
    font-size: 13px;
    font-weight: 700;
    color: #0f172a;
  }
  .bullets { list-style: none; margin: 0 0 16px; padding: 0; }
  .bullets li {
    display: flex;
    gap: 8px;
    font-size: 13px;
    color: #334155;
    margin-bottom: 8px;
    line-height: 1.45;
  }
  .check { font-weight: 700; flex-shrink: 0; }
  .outcome-box {
    display: flex;
    gap: 12px;
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    border-radius: 10px;
    padding: 14px 16px;
    margin-top: 8px;
  }
  .outcome-icon {
    width: 28px; height: 28px;
    border-radius: 50%;
    background: #2563eb;
    color: #fff;
    font-size: 16px;
    font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  .outcome-box strong { display: block; font-size: 13px; margin-bottom: 4px; color: #1e40af; }
  .outcome-box p { margin: 0; font-size: 13px; color: #334155; }
  .sidebar-block {
    margin-bottom: 22px;
    padding-bottom: 18px;
    border-bottom: 1px solid #e2e8f0;
  }
  .sidebar-block:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
  .sidebar-block h3 {
    margin: 0 0 10px;
    font-size: 13px;
    font-weight: 700;
    color: #1e40af;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .sidebar-icon { font-size: 16px; }
  .citation-cards { display: flex; flex-direction: column; gap: 8px; }
  .citation-card {
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 12px;
  }
  .citation-ref {
    display: inline-block;
    background: #0f172a;
    color: #fff;
    font-weight: 700;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
    margin-bottom: 4px;
  }
  .citation-detail { display: block; color: #64748b; line-height: 1.35; }
  .muted { font-size: 12px; color: #94a3b8; margin: 0; }
  .footer-bar {
    background: #0a1628;
    color: #94a3b8;
    font-size: 11px;
    padding: 12px 32px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .footer-shield { color: #60a5fa; font-size: 14px; }
</style>

<header class="header">
  <div class="logo-wrap">
    <img src="${escapeHtml(logoUrl)}" alt="Augusta Search" crossorigin="anonymous" />
  </div>
  <div class="header-text">
    <h1>${escapeHtml(title)}</h1>
    <p class="doc">${escapeHtml(payload.documentSubtitle)}</p>
    <div class="meta-row">
      ${metaParts.map((m) => `<span class="meta-item">📅 ${escapeHtml(m)}</span>`).join('')}
    </div>
  </div>
</header>

<section class="question-box">
  <div class="q-icon">?</div>
  <div>
    <div class="q-label">QUESTION</div>
    <div class="q-text">${escapeHtml(payload.question)}</div>
  </div>
</section>

<div class="body-grid">
  <div class="answer-col">
    <div class="section-label answer"><span>✓</span> ANSWER</div>
    ${
      sections.practitionerSummary
        ? `<p class="summary"><strong>Practitioner summary</strong><br/>${escapeHtml(truncateText(sections.practitionerSummary, 520))}</p>`
        : ''
    }
    ${
      sections.clauseRequirements.length
        ? `<div class="subsection"><h4>What the cited clauses require</h4>${bulletsHtml(sections.clauseRequirements)}</div>`
        : ''
    }
    ${exceptionsBlock}
    ${outcomeBlock}
  </div>
  <aside class="sidebar">
    <div class="sidebar-block">
      <h3><span class="sidebar-icon">📋</span> Required Actions</h3>
      ${bulletsHtml(sections.requiredActions, '#2563eb')}
    </div>
    <div class="sidebar-block">
      <h3><span class="sidebar-icon">📍</span> Confirm on Site or in Design Records</h3>
      ${bulletsHtml(sections.confirmOnSite, '#2563eb')}
    </div>
    <div class="sidebar-block">
      <h3><span class="sidebar-icon">📖</span> Clause References</h3>
      ${citationCardsHtml(sections.citations, payload.sources)}
    </div>
  </aside>
</div>

<footer class="footer-bar">
  <span class="footer-shield">🛡</span>
  <span>${escapeHtml(profileFooterNote(profile))}</span>
</footer>
</div>`;
}

async function waitForImages(root: HTMLElement, timeoutMs = 8000): Promise<void> {
  const images = Array.from(root.querySelectorAll('img'));
  if (!images.length) return;
  await Promise.race([
    Promise.all(
      images.map(
        (img) =>
          new Promise<void>((resolve) => {
            if (img.complete && img.naturalWidth > 0) {
              resolve();
              return;
            }
            img.onload = () => resolve();
            img.onerror = () => resolve();
          }),
      ),
    ),
    new Promise<void>((resolve) => window.setTimeout(resolve, timeoutMs)),
  ]);
}

function socialImageFilename(payload: QaExportPayload): string {
  const date = payload.answeredAt.toISOString().slice(0, 10);
  const prefix = PROFILE_META[resolveProfile(payload)].filenamePrefix;
  return `${prefix}-social-${date}.jpg`;
}

/** Render branded Q&A card and download as JPEG (Facebook-friendly). */
export async function exportQaAnswerAsJpeg(payload: QaExportPayload): Promise<void> {
  const logoUrl = `${window.location.origin}${headerBrandLogoUrl}`;
  const host = document.createElement('div');
  host.style.position = 'fixed';
  host.style.left = '-9999px';
  host.style.top = '0';
  host.style.zIndex = '-1';
  host.style.pointerEvents = 'none';
  host.innerHTML = buildSocialCardHtml(payload, logoUrl);
  document.body.appendChild(host);

  const card = host.querySelector('.social-card') as HTMLElement | null;
  if (!card) {
    document.body.removeChild(host);
    throw new Error('Could not build social export card.');
  }

  try {
    await waitForImages(card);
    // Allow layout/fonts to settle
    await new Promise((r) => window.setTimeout(r, 150));

    const dataUrl = await toJpeg(card, {
      quality: JPEG_QUALITY,
      width: CARD_WIDTH,
      pixelRatio: 2,
      cacheBust: true,
      skipFonts: false,
      style: {
        transform: 'scale(1)',
        transformOrigin: 'top left',
      },
    });

    const link = document.createElement('a');
    link.href = dataUrl;
    link.download = socialImageFilename(payload);
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    document.body.removeChild(host);
  }
}
