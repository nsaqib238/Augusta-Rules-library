/** Browser-persisted multi-run design planning report (append sections per code bundle). */

export type DesignPlanningReportItem = {
  requirement_id: string;
  domain?: string;
  question?: string;
  status: string;
  summary: string;
  reason: string;
  design_scope?: {
    deliverables?: string[];
    design_checks?: string[];
    coordination?: string[];
    confirm?: string[];
  };
  design_actions?: string[];
  evidence_ids?: string[];
};

export type DesignPlanningReportSource = {
  id: string;
  document_id?: string;
  codebook?: string;
  codebook_label?: string;
  clause_number?: string;
  heading?: string;
  page_number?: number;
};

export type DesignPlanningRunReport = {
  report_markdown: string;
  executive_summary: string;
  items: DesignPlanningReportItem[];
  sources?: DesignPlanningReportSource[];
  cross_domain_notes?: string[];
  open_items?: string[];
  recommendations?: string[];
};

export type DesignPlanningFinalizedSnapshot = {
  finalizedAt: string;
  sectionCount: number;
  sectionLabels: string[];
  report: DesignPlanningRunReport;
};

export type DesignPlanningReportSection = {
  id: string;
  label: string;
  codeLabels: string[];
  documentIds: string[];
  discipline: string;
  addedAt: string;
  report: DesignPlanningRunReport;
};

export type DesignPlanningProjectReport = {
  version: 1;
  briefQuestion: string;
  scopingAnswers: Record<string, string>;
  discipline: string;
  sections: DesignPlanningReportSection[];
  updatedAt: string;
  finalized?: DesignPlanningFinalizedSnapshot;
};

const STORAGE_VERSION = 1;
const STORAGE_PREFIX = 'augusta-design-planning-project-v1';

function storageKey(userId?: string | null): string {
  return `${STORAGE_PREFIX}-${userId?.trim() || 'anonymous'}`;
}

