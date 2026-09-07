/**
 * Design Planning social JPG export — branded card (1200px wide) for LinkedIn / social.
 */

import { toJpeg } from 'html-to-image';
import { headerBrandLogoUrl } from './brandLogo';
import { formatExportDate, DesignPlanningExportInput } from './designPlanningExport';
import {
  DesignPlanningProjectReport,
  DesignPlanningReportItem,
  DesignPlanningRunReport,
  getExportableProjectReport,
} from './designPlanningProjectReport';

const CARD_WIDTH = 1200;
const JPEG_QUALITY = 0.92;
const MAX_CHECKLIST_ITEMS = 6;
const MAX_OPEN_ITEMS = 4;

type NormalizedStatus = 'applies' | 'conditional' | 'not_applicable' | 'needs_code' | 'conflict';

const STATUS_STYLE: Record<NormalizedStatus, { bg: string; color: string; label: string }> = {
  applies: { bg: '#dcfce7', color: '#166534', label: 'Applies' },
  conditional: { bg: '#fef9c3', color: '#854d0e', label: 'Conditional' },
  not_applicable: { bg: '#f1f5f9', color: '#64748b', label: 'Not applicable' },
  needs_code: { bg: '#e2e8f0', color: '#475569', label: 'Code not selected' },
  conflict: { bg: '#fee2e2', color: '#991b1b', label: 'Potential conflict' },
};

const STATUS_RANK: Record<NormalizedStatus, number> = {
  conflict: 0,
  applies: 1,
  conditional: 2,
  needs_code: 3,
  not_applicable: 4,
};

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function truncateText(text: string, maxLen: number): string {
  const trimmed = text.replace(/\s+/g, ' ').trim();
  if (trimmed.length <= maxLen) return trimmed;
  return `${trimmed.slice(0, maxLen - 1).trimEnd()}…`;
}

function normalizeStatus(status: string): NormalizedStatus {
  const normalized = (status || 'conditional').trim().toLowerCase().replace(/\s+/g, '_');
  if (normalized in STATUS_STYLE) return normalized as NormalizedStatus;
  return 'conditional';
}

function statusLabel(status: string): string {
  return STATUS_STYLE[normalizeStatus(status)].label;
}

function priorityItems(items: DesignPlanningReportItem[]): DesignPlanningReportItem[] {
  return [...items]
    .sort(
      (left, right) =>
        STATUS_RANK[normalizeStatus(left.status)] - STATUS_RANK[normalizeStatus(right.status)],
    )
    .slice(0, MAX_CHECKLIST_ITEMS);
}

function buildChecklistHtml(items: DesignPlanningReportItem[]): string {
  const selected = priorityItems(items);
  if (!selected.length) {
    return '<p class="muted">No checklist items in this report.</p>';
  }
  return `<div class="checklist">${selected
    .map((item) => {
      const style = STATUS_STYLE[normalizeStatus(item.status)];
      const domain = item.domain || item.requirement_id || 'Requirement';
      return `<article class="check-row">
        <div class="check-head">
          <h4>${escapeHtml(truncateText(domain.replace(/_/g, ' '), 72))}</h4>
          <span class="status-pill" style="background:${style.bg};color:${style.color}">${escapeHtml(statusLabel(item.status))}</span>
        </div>
        <p>${escapeHtml(truncateText(item.summary, 180))}</p>
      </article>`;
    })
    .join('')}</div>`;
}

function buildOpenItemsHtml(openItems?: string[]): string {
  const items = (openItems ?? []).filter(Boolean).slice(0, MAX_OPEN_ITEMS);
  if (!items.length) return '<p class="muted">Confirm key project facts during detailed design.</p>';
  return `<ul class="bullets">${items
    .map((item) => `<li><span class="dot">•</span>${escapeHtml(truncateText(item, 160))}</li>`)
    .join('')}</ul>`;
}

function buildStatsHtml(report: DesignPlanningRunReport): string {
  const counts = report.items.reduce<Record<NormalizedStatus, number>>(
    (acc, item) => {
      const status = normalizeStatus(item.status);
      acc[status] += 1;
      return acc;
    },
    { applies: 0, conditional: 0, not_applicable: 0, needs_code: 0, conflict: 0 },
  );
  const parts = [
    `${report.items.length} planning topic${report.items.length === 1 ? '' : 's'}`,
    counts.conflict ? `${counts.conflict} conflict${counts.conflict === 1 ? '' : 's'}` : '',
    counts.applies ? `${counts.applies} applies` : '',
    counts.conditional ? `${counts.conditional} conditional` : '',
  ].filter(Boolean);
  return parts.join(' · ');
}

