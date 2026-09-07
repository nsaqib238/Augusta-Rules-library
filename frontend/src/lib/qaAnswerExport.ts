/**
 * Per Q&A export: direct PDF download with Augusta Search header.
 */

import { downloadHtmlAsPdf } from './downloadHtmlAsPdf';

export interface QaExportSource {
  clause_number?: string;
  text?: string;
  page_number?: number;
}

export type QaExportProfile = 'qna' | 'ncc' | 'sir';

export interface QaExportPayload {
  /** Drives default title, icon, footer, and download filename when fields below are omitted. */
  profile?: QaExportProfile;
  productTitle?: string;
  /** Second line under the main title (e.g. "NCC (National Construction Code)"). */
  productTagline?: string;
  documentSubtitle: string;
  question: string;
  answerMarkdown: string;
  answeredAt: Date;
  confidence?: number;
  retrievalNote?: string;
  sources?: QaExportSource[];
}

export const PROFILE_META: Record<
  QaExportProfile,
  {
    defaultTitle: string;
    icon: string;
    sourcesTitle: string;
    footerNote: string;
    filenamePrefix: string;
  }
> = {
  qna: {
    defaultTitle: 'Augusta Search Q&A',
    icon: '💬',
    sourcesTitle: 'Sources / citations',
    footerNote:
      'This export is generated only from the cited clauses in the code(s) you selected. It is not a certificate of compliance. Confirm measurements, equipment, and site conditions on site; engage a qualified person where required.',
    filenamePrefix: 'augusta-search-qna',
  },
  ncc: {
    defaultTitle: 'NCC Q&A Assistant',
    icon: '🏗️',
    sourcesTitle: 'Supporting NCC provisions',
    footerNote:
      'This export is generated only from the NCC provisions cited for your question. It is not a certificate of compliance or a performance solution. Confirm applicability to your building class, state/territory variations, and on-site conditions; engage a qualified person where required.',
    filenamePrefix: 'augusta-search-ncc',
  },
  sir: {
    defaultTitle: 'Services & Installation Rules (SIR)',
    icon: '⚡',
    sourcesTitle: 'Supporting SIR provisions',
    footerNote:
      'This export is generated only from the SIR clauses cited for your question. It is not a certificate of compliance or network approval. Confirm with your distributor, site conditions, and applicable standards; engage a qualified person where required.',
    filenamePrefix: 'augusta-search-sir',
  },
};

export function resolveProfile(payload: QaExportPayload): QaExportProfile {
  return payload.profile ?? 'qna';
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Same rules as RichTextConverter — keep export aligned with on-screen answer. */
export function answerMarkdownToHtml(plainText: string): string {
  if (!plainText) return '';

  let richText = plainText
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`(.*?)`/g, '<code>$1</code>');

  richText = richText.replace(/^(\d+\.\s+)(.*)$/gm, '<li class="numbered">$1$2</li>');
  richText = richText.replace(/^([•\-\*]\s+)(.*)$/gm, '<li class="bullet">$1$2</li>');
  richText = richText.replace(/^(.+):\s*$/gm, '<h4 class="section">$1:</h4>');
  richText = richText.replace(
    /(<li class="numbered">.*?<\/li>(\s*<li class="numbered">.*?<\/li>)*)/gs,
    '<ol>$1</ol>',
  );
  richText = richText.replace(
    /(<li class="bullet">.*?<\/li>(\s*<li class="bullet">.*?<\/li>)*)/gs,
    '<ul>$1</ul>',
  );
  richText = richText.replace(/\n\n/g, '</p><p>');
  richText = `<p>${richText}</p>`;
  richText = richText.replace(/<p><\/p>/g, '');
  return richText;
}