function readRaw(key: string): DesignPlanningProjectReport | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as DesignPlanningProjectReport;
    if (parsed?.version !== STORAGE_VERSION || !Array.isArray(parsed.sections)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeRaw(key: string, report: DesignPlanningProjectReport): void {
  window.localStorage.setItem(key, JSON.stringify(report));
}

export function loadProjectReport(userId?: string | null): DesignPlanningProjectReport | null {
  return readRaw(storageKey(userId));
}

export function clearProjectReport(userId?: string | null): void {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(storageKey(userId));
}

export function removeProjectSection(userId: string | null | undefined, sectionId: string): DesignPlanningProjectReport | null {
  const key = storageKey(userId);
  const current = readRaw(key);
  if (!current) return null;
  const sections = current.sections.filter((section) => section.id !== sectionId);
  if (sections.length === 0) {
    clearProjectReport(userId);
    return null;
  }
  const next: DesignPlanningProjectReport = {
    ...current,
    sections,
    updatedAt: new Date().toISOString(),
    finalized: undefined,
  };
  writeRaw(key, next);
  return next;
}

export function buildSectionLabel(codeLabels: string[]): string {
  const unique = Array.from(new Set(codeLabels.map((label) => label.trim()).filter(Boolean)));
  if (unique.length === 0) return 'Selected codes';
  if (unique.length <= 4) return unique.join(' · ');
  return `${unique.slice(0, 3).join(' · ')} + ${unique.length - 3} more`;
}

export type AddSectionInput = {
  briefQuestion: string;
  scopingAnswers: Record<string, string>;
  discipline: string;
  documentIds: string[];
  codeLabels: string[];
  report: DesignPlanningRunReport;
};

export function addSectionToProjectReport(
  userId: string | null | undefined,
  input: AddSectionInput
): DesignPlanningProjectReport {
  const key = storageKey(userId);
  const existing = readRaw(key);
  const section: DesignPlanningReportSection = {
    id: `section-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    label: buildSectionLabel(input.codeLabels),
    codeLabels: input.codeLabels,
    documentIds: input.documentIds,
    discipline: input.discipline,
    addedAt: new Date().toISOString(),
    report: input.report,
  };

  const next: DesignPlanningProjectReport = {
    version: STORAGE_VERSION,
    briefQuestion: input.briefQuestion.trim(),
    scopingAnswers: input.scopingAnswers,
    discipline: input.discipline,
    sections: existing ? [...existing.sections, section] : [section],
    updatedAt: new Date().toISOString(),
    finalized: undefined,
  };
  writeRaw(key, next);
  return next;
}

export function isFinalizedReportCurrent(project: DesignPlanningProjectReport | null | undefined): boolean {
  if (!project?.finalized) return false;
  return project.finalized.sectionCount === project.sections.length;
}

export function saveFinalizedReport(
  userId: string | null | undefined,
  project: DesignPlanningProjectReport,
  payload: {
    report: DesignPlanningRunReport;
    section_labels?: string[];
    merged_from_sections?: number;
  }
): DesignPlanningProjectReport {
  const key = storageKey(userId);
  const next: DesignPlanningProjectReport = {
    ...project,
    finalized: {
      finalizedAt: new Date().toISOString(),
      sectionCount: project.sections.length,
      sectionLabels: payload.section_labels ?? project.sections.map((section) => section.label),
      report: payload.report,
    },
    updatedAt: new Date().toISOString(),
  };
  writeRaw(key, next);
  return next;
}

export function getExportableProjectReport(
  project: DesignPlanningProjectReport
): DesignPlanningRunReport | null {
  if (project.sections.length === 1) {
    return project.sections[0].report;
  }
  if (isFinalizedReportCurrent(project)) {
    return project.finalized!.report;
  }
  return null;
}

export function buildFinalizedProjectMarkdown(project: DesignPlanningProjectReport): string {
  const exportReport = getExportableProjectReport(project);
  if (!exportReport) {
    throw new Error('Generate the final report before exporting a multi-section project.');
  }
  const markdown = exportReport.report_markdown?.trim();
  if (!markdown) {
    throw new Error('Final report content is missing. Click Generate final report again.');
  }
  return markdown;
}

function formatSectionDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  } catch {
    return iso;
  }
}

const SCOPE_SECTIONS: Array<{ key: keyof NonNullable<DesignPlanningReportItem['design_scope']>; label: string }> = [
  { key: 'deliverables', label: 'Drawings & specifications' },
  { key: 'design_checks', label: 'Design checks (clearances, sizing, access)' },
  { key: 'coordination', label: 'Coordinate with' },
  { key: 'confirm', label: 'Confirm before detailing' },
];

function appendDesignScopeMarkdown(lines: string[], item: DesignPlanningReportItem): void {
  const scope = item.design_scope;
  const hasScope = scope && SCOPE_SECTIONS.some((section) => (scope[section.key]?.length ?? 0) > 0);
  if (hasScope && scope) {
    lines.push('**Your design package should address:**', '');
    for (const section of SCOPE_SECTIONS) {
      const entries = scope[section.key] ?? [];
      if (entries.length === 0) continue;
      lines.push(`*${section.label}:*`, ...entries.map((entry) => `- ${entry}`), '');
    }
    return;
  }
  if (item.design_actions?.length) {
    lines.push('**Address in drawings/specs:**', ...item.design_actions.map((action) => `- ${action}`), '');
  }
}

export function buildSectionMarkdown(section: DesignPlanningReportSection, index: number): string {
  const lines = [
    `## Part ${index + 1}: ${section.label}`,
    `_Added ${formatSectionDate(section.addedAt)} · ${section.discipline}_`,
    '',
    section.report.report_markdown.trim(),
    '',
  ];

  if (section.report.items.length > 0) {
    lines.push('### Design requirements checklist', '');
    for (const item of section.report.items) {
      lines.push(`#### ${item.domain || item.requirement_id}`, '');
      lines.push(`- **Status:** ${item.status.replace(/_/g, ' ')}`, '');
      lines.push(item.summary, '');
      if (item.reason) lines.push(`_Why it applies:_ ${item.reason}`, '');
      appendDesignScopeMarkdown(lines, item);
      lines.push('');
    }
  }

  return lines.join('\n').trim();
}

export function buildMergedProjectMarkdown(project: DesignPlanningProjectReport): string {
  const scopingLines = Object.entries(project.scopingAnswers)
    .filter(([, value]) => value.trim())
    .map(([id, value]) => `- **${id}:** ${value.trim()}`);

  const header = [
    '# Design Planning Report — Project briefing',
    '',
    '## Project brief',
    '',
    project.briefQuestion.trim(),
    '',
    `_Discipline: ${project.discipline} · ${project.sections.length} report section${project.sections.length === 1 ? '' : 's'}_`,
    '',
  ];

  if (scopingLines.length > 0) {
    header.push('## Scoping answers', '', ...scopingLines, '');
  }

  const body = project.sections
    .map((section, index) => buildSectionMarkdown(section, index))
    .join('\n\n---\n\n');

  const footer = [
    '',
    '---',
    '',
    '_This report is generated from cited code provisions for planning purposes only. It is not a certificate of compliance. Confirm applicability, site conditions, and engage a qualified person where required._',
  ];

  return [...header, body, ...footer].join('\n').trim();
}

export function buildSingleRunMarkdown(input: {
  briefQuestion: string;
  discipline: string;
  scopingAnswers: Record<string, string>;
  codeLabels: string[];
  report: DesignPlanningRunReport;
}): string {
  const fakeProject: DesignPlanningProjectReport = {
    version: STORAGE_VERSION,
    briefQuestion: input.briefQuestion,
    scopingAnswers: input.scopingAnswers,
    discipline: input.discipline,
    sections: [
      {
        id: 'single',
        label: buildSectionLabel(input.codeLabels),
        codeLabels: input.codeLabels,
        documentIds: [],
        discipline: input.discipline,
        addedAt: new Date().toISOString(),
        report: input.report,
      },
    ],
    updatedAt: new Date().toISOString(),
  };
  return buildMergedProjectMarkdown(fakeProject);
}
