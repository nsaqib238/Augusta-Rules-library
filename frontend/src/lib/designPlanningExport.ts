/**
 * Design Planning Report export: Markdown download and direct PDF download.
 */

import { downloadHtmlAsPdf } from './downloadHtmlAsPdf';
import { renderPractitionerMarkdown } from './renderMarkdown';
import {
  buildSingleRunMarkdown,
  DesignPlanningProjectReport,
  DesignPlanningRunReport,
  getExportableProjectReport,
} from './designPlanningProjectReport';

export interface DesignPlanningExportInput {
  briefQuestion: string;
  discipline: string;
  scopingAnswers: Record<string, string>;
  codeLabels: string[];
  report: DesignPlanningRunReport;
  exportedAt?: Date;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function formatExportDate(d: Date): string {
  return d.toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}


function exportFilename(prefix: string, exportedAt: Date): string {
  return `${prefix}-${exportedAt.toISOString().slice(0, 10)}`;
}

function statusLabel(status: string): string {
  return status.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

function buildChecklistHtml(report: DesignPlanningRunReport): string {
  if (!report.items.length) return '';

  const scopeSections = [
    { key: 'deliverables' as const, label: 'Drawings & specifications' },
    { key: 'design_checks' as const, label: 'Design checks' },
    { key: 'coordination' as const, label: 'Coordinate with' },
    { key: 'confirm' as const, label: 'Confirm before detailing' },
  ];

  const rows = report.items
    .map((item) => {
      const scope = item.design_scope;
      const scopeHtml = scope
        ? scopeSections
            .map((section) => {
              const entries = scope[section.key] ?? [];
              if (entries.length === 0) return '';
              return `<div class="scope-group"><p class="scope-label">${escapeHtml(section.label)}</p><ul>${entries
                .map((entry) => `<li>${escapeHtml(entry)}</li>`)
                .join('')}</ul></div>`;
            })
            .join('')
        : '';
      const actionsHtml =
        !scopeHtml && item.design_actions && item.design_actions.length > 0
          ? `<ul>${item.design_actions.map((action) => `<li>${escapeHtml(action)}</li>`).join('')}</ul>`
          : '';
      return `<article class="check-item">
        <div class="check-head">
          <h4>${escapeHtml(item.domain || item.requirement_id)}</h4>
          <span class="status">${escapeHtml(statusLabel(item.status))}</span>
        </div>
        <p>${escapeHtml(item.summary)}</p>
        ${item.reason ? `<p class="reason"><strong>Why it applies:</strong> ${escapeHtml(item.reason)}</p>` : ''}
        ${scopeHtml ? `<div class="scope-block"><p class="scope-title">Your design package should address</p>${scopeHtml}</div>` : actionsHtml}
      </article>`;
    })
    .join('');

  return `<section class="checklist">
    <h3>Design requirements</h3>
    ${rows}
  </section>`;
}

function buildExportHtml(options: {
  title: string;
  subtitle: string;
  briefQuestion: string;
  discipline: string;
  scopingAnswers: Record<string, string>;
  bodyMarkdown: string;
  checklistReports: DesignPlanningRunReport[];
  exportedAt: Date;
  sectionCount: number;
  /** When true, bodyMarkdown is a complete report — skip duplicate brief/scoping/checklist blocks. */
  fullDocument?: boolean;
}): string {
  const fullDocument = options.fullDocument ?? false;
  const scopingEntries = Object.entries(options.scopingAnswers).filter(([, value]) => value.trim());
  const scopingBlock =
    !fullDocument && scopingEntries.length > 0
      ? `<section class="scoping">
      <h3>Scoping answers</h3>
      <ul>${scopingEntries
        .map(([id, value]) => `<li><strong>${escapeHtml(id)}:</strong> ${escapeHtml(value.trim())}</li>`)
        .join('')}</ul>
    </section>`
      : '';

  const reportBodyHtml = renderPractitionerMarkdown(options.bodyMarkdown);
  const checklistHtml = fullDocument
    ? ''
    : options.checklistReports.map((report) => buildChecklistHtml(report)).join('');
  const briefBlock = fullDocument
    ? ''
    : `<section class="brief-block">
    <div class="label">Project brief</div>
    <div class="text">${escapeHtml(options.briefQuestion)}</div>
  </section>`;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(options.title)}</title>
  <style>
    @page { margin: 18mm 16mm; }
    * { box-sizing: border-box; }
    body {
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      font-size: 11pt;
      line-height: 1.55;
      color: #0f172a;
      margin: 0;
      padding: 0;
    }
    .header {
      background: linear-gradient(90deg, #0b1220 0%, #1f2937 100%);
      color: #fff;
      padding: 20px 24px;
      border-radius: 12px;
      margin-bottom: 24px;
    }
    .header-inner { display: flex; align-items: flex-start; gap: 14px; }
    .header-icon {
      width: 44px; height: 44px;
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.12);
      background: rgba(255,255,255,0.08);
      display: flex; align-items: center; justify-content: center;
      font-size: 20px;
    }
    .header h1 { margin: 0 0 4px 0; font-size: 18pt; font-weight: 600; }
    .header .tagline { margin: 0 0 4px 0; font-size: 10pt; color: #e2e8f0; }
    .header .meta { margin: 8px 0 0 0; font-size: 9pt; color: #94a3b8; }
    .brief-block {
      background: linear-gradient(90deg, #0b1220 0%, #263245 100%);
      color: #fff;
      padding: 14px 18px;
      border-radius: 10px;
      margin-bottom: 20px;
    }
    .brief-block .label {
      font-size: 8pt;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: #94a3b8;
      margin-bottom: 6px;
    }
    .brief-block .text { font-size: 11pt; white-space: pre-wrap; }
    .report-body {
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 18px 20px;
      background: #fff;
      margin-bottom: 20px;
    }
    .report-body h1 { font-size: 16pt; margin: 0 0 12px 0; }
    .report-body h2 { font-size: 13pt; margin: 18px 0 8px 0; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }
    .report-body h3 { font-size: 11.5pt; margin: 14px 0 6px 0; }
    .report-body p { margin: 8px 0; page-break-inside: avoid; break-inside: avoid-page; }
    .report-body ul { margin: 8px 0 8px 20px; padding: 0; page-break-inside: avoid; break-inside: avoid-page; }
    .report-body li { margin: 4px 0; page-break-inside: avoid; break-inside: avoid-page; }
    .report-body h2 { page-break-after: avoid; break-after: avoid-page; }
    .report-body h3 { page-break-after: avoid; break-after: avoid-page; page-break-inside: avoid; break-inside: avoid-page; }
    .report-body code {
      background: #f1f5f9;
      padding: 1px 5px;
      border-radius: 4px;
      font-size: 10pt;
    }
    .scoping { margin-bottom: 20px; font-size: 10.5pt; }
    .scoping h3 { font-size: 11pt; margin: 0 0 8px 0; }
    .scoping ul { margin: 0; padding-left: 20px; }
    .checklist { margin-top: 24px; }
    .checklist h3 { font-size: 12pt; margin: 0 0 12px 0; }
    .check-item {
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 12px 14px;
      margin-bottom: 10px;
      page-break-inside: avoid;
      break-inside: avoid-page;
    }
    .check-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
    .check-head h4 { margin: 0; font-size: 11pt; }
    .status {
      font-size: 8.5pt;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #475569;
      white-space: nowrap;
    }
    .check-item p { margin: 8px 0 0 0; font-size: 10.5pt; }
    .check-item .reason { font-size: 9.5pt; color: #64748b; }
    .check-item ul { margin: 8px 0 0 18px; padding: 0; font-size: 10pt; }
    .scope-block { margin-top: 10px; padding-top: 8px; border-top: 1px dashed #e2e8f0; page-break-inside: avoid; break-inside: avoid-page; }
    .scope-title { margin: 0 0 6px 0; font-size: 9pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #9a7a35; }
    .scope-group { margin-top: 6px; }
    .scope-label { margin: 0 0 4px 0; font-size: 9.5pt; font-weight: 600; color: #334155; }
    .scope-group ul { margin: 0 0 0 18px; padding: 0; font-size: 9.5pt; }
    .footer {
      margin-top: 28px;
      padding-top: 14px;
      border-top: 1px solid #e2e8f0;
      font-size: 8.5pt;
      color: #64748b;
      line-height: 1.45;
    }
    @media print {
      .header, .brief-block { -webkit-print-color-adjust: exact; print-color-adjust: exact; border-radius: 0; }
    }
  </style>
</head>
<body>
  <header class="header">
    <div class="header-inner">
      <div class="header-icon" aria-hidden="true">📋</div>
      <div>
        <h1>${escapeHtml(options.title)}</h1>
        <p class="tagline">${escapeHtml(options.subtitle)}</p>
        <p class="meta">Exported ${escapeHtml(formatExportDate(options.exportedAt))} · Discipline: ${escapeHtml(options.discipline)}${
          fullDocument
            ? ' · Unified final report'
            : options.sectionCount > 1
              ? ` · ${options.sectionCount} sections`
              : ''
        }</p>
      </div>
    </div>
  </header>

  ${briefBlock}

  ${scopingBlock}

  <section class="report-body">
    ${reportBodyHtml}
  </section>

  ${checklistHtml}

  <footer class="footer">
    <p><strong>Note:</strong> This export is generated from cited code provisions for design planning only. It is not a certificate of compliance. Confirm applicability, site conditions, and engage a qualified person where required.</p>
    <p>Augusta Search · augustasearch.com</p>
  </footer>
</body>
</html>`;
}

function downloadTextFile(filename: string, content: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const blobUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = filename;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 120_000);
}

export function downloadDesignPlanningMarkdown(input: DesignPlanningExportInput): void {
  const exportedAt = input.exportedAt ?? new Date();
  const markdown = buildSingleRunMarkdown(input);
  const filename = `${exportFilename('design-planning-report', exportedAt)}.md`;
  downloadTextFile(filename, markdown, 'text/markdown;charset=utf-8');
}

export async function exportDesignPlanningAsPdf(input: DesignPlanningExportInput): Promise<void> {
  const exportedAt = input.exportedAt ?? new Date();
  const html = buildExportHtml({
    title: 'Design Planning Report',
    subtitle: buildSectionSubtitle(input.codeLabels),
    briefQuestion: input.briefQuestion,
    discipline: input.discipline,
    scopingAnswers: input.scopingAnswers,
    bodyMarkdown: input.report.report_markdown,
    checklistReports: [input.report],
    exportedAt,
    sectionCount: 1,
  });
  await downloadHtmlAsPdf(html, `${exportFilename('design-planning-report', exportedAt)}.pdf`);
}

export async function exportProjectReportAsPdf(project: DesignPlanningProjectReport): Promise<void> {
  const exportReport = getExportableProjectReport(project);
  if (!exportReport) {
    throw new Error('Generate the final report before exporting a multi-section project.');
  }
  const markdown = exportReport.report_markdown?.trim();
  if (!markdown) {
    throw new Error('Final report content is missing. Click Generate final report again.');
  }
  const exportedAt = new Date();
  const sectionCount = project.sections.length;
  const html = buildExportHtml({
    title: 'Design Planning Report — Final briefing',
    subtitle:
      sectionCount === 1
        ? buildSectionSubtitle(project.sections[0]?.codeLabels ?? [])
        : `Unified report · ${sectionCount} planning runs merged`,
    briefQuestion: project.briefQuestion,
    discipline: project.discipline,
    scopingAnswers: project.scopingAnswers,
    bodyMarkdown: markdown,
    checklistReports: [],
    exportedAt,
    sectionCount: 1,
    fullDocument: true,
  });
  await downloadHtmlAsPdf(html, `${exportFilename('design-planning-final-report', exportedAt)}.pdf`);
}

function buildSectionSubtitle(codeLabels: string[]): string {
  const unique = Array.from(new Set(codeLabels.map((label) => label.trim()).filter(Boolean)));
  if (unique.length === 0) return 'Selected standards';
  if (unique.length <= 5) return unique.join(' · ');
  return `${unique.slice(0, 4).join(' · ')} + ${unique.length - 4} more`;
}