export function formatExportDate(d: Date): string {
  return d.toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

export function buildQaExportHtml(payload: QaExportPayload): string {
  const profile = resolveProfile(payload);
  const profileMeta = PROFILE_META[profile];
  const title = payload.productTitle || profileMeta.defaultTitle;
  const tagline = payload.productTagline?.trim();
  const answerHtml = answerMarkdownToHtml(payload.answerMarkdown);
  const exportMetaLines: string[] = [`Exported ${formatExportDate(payload.answeredAt)}`];
  if (payload.confidence != null && payload.confidence > 0) {
    exportMetaLines.push(`Confidence ${(payload.confidence * 100).toFixed(1)}%`);
  }
  if (payload.retrievalNote) {
    exportMetaLines.push(escapeHtml(payload.retrievalNote));
  }

  const sourcesBlock =
    payload.sources && payload.sources.length > 0
      ? `<section class="sources">
      <h3>${escapeHtml(profileMeta.sourcesTitle)}</h3>
      <ul>${payload.sources
        .map((s) => {
          const ref = s.clause_number || s.text || 'Clause';
          const page = s.page_number ? ` (p. ${s.page_number})` : '';
          return `<li><strong>${escapeHtml(String(ref))}</strong>${page}</li>`;
        })
        .join('')}</ul>
    </section>`
      : '';

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(title)} — Q&amp;A export</title>
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
    .header h1 {
      margin: 0 0 4px 0;
      font-size: 18pt;
      font-weight: 600;
      letter-spacing: -0.02em;
    }
    .header .tagline {
      margin: 0 0 4px 0;
      font-size: 10pt;
      color: #e2e8f0;
      font-weight: 500;
    }
    .header .doc {
      margin: 0;
      font-size: 10.5pt;
      color: #cbd5e1;
      font-weight: 500;
    }
    .header .meta {
      margin: 8px 0 0 0;
      font-size: 9pt;
      color: #94a3b8;
    }
    .question-block {
      background: linear-gradient(90deg, #0b1220 0%, #263245 100%);
      color: #fff;
      padding: 14px 18px;
      border-radius: 10px;
      margin-bottom: 20px;
    }
    .question-block .label {
      font-size: 8pt;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: #94a3b8;
      margin-bottom: 6px;
    }
    .question-block .text { font-size: 11pt; white-space: pre-wrap; }
    .answer-block {
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 18px 20px;
      background: #fff;
    }
    .answer-block .label {
      font-size: 8pt;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: #64748b;
      margin-bottom: 10px;
      font-weight: 600;
    }
    .answer-block h4.section {
      margin: 14px 0 6px 0;
      font-size: 11pt;
      color: #0f172a;
    }
    .answer-block strong { color: #0f172a; }
    .answer-block ul, .answer-block ol { margin: 8px 0 8px 20px; padding: 0; }
    .answer-block li { margin: 4px 0; }
    .answer-block code {
      background: #f1f5f9;
      padding: 1px 5px;
      border-radius: 4px;
      font-size: 10pt;
    }
    .sources { margin-top: 20px; font-size: 10pt; }
    .sources h3 { font-size: 10pt; margin: 0 0 8px 0; color: #475569; }
    .footer {
      margin-top: 28px;
      padding-top: 14px;
      border-top: 1px solid #e2e8f0;
      font-size: 8.5pt;
      color: #64748b;
      line-height: 1.45;
    }
    .footer strong { color: #475569; }
    @media print {
      .header { border-radius: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
      .question-block { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    }
  </style>
</head>
<body>
  <header class="header">
    <div class="header-inner">
      <div class="header-icon" aria-hidden="true">${profileMeta.icon}</div>
      <div>
        <h1>${escapeHtml(title)}</h1>
        ${tagline ? `<p class="tagline">${escapeHtml(tagline)}</p>` : ''}
        <p class="doc">${escapeHtml(payload.documentSubtitle)}</p>
        <p class="meta">${exportMetaLines.join(' · ')}</p>
      </div>
    </div>
  </header>

  <section class="question-block">
    <div class="label">Question</div>
    <div class="text">${escapeHtml(payload.question)}</div>
  </section>

  <section class="answer-block">
    <div class="label">Answer</div>
    ${answerHtml}
  </section>

  ${sourcesBlock}

  <footer class="footer">
    <p><strong>Note:</strong> ${profileMeta.footerNote}</p>
    <p>Augusta Search · augustasearch.com</p>
  </footer>
</body>
</html>`;
}

function exportFilename(payload: QaExportPayload): string {
  const date = payload.answeredAt.toISOString().slice(0, 10);
  const prefix = PROFILE_META[resolveProfile(payload)].filenamePrefix;
  return `${prefix}-${date}.pdf`;
}

/** Download a styled Q&A export as PDF. */
export async function exportQaAnswerAsPdf(payload: QaExportPayload): Promise<void> {
  const html = buildQaExportHtml(payload);
  await downloadHtmlAsPdf(html, exportFilename(payload));
}

export function copyQaAnswerPlainText(payload: QaExportPayload): void {
  const profile = resolveProfile(payload);
  const profileMeta = PROFILE_META[profile];
  const title = payload.productTitle || profileMeta.defaultTitle;
  const tagline = payload.productTagline?.trim();
  const lines = [
    title,
    ...(tagline ? [tagline] : []),
    payload.documentSubtitle,
    `Exported ${formatExportDate(payload.answeredAt)}`,
    '',
    'Question',
    payload.question,
    '',
    'Answer',
    payload.answerMarkdown,
  ];
  if (payload.sources?.length) {
    lines.push('', profileMeta.sourcesTitle, ...payload.sources.map((s) => s.clause_number || s.text || ''));
  }
  lines.push('', `Note: ${profileMeta.footerNote}`);
  const text = lines.filter((l) => l !== undefined).join('\n');
  navigator.clipboard.writeText(text).catch(() => {
    alert('Could not copy to clipboard.');
  });
}

export function findQuestionForAssistantMessage(
  messages: Array<{ id: string; type: string; content: string }>,
  assistantMessageId: string,
): string {
  const idx = messages.findIndex((m) => m.id === assistantMessageId);
  if (idx <= 0) return '';
  for (let i = idx - 1; i >= 0; i--) {
    if (messages[i].type === 'user') return messages[i].content;
  }
  return '';
}