function buildSocialCardHtml(
  input: DesignPlanningExportInput & { subtitle?: string },
  logoUrl: string,
): string {
  const exportedAt = input.exportedAt ?? new Date();
  const report = input.report;
  const standards =
    input.codeLabels.length > 0
      ? truncateText(input.codeLabels.join(' · '), 140)
      : 'Selected standards';
  const subtitle =
    input.subtitle?.trim() ||
    (input.codeLabels.length <= 4
      ? input.codeLabels.join(' · ')
      : `${input.codeLabels.slice(0, 3).join(' · ')} + ${input.codeLabels.length - 3} more`);

  return `<div class="social-card" xmlns="http://www.w3.org/1999/xhtml">
<style>
  .social-card {
    width: ${CARD_WIDTH}px;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    background: #eef2f7;
    color: #0f172a;
    line-height: 1.45;
    box-sizing: border-box;
  }
  .social-card * { box-sizing: border-box; }
  .header {
    background: linear-gradient(135deg, #0b1220 0%, #263245 100%);
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
    margin: 0 0 4px;
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  .header-text .tagline {
    margin: 0 0 8px;
    font-size: 15px;
    color: #cbd5e1;
    font-weight: 500;
  }
  .meta-row {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    font-size: 12px;
    color: #94a3b8;
  }
  .brief-box {
    margin: 0;
    background: linear-gradient(135deg, #0b1220 0%, #263245 100%);
    border-left: 6px solid #9a7a35;
    padding: 22px 32px 24px;
  }
  .brief-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.14em;
    color: #f1ddab;
    margin-bottom: 8px;
  }
  .brief-text {
    font-size: 18px;
    font-weight: 600;
    color: #fff;
    line-height: 1.4;
  }
  .body-grid {
    display: grid;
    grid-template-columns: 1fr 320px;
    gap: 0;
    background: #fff;
  }
  .main-col { padding: 24px 28px 28px 32px; border-right: 1px solid #e2e8f0; }
  .sidebar { padding: 24px 22px 28px 20px; background: #f8fafc; }
  .section-label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: #9a7a35;
    margin-bottom: 12px;
  }
  .summary {
    font-size: 14px;
    color: #334155;
    margin: 0 0 18px;
    line-height: 1.55;
  }
  .stats {
    display: inline-block;
    background: #fff7e6;
    border: 1px solid #f1ddab;
    color: #7c5f1e;
    font-size: 12px;
    font-weight: 600;
    padding: 6px 10px;
    border-radius: 999px;
    margin-bottom: 16px;
  }
  .checklist { display: flex; flex-direction: column; gap: 10px; }
  .check-row {
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 12px 14px;
    background: #fff;
  }
  .check-head {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: flex-start;
    margin-bottom: 6px;
  }
  .check-head h4 {
    margin: 0;
    font-size: 14px;
    font-weight: 700;
    color: #0f172a;
    line-height: 1.3;
  }
  .status-pill {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 4px 8px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .check-row p {
    margin: 0;
    font-size: 12px;
    color: #475569;
    line-height: 1.45;
  }
  .sidebar-block {
    margin-bottom: 20px;
    padding-bottom: 16px;
    border-bottom: 1px solid #e2e8f0;
  }
  .sidebar-block:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
  .sidebar-block h3 {
    margin: 0 0 10px;
    font-size: 13px;
    font-weight: 700;
    color: #1e40af;
  }
  .standards {
    font-size: 12px;
    color: #475569;
    line-height: 1.45;
  }
  .bullets { list-style: none; margin: 0; padding: 0; }
  .bullets li {
    display: flex;
    gap: 8px;
    font-size: 12px;
    color: #334155;
    margin-bottom: 8px;
    line-height: 1.4;
  }
  .dot { color: #9a7a35; font-weight: 700; flex-shrink: 0; }
  .muted { font-size: 12px; color: #94a3b8; margin: 0; }
  .footer-bar {
    background: #0b1220;
    color: #94a3b8;
    font-size: 11px;
    padding: 12px 32px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .footer-shield { color: #f1ddab; font-size: 14px; }
</style>

<header class="header">
  <div class="logo-wrap">
    <img src="${escapeHtml(logoUrl)}" alt="Augusta Search" crossorigin="anonymous" />
  </div>
  <div class="header-text">
    <h1>Design Planning Report — ${escapeHtml(input.discipline.replace(/^\w/, (c) => c.toUpperCase()))}</h1>
    <p class="tagline">${escapeHtml(subtitle)}</p>
    <div class="meta-row">
      <span>📅 Exported ${escapeHtml(formatExportDate(exportedAt))}</span>
      <span>📋 ${escapeHtml(buildStatsHtml(report))}</span>
    </div>
  </div>
</header>

<section class="brief-box">
  <div class="brief-label">PROJECT BRIEF</div>
  <div class="brief-text">${escapeHtml(truncateText(input.briefQuestion, 320))}</div>
</section>

<div class="body-grid">
  <div class="main-col">
    <div class="section-label"><span>📋</span> EXECUTIVE SUMMARY</div>
    <p class="summary">${escapeHtml(
      truncateText(
        report.executive_summary ||
          'Design planning briefing generated from the project brief and selected standards.',
        520,
      ),
    )}</p>
    <div class="section-label"><span>✓</span> KEY DESIGN TOPICS</div>
    ${buildChecklistHtml(report.items)}
  </div>
  <aside class="sidebar">
    <div class="sidebar-block">
      <h3>Confirm during design</h3>
      ${buildOpenItemsHtml(report.open_items)}
    </div>
    <div class="sidebar-block">
      <h3>Standards covered</h3>
      <p class="standards">${escapeHtml(standards)}</p>
    </div>
    ${
      report.recommendations?.length
        ? `<div class="sidebar-block"><h3>Next steps</h3>${buildOpenItemsHtml(report.recommendations)}</div>`
        : ''
    }
  </aside>
</div>

<footer class="footer-bar">
  <span class="footer-shield">🛡</span>
  <span>AI-assisted design planning only — not a compliance certificate. Confirm on site and engage qualified professionals where required. augustasearch.com</span>
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

function socialImageFilename(prefix: string, exportedAt: Date): string {
  return `${prefix}-${exportedAt.toISOString().slice(0, 10)}-linkedin.jpg`;
}

async function renderSocialCardToJpeg(
  input: DesignPlanningExportInput & { subtitle?: string },
  filenamePrefix: string,
): Promise<void> {
  const logoUrl = `${window.location.origin}${headerBrandLogoUrl}`;
  const host = document.createElement('div');
  host.style.position = 'fixed';
  host.style.left = '-9999px';
  host.style.top = '0';
  host.style.zIndex = '-1';
  host.style.pointerEvents = 'none';
  host.innerHTML = buildSocialCardHtml(input, logoUrl);
  document.body.appendChild(host);

  const card = host.querySelector('.social-card') as HTMLElement | null;
  if (!card) {
    document.body.removeChild(host);
    throw new Error('Could not build design planning social export card.');
  }

  try {
    await waitForImages(card);
    await new Promise((resolve) => window.setTimeout(resolve, 150));

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

    const exportedAt = input.exportedAt ?? new Date();
    const link = document.createElement('a');
    link.href = dataUrl;
    link.download = socialImageFilename(filenamePrefix, exportedAt);
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    document.body.removeChild(host);
  }
}

/** Branded JPG for a single planning run (LinkedIn / social). */
export async function exportDesignPlanningAsJpeg(input: DesignPlanningExportInput): Promise<void> {
  await renderSocialCardToJpeg(input, 'design-planning-report');
}

/** Branded JPG for a unified final project report. */
export async function exportProjectReportAsJpeg(project: DesignPlanningProjectReport): Promise<void> {
  const exportReport = getExportableProjectReport(project);
  if (!exportReport) {
    throw new Error('Generate the final report before exporting a multi-section project.');
  }
  const sectionCount = project.sections.length;
  const codeLabels = Array.from(
    new Set(project.sections.flatMap((section) => section.codeLabels).filter(Boolean)),
  );
  await renderSocialCardToJpeg(
    {
      briefQuestion: project.briefQuestion,
      discipline: project.discipline,
      scopingAnswers: project.scopingAnswers,
      codeLabels,
      report: exportReport,
      subtitle:
        sectionCount > 1
          ? `Unified final report · ${sectionCount} planning runs merged`
          : undefined,
    },
    'design-planning-final-report',
  );
}
