import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Download,
  FileText,
  FolderPlus,
  HelpCircle,
  Image,
  Layers,
  Play,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { apiRequest } from '../lib/api';
import { labelForCodebookId } from '../lib/codebooks';
import LibraryTree from './library/LibraryTree';
import {
  LibraryCatalogDocument,
  LibraryCountry,
  LibraryDocumentType,
  LibraryEditionStatus,
  documentIsReady,
  editionsForDocument,
} from '../lib/libraryCatalog';
import {
  downloadDesignPlanningMarkdown,
  exportDesignPlanningAsPdf,
  exportProjectReportAsPdf,
} from '../lib/designPlanningExport';
import {
  exportDesignPlanningAsJpeg,
  exportProjectReportAsJpeg,
} from '../lib/designPlanningSocialImageExport';
import {
  addSectionToProjectReport,
  clearProjectReport,
  DesignPlanningProjectReport,
  isFinalizedReportCurrent,
  loadProjectReport,
  removeProjectSection,
  saveFinalizedReport,
} from '../lib/designPlanningProjectReport';
import { renderPractitionerMarkdown } from '../lib/renderMarkdown';
import { supabase } from '../lib/supabase';
import { DesignScopeBlock, DesignScope } from './DesignScopeBlock';

type Discipline =
  | 'electrical'
  | 'mechanical'
  | 'fire'
  | 'hydraulics'
  | 'multi-discipline'
  | 'other';

type ReviewDocument = {
  id: string;
  filename?: string;
  original_filename?: string;
  codebook?: string;
  source?: string;
  discipline?: string;
  status: string;
  is_shared_library?: boolean;
};

type ScopingQuestion = { id: string; question: string; why?: string };
type Requirement = {
  id: string;
  domain: string;
  question: string;
  preferred_sources?: string[];
  applicability?: string;
};
type Recommendation = {
  codebook_id: string;
  label: string;
  reason: string;
  availability: string;
};
type ReportScope = {
  requirement_count: number;
  document_count: number;
  estimated_rag_calls: number;
  level: 'ok' | 'warn' | 'block';
};

type Plan = {
  needs_scoping: boolean;
  scoping_questions: ScopingQuestion[];
  requirements: Requirement[];
  recommended_codes: Recommendation[];
  planner_notes?: string;
  selected_documents?: ReviewDocument[];
  scope?: ReportScope;
  scope_warning?: string;
};

type ReportItem = {
  requirement_id: string;
  domain?: string;
  question?: string;
  status: 'applies' | 'conditional' | 'not_applicable' | 'needs_code' | 'conflict';
  summary: string;
  reason: string;
  design_scope?: DesignScope;
  design_actions?: string[];
  evidence_ids?: string[];
};
type Report = {
  report_markdown: string;
  executive_summary: string;
  items: ReportItem[];
};

const DISCIPLINES: Array<{ id: Discipline; label: string }> = [
  { id: 'electrical', label: 'Electrical' },
  { id: 'mechanical', label: 'Mechanical / HVAC' },
  { id: 'fire', label: 'Fire safety' },
  { id: 'hydraulics', label: 'Hydraulics' },
  { id: 'multi-discipline', label: 'Multi-discipline' },
  { id: 'other', label: 'Other' },
];

const SOFT_WARN_ESTIMATED_RAG_CALLS = 40;
const HARD_MAX_ESTIMATED_RAG_CALLS = 72;
const SOFT_WARN_SELECTED_DOCUMENTS = 6;
const HARD_MAX_SELECTED_DOCUMENTS = 10;
const HARD_MAX_REQUIREMENTS_WITH_MANY_DOCS = 10;

function estimateClientScope(requirementCount: number, documentCount: number): ReportScope {
  const estimated_rag_calls = Math.max(1, requirementCount) * Math.max(1, documentCount);
  let level: ReportScope['level'] = 'ok';
  if (
    estimated_rag_calls > HARD_MAX_ESTIMATED_RAG_CALLS ||
    (documentCount > HARD_MAX_SELECTED_DOCUMENTS && requirementCount > HARD_MAX_REQUIREMENTS_WITH_MANY_DOCS)
  ) {
    level = 'block';
  } else if (estimated_rag_calls > SOFT_WARN_ESTIMATED_RAG_CALLS || documentCount > SOFT_WARN_SELECTED_DOCUMENTS) {
    level = 'warn';
  }
  return {
    requirement_count: requirementCount,
    document_count: documentCount,
    estimated_rag_calls,
    level,
  };
}

function scopeReductionMessage(scope: ReportScope): string {
  if (scope.level === 'block') {
    return (
      `This report is too large to run reliably (${scope.requirement_count} planning topics × ` +
      `${scope.document_count} selected codes ≈ ${scope.estimated_rag_calls} code searches). ` +
      `Deselect some codes — aim for about 3–5 key standards — or shorten the brief and click Plan this review again.`
    );
  }
  if (scope.level === 'warn') {
    return (
      `This is a large report (${scope.requirement_count} topics, ${scope.document_count} codes, ` +
      `≈ ${scope.estimated_rag_calls} searches). If generation fails, remove non-essential codes and re-plan.`
    );
  }
  return '';
}

const STATUS_META: Record<
  ReportItem['status'],
  { label: string; className: string; icon: React.ReactNode }
> = {
  applies: {
    label: 'Applies to this project',
    className: 'border-emerald-200 bg-emerald-50 text-emerald-900',
    icon: <CheckCircle2 className="h-4 w-4" />,
  },
  conditional: {
    label: 'Conditional',
    className: 'border-sky-200 bg-sky-50 text-sky-900',
    icon: <HelpCircle className="h-4 w-4" />,
  },
  not_applicable: {
    label: 'Not applicable',
    className: 'border-slate-200 bg-slate-50 text-slate-700',
    icon: <FileText className="h-4 w-4" />,
  },
  needs_code: {
    label: 'Code not selected',
    className: 'border-amber-200 bg-amber-50 text-amber-900',
    icon: <AlertTriangle className="h-4 w-4" />,
  },
  conflict: {
    label: 'Potential conflict',
    className: 'border-red-200 bg-red-50 text-red-900',
    icon: <AlertTriangle className="h-4 w-4" />,
  },
};

const LEGACY_STATUS_MAP: Record<string, ReportItem['status']> = {
  compliant: 'applies',
  non_compliant: 'conflict',
  not_assessed: 'conditional',
};

function normalizeReportStatus(status: string): ReportItem['status'] {
  const normalized = LEGACY_STATUS_MAP[status] || status;
  if (normalized in STATUS_META) return normalized as ReportItem['status'];
  return 'conditional';
}

function displayDocumentName(document: ReviewDocument): string {
  return document.original_filename || document.filename || document.source || document.codebook || 'Untitled document';
}

function errorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === 'object') {
    const record = data as { detail?: unknown; error?: unknown; message?: unknown };
    for (const candidate of [record.detail, record.error, record.message]) {
      if (typeof candidate === 'string' && candidate.trim()) return candidate;
    }
  }
  return fallback;
}

async function readApiJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text.trim()) {
    throw new Error('The server returned an empty response. Please try again.');
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    if (text.trim().startsWith('<')) {
      throw new Error(
        'The report server returned an HTML error page. The report may have timed out; please deploy the latest backend and try again.'
      );
    }
    throw new Error('The server returned an invalid response. Please try again.');
  }
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function pollDesignComplianceJob(jobId: string): Promise<any> {
  const deadline = Date.now() + 20 * 60 * 1000;
  while (Date.now() < deadline) {
    await sleep(2500);
    const response = await apiRequest(`/api/v1/query/design-compliance/jobs/${jobId}`);
    const data = await readApiJson(response);
    if (!response.ok) throw new Error(errorMessage(data, 'The report job failed'));
    if (data && typeof data === 'object' && (data as { status?: string }).status === 'done') {
      return (data as { result?: unknown }).result;
    }
    if (data && typeof data === 'object' && (data as { status?: string }).status === 'error') {
      throw new Error(errorMessage(data, 'The report job failed'));
    }
  }
  throw new Error('The report is taking too long. Please try again later.');
}

const DEFAULT_DESIGN_REVIEW_QUESTION = `Electrical design planning brief for an 8-storey Class 3 student accommodation building in Victoria, comprising approximately 12,000 m² gross floor area, 180 bedrooms, 220 occupants, and one basement car park.

The proposed electrical design has:
- 400/230 V, three-phase supply
- Estimated connected load: 680 kVA
- Estimated diversified maximum demand: 450 kVA
- One lift
- Fire pumps and fire detection systems
- Emergency lighting and exit signs
- Standby generator: proposed but not confirmed
- 80 EV charging spaces
- 150 kW rooftop PV system
- 250 kWh battery energy storage system
- Individual room metering plus common-area metering

Identify which code requirements apply to this brief and what the electrical design must address in drawings and specifications.`;

const DesignCompliancePanel: React.FC = () => {
  const { user } = useAuth();
  const [discipline, setDiscipline] = useState<Discipline>('electrical');
  const [documents, setDocuments] = useState<ReviewDocument[]>([]);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [countries, setCountries] = useState<LibraryCountry[]>([]);
  const [libraryTypes, setLibraryTypes] = useState<LibraryDocumentType[]>([]);
  const [catalogDocuments, setCatalogDocuments] = useState<LibraryCatalogDocument[]>([]);
  const [editions, setEditions] = useState<LibraryEditionStatus[]>([]);
  const [countryId, setCountryId] = useState('');
  const [typeId, setTypeId] = useState<string | null>(null);
  const [selectedCatalogIds, setSelectedCatalogIds] = useState<string[]>([]);
  const [question, setQuestion] = useState(DEFAULT_DESIGN_REVIEW_QUESTION);
  const [scopingAnswers, setScopingAnswers] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState<Plan | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [loadingDocuments, setLoadingDocuments] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [projectReport, setProjectReport] = useState<DesignPlanningProjectReport | null>(null);
  const [reportView, setReportView] = useState<'current' | 'sections' | 'final'>('current');
  const [projectNotice, setProjectNotice] = useState('');
  const [finalizingProject, setFinalizingProject] = useState(false);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [exportingJpeg, setExportingJpeg] = useState(false);

  const loadDocuments = useCallback(async () => {
    if (!user?.id) return;
    try {
      setLoadingDocuments(true);
      const [countryRes, typeRes, docRes, editionRes, readyRes] = await Promise.all([
        supabase.from('library_countries').select('*').eq('is_active', true).order('sort_order'),
        supabase.from('library_document_types').select('*').order('sort_order'),
        supabase.from('library_documents').select('*').eq('is_active', true).order('sort_order'),
        supabase.from('shared_library_editions').select('codebook, label, family, library_document_id, discipline'),
        supabase
          .from('documents')
          .select('id, filename, original_filename, codebook, source, discipline, status, created_at, is_shared_library')
          .eq('is_shared_library', true)
          .eq('status', 'ready_for_search')
          .order('original_filename', { ascending: true }),
      ]);
      if (countryRes.error) {
        throw new Error(countryRes.error.message);
      }
      const readyDocs = ((readyRes.data || []) as ReviewDocument[]).map((document) => ({
        ...document,
        is_shared_library: true,
      }));
      const readyByCodebook = new Map(readyDocs.map((row) => [String(row.codebook || '').toUpperCase(), row]));
      setDocuments(readyDocs);
      setCountries((countryRes.data || []) as LibraryCountry[]);
      setLibraryTypes((typeRes.data || []) as LibraryDocumentType[]);
      setCatalogDocuments((docRes.data || []) as LibraryCatalogDocument[]);
      setEditions(
        (editionRes.data || []).map((row) => {
          const readyDoc = readyByCodebook.get(String(row.codebook || '').toUpperCase());
          return {
            codebook: row.codebook,
            label: row.label || row.codebook,
            family: row.family,
            library_document_id: row.library_document_id,
            document_id: readyDoc?.id || null,
            status: readyDoc?.status || null,
            ready: Boolean(readyDoc),
          };
        })
      );
      setCountryId((prev) => prev || countryRes.data?.[0]?.id || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load the library catalog');
    } finally {
      setLoadingDocuments(false);
    }
  }, [user?.id]);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  useEffect(() => {
    setProjectReport(loadProjectReport(user?.id));
  }, [user?.id]);

  useEffect(() => {
    if (!projectNotice) return;
    const timer = window.setTimeout(() => setProjectNotice(''), 4000);
    return () => window.clearTimeout(timer);
  }, [projectNotice]);

  const readyIdsForCatalog = (catalogId: string) =>
    editionsForDocument(editions, catalogId)
      .filter((edition) => edition.ready && edition.document_id)
      .map((edition) => edition.document_id as string);

  const toggleCatalogDocument = (catalogId: string) => {
    if (!documentIsReady(editions, catalogId)) return;
    const readyIds = readyIdsForCatalog(catalogId);
    const selected = selectedCatalogIds.includes(catalogId);
    setSelectedCatalogIds((current) =>
      selected ? current.filter((id) => id !== catalogId) : [...current, catalogId]
    );
    setSelectedDocumentIds((current) => {
      if (selected) {
        const drop = new Set(readyIds);
        return current.filter((id) => !drop.has(id));
      }
      return Array.from(new Set([...current, ...readyIds]));
    });
    setPlan(null);
    setReport(null);
  };

  const selectRecommendedCode = (codebookId: string) => {
    const matching = documents.filter((document) => document.codebook?.toUpperCase() === codebookId.toUpperCase());
    setSelectedDocumentIds((current) => Array.from(new Set([...current, ...matching.map((document) => document.id)])));
    setPlan(null);
    setReport(null);
  };

  const runPlan = async () => {
    if (!question.trim() || selectedDocumentIds.length === 0 || loading) return;
    setLoading(true);
    setError('');
    setReport(null);
    try {
      const response = await apiRequest('/api/v1/query/design-compliance/plan', {
        method: 'POST',
        body: JSON.stringify({
          discipline,
          question: question.trim(),
          document_ids: selectedDocumentIds,
          scoping_answers: scopingAnswers,
        }),
      });
      const data = await readApiJson(response);
      if (!response.ok) throw new Error(errorMessage(data, 'Planning failed'));
      setPlan(data as Plan);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Planning failed');
    } finally {
      setLoading(false);
    }
  };

  const runReport = async () => {
    if (!plan || plan.needs_scoping || plan.requirements.length === 0 || loading || reportBlocked) return;
    setLoading(true);
    setError('');
    try {
      const response = await apiRequest('/api/v1/query/design-compliance/run', {
        method: 'POST',
        body: JSON.stringify({
          discipline,
          question: question.trim(),
          document_ids: selectedDocumentIds,
          scoping_answers: scopingAnswers,
          plan,
        }),
      });
      const data = await readApiJson(response);
      if (!response.ok) throw new Error(errorMessage(data, 'Report generation failed'));
      if (
        response.status === 202 &&
        data &&
        typeof data === 'object' &&
        typeof (data as { job_id?: unknown }).job_id === 'string'
      ) {
        const result = await pollDesignComplianceJob((data as { job_id: string }).job_id);
        setReport((result as { report?: Report }).report as Report);
      } else {
        setReport((data as { report?: Report }).report as Report);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Report generation failed');
    } finally {
      setLoading(false);
    }
  };

  const liveScope = useMemo(() => {
    if (!plan || plan.needs_scoping || plan.requirements.length === 0) return null;
    return estimateClientScope(plan.requirements.length, selectedDocumentIds.length);
  }, [plan, selectedDocumentIds.length]);

  const scopeWarning =
    (liveScope && scopeReductionMessage(liveScope)) ||
    plan?.scope_warning ||
    (plan?.scope && scopeReductionMessage(plan.scope)) ||
    '';
  const reportBlocked = liveScope?.level === 'block' || plan?.scope?.level === 'block';

  const selectedCodeLabels = useMemo(() => {
    const labels = selectedDocumentIds
      .map((documentId) => {
        const document = documents.find((entry) => entry.id === documentId);
        if (!document) return '';
        return labelForCodebookId(document.codebook || '') || displayDocumentName(document);
      })
      .filter(Boolean);
    return Array.from(new Set(labels));
  }, [documents, selectedDocumentIds]);

  const exportContext = useMemo(() => {
    if (!report) return null;
    return {
      briefQuestion: question.trim(),
      discipline,
      scopingAnswers,
      codeLabels: selectedCodeLabels,
      report,
    };
  }, [report, question, discipline, scopingAnswers, selectedCodeLabels]);

  const projectSections = projectReport?.sections ?? [];
  const finalizedReport = isFinalizedReportCurrent(projectReport) ? projectReport?.finalized?.report ?? null : null;
  const finalizedIsStale = Boolean(projectReport?.finalized && !isFinalizedReportCurrent(projectReport));
  const canExportProject = projectSections.length === 1 || Boolean(finalizedReport);

  const statusCounts = report?.items.reduce(
    (counts, item) => {
      const status = normalizeReportStatus(item.status);
      return { ...counts, [status]: counts[status] + 1 };
    },
    { applies: 0, conditional: 0, not_applicable: 0, needs_code: 0, conflict: 0 }
  );

  const addCurrentRunToProject = () => {
    if (!report || !exportContext) return;
    const next = addSectionToProjectReport(user?.id, {
      briefQuestion: exportContext.briefQuestion,
      scopingAnswers: exportContext.scopingAnswers,
      discipline: exportContext.discipline,
      documentIds: selectedDocumentIds,
      codeLabels: exportContext.codeLabels,
      report,
    });
    setProjectReport(next);
    setProjectNotice(`Added to project report (${next.sections.length} section${next.sections.length === 1 ? '' : 's'}).`);
  };

  const handleRemoveProjectSection = (sectionId: string) => {
    const next = removeProjectSection(user?.id, sectionId);
    setProjectReport(next);
    if (!next) {
      setReportView('current');
      return;
    }
    if (reportView === 'final') setReportView('sections');
  };

  const handleClearProjectReport = () => {
    if (!projectReport?.sections.length) return;
    if (!window.confirm('Remove all saved report sections for this project?')) return;
    clearProjectReport(user?.id);
    setProjectReport(null);
    setReportView('current');
  };

  const handleFinalizeProjectReport = async () => {
    if (!projectReport || projectSections.length === 0 || finalizingProject) return;
    setFinalizingProject(true);
    setError('');
    try {
      const response = await apiRequest('/api/v1/query/design-compliance/finalize', {
        method: 'POST',
        body: JSON.stringify({
          discipline: projectReport.discipline,
          question: projectReport.briefQuestion,
          scoping_answers: projectReport.scopingAnswers,
          sections: projectSections.map((section) => ({
            id: section.id,
            label: section.label,
            code_labels: section.codeLabels,
            report: section.report,
          })),
        }),
      });
      const data = await readApiJson(response);
      if (!response.ok) throw new Error(errorMessage(data, 'Final report generation failed'));
      const resultReport = (data as { report?: Report }).report as Report;
      if (!resultReport?.report_markdown?.trim()) {
        throw new Error('Final report content was empty. Please try again.');
      }
      const next = saveFinalizedReport(user?.id, projectReport, {
        report: resultReport,
        section_labels: (data as { section_labels?: string[] }).section_labels,
        merged_from_sections: (data as { merged_from_sections?: number }).merged_from_sections,
      });
      setProjectReport(next);
      setReportView('final');
      setProjectNotice('Final unified report generated.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Final report generation failed');
    } finally {
      setFinalizingProject(false);
    }
  };

  const handleExportProjectPdf = async () => {
    if (!projectReport) return;
    if (!canExportProject) {
      setError('Generate the final report before exporting a multi-section project.');
      return;
    }
    setExportingPdf(true);
    setError('');
    try {
      await exportProjectReportAsPdf(projectReport);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExportingPdf(false);
    }
  };

  const handleExportCurrentRunPdf = async () => {
    if (!exportContext) return;
    setExportingPdf(true);
    setError('');
    try {
      await exportDesignPlanningAsPdf(exportContext);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExportingPdf(false);
    }
  };

  const handleExportCurrentRunJpeg = async () => {
    if (!exportContext) return;
    setExportingJpeg(true);
    setError('');
    try {
      await exportDesignPlanningAsJpeg(exportContext);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'JPG export failed');
    } finally {
      setExportingJpeg(false);
    }
  };

  const handleExportProjectJpeg = async () => {
    if (!projectReport) return;
    if (!canExportProject) {
      setError('Generate the final report before exporting a multi-section project.');
      return;
    }
    setExportingJpeg(true);
    setError('');
    try {
      await exportProjectReportAsJpeg(projectReport);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'JPG export failed');
    } finally {
      setExportingJpeg(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded-[26px] border border-white/70 bg-gradient-to-br from-[#0b1220] to-[#263245] p-6 text-white shadow-xl">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-white/10">
            <ClipboardCheck className="h-6 w-6 text-[#f1ddab]" />
          </div>
          <div>
            <p className="augusta-eyebrow text-[#f1ddab]">Planner-first briefing</p>
            <h2 className="mt-1 text-2xl font-semibold">Design Planning Report</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
              Give an early project brief — not drawings. Select library documents from the country tree, then
              Augusta Search identifies which requirements apply and what the design must address.
            </p>
          </div>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)]">
        <section className="space-y-5 rounded-[26px] border border-slate-200/80 bg-white/80 p-5 shadow-sm">
          <div>
            <label className="mb-2 block text-sm font-semibold text-slate-800" htmlFor="design-discipline">
              1. Your discipline
            </label>
            <select
              id="design-discipline"
              value={discipline}
              onChange={(event) => {
                setDiscipline(event.target.value as Discipline);
                setPlan(null);
                setReport(null);
              }}
              className="augusta-input w-full"
              disabled={loading}
            >
              {DISCIPLINES.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <label className="block text-sm font-semibold text-slate-800">2. Select library documents</label>
              <span className="text-xs font-medium text-slate-500">{selectedDocumentIds.length} selected</span>
            </div>
            {loadingDocuments ? (
              <p className="py-5 text-center text-sm text-slate-500">Loading library...</p>
            ) : countries.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-5 text-center text-sm text-slate-500">
                <FileText className="mx-auto mb-2 h-6 w-6 text-[#c9a45c]" />
                Library catalog is not ready yet. An admin must run combined_setup.sql (section 10b) and ingest editions.
              </div>
            ) : (
              <div className="max-h-[420px] overflow-y-auto">
                <LibraryTree
                  countries={countries}
                  types={libraryTypes}
                  documents={catalogDocuments}
                  editions={editions}
                  countryId={countryId}
                  typeId={typeId}
                  documentId={null}
                  selectedDocumentIds={selectedCatalogIds}
                  multiSelect
                  onCountryChange={(id) => {
                    setCountryId(id);
                    setTypeId(null);
                  }}
                  onSelectType={setTypeId}
                  onSelectDocument={toggleCatalogDocument}
                  showUnready
                  searchEnabled
                />
              </div>
            )}
          </div>

          <div>
            <label className="mb-2 block text-sm font-semibold text-slate-800" htmlFor="design-question">
              3. Your design question
            </label>
            <textarea
              id="design-question"
              value={question}
              onChange={(event) => {
                setQuestion(event.target.value);
                setPlan(null);
                setReport(null);
              }}
              className="augusta-input min-h-[150px] w-full resize-y"
              placeholder="Describe the project brief: building type, location, major systems, and design scope."
              disabled={loading}
            />
          </div>

          {scopeWarning && !report && (
            <div
              className={`rounded-2xl border p-3 text-sm ${
                reportBlocked
                  ? 'border-amber-300 bg-amber-50 text-amber-950'
                  : 'border-sky-200 bg-sky-50 text-sky-900'
              }`}
            >
              {scopeWarning}
            </div>
          )}

          {error && <div className="rounded-2xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>}

          {!plan ? (
            <button
              type="button"
              onClick={() => void runPlan()}
              disabled={loading || !question.trim() || selectedDocumentIds.length === 0}
              className="flex w-full items-center justify-center gap-2 rounded-2xl bg-[#0b1220] px-4 py-3 font-semibold text-white transition hover:bg-[#182033] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Play className="h-4 w-4" />
              {loading ? 'Planning review...' : 'Plan this review'}
            </button>
          ) : plan.needs_scoping ? (
            <button
              type="button"
              onClick={() => void runPlan()}
              disabled={loading || plan.scoping_questions.some((item) => !scopingAnswers[item.id]?.trim())}
              className="flex w-full items-center justify-center gap-2 rounded-2xl bg-[#0b1220] px-4 py-3 font-semibold text-white transition hover:bg-[#182033] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Play className="h-4 w-4" />
              {loading ? 'Updating plan...' : 'Continue planning'}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void runReport()}
              disabled={loading || plan.requirements.length === 0 || reportBlocked}
              className="flex w-full items-center justify-center gap-2 rounded-2xl bg-[#9a7a35] px-4 py-3 font-semibold text-white transition hover:bg-[#7c5f1e] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ClipboardCheck className="h-4 w-4" />
              {loading
                ? 'Generating report...'
                : reportBlocked
                  ? 'Too many codes — reduce selection'
                  : 'Generate complete report'}
            </button>
          )}
        </section>

        <section className="space-y-5">
          {plan?.needs_scoping && (
            <div className="rounded-[26px] border border-[#d6bf82] bg-[#fffaf0] p-5 shadow-sm">
              <h3 className="text-lg font-semibold text-slate-950">A few details are needed first</h3>
              <p className="mt-1 text-sm text-slate-600">
                These questions help the planner decide which requirements actually apply.
              </p>
              <div className="mt-4 space-y-4">
                {plan.scoping_questions.map((item) => (
                  <div key={item.id}>
                    <label className="mb-1 block text-sm font-semibold text-slate-800" htmlFor={item.id}>
                      {item.question}
                    </label>
                    {item.why && <p className="mb-2 text-xs text-slate-500">{item.why}</p>}
                    <input
                      id={item.id}
                      value={scopingAnswers[item.id] || ''}
                      onChange={(event) =>
                        setScopingAnswers((answers) => ({ ...answers, [item.id]: event.target.value }))
                      }
                      className="augusta-input w-full"
                      placeholder="Answer or enter Unknown"
                      disabled={loading}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {plan && !plan.needs_scoping && !report && (
            <div className="rounded-[26px] border border-slate-200/80 bg-white/80 p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-lg font-semibold text-slate-950">Review plan</h3>
                  <p className="mt-1 text-sm text-slate-600">
                    The planner created {plan.requirements.length} targeted questions
                    {liveScope
                      ? ` · ${selectedDocumentIds.length} codes selected · ≈ ${liveScope.estimated_rag_calls} searches`
                      : ''}
                    .
                  </p>
                </div>
                <ClipboardCheck className="h-5 w-5 text-[#9a7a35]" />
              </div>
              {scopeWarning && (
                <div
                  className={`mt-4 rounded-2xl border p-3 text-sm ${
                    reportBlocked
                      ? 'border-amber-300 bg-amber-50 text-amber-950'
                      : 'border-sky-200 bg-sky-50 text-sky-900'
                  }`}
                >
                  {scopeWarning}
                </div>
              )}
              <div className="mt-4 space-y-2">
                {plan.requirements.map((requirement) => (
                  <div key={requirement.id} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
                    <p className="text-xs font-semibold uppercase tracking-wide text-[#9a7a35]">{requirement.domain}</p>
                    <p className="mt-1 text-sm text-slate-700">{requirement.question}</p>
                  </div>
                ))}
              </div>
              {plan.recommended_codes.length > 0 && (
                <div className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 p-4">
                  <h4 className="font-semibold text-amber-950">Codes that may be needed</h4>
                  <div className="mt-2 space-y-3">
                    {plan.recommended_codes.map((recommendation) => {
                      const available = documents.some(
                        (document) => document.codebook?.toUpperCase() === recommendation.codebook_id.toUpperCase()
                      );
                      return (
                        <div key={recommendation.codebook_id} className="flex items-start justify-between gap-3 text-sm">
                          <div>
                            <p className="font-semibold text-amber-950">{recommendation.label}</p>
                            <p className="text-xs leading-5 text-amber-900/80">{recommendation.reason}</p>
                          </div>
                          {available ? (
                            <button
                              type="button"
                              onClick={() => selectRecommendedCode(recommendation.codebook_id)}
                              className="shrink-0 rounded-lg border border-amber-300 px-2 py-1 text-xs font-semibold text-amber-950 hover:bg-white"
                            >
                              Select
                            </button>
                          ) : (
                            <span className="shrink-0 text-xs font-semibold text-amber-800">Upload needed</span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  <p className="mt-3 text-xs text-amber-900">
                    You can add these codes and re-plan, or continue. Topics without the right code will be marked Code not selected.
                  </p>
                </div>
              )}
            </div>
          )}

          {(report || projectSections.length > 0) && (
            <div className="space-y-5">
              {projectSections.length > 0 && (
                <div className="rounded-[26px] border border-[#d6bf82]/60 bg-[#fffaf0] p-5 shadow-sm">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <Layers className="h-5 w-5 text-[#9a7a35]" />
                        <h3 className="text-lg font-semibold text-slate-950">Project report</h3>
                      </div>
                      <p className="mt-1 text-sm text-slate-600">
                        {projectSections.length} saved section{projectSections.length === 1 ? '' : 's'}. Merge them into
                        one final briefing when all code runs are done.
                      </p>
                      {finalizedIsStale && (
                        <p className="mt-2 text-xs font-medium text-amber-800">
                          Sections changed since the last final report — regenerate final report before export.
                        </p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {report && (
                        <button
                          type="button"
                          onClick={() => setReportView('current')}
                          className={`rounded-xl px-3 py-2 text-xs font-semibold transition ${
                            reportView === 'current'
                              ? 'bg-[#0b1220] text-white'
                              : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                          }`}
                        >
                          Current run
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => setReportView('sections')}
                        className={`rounded-xl px-3 py-2 text-xs font-semibold transition ${
                          reportView === 'sections'
                            ? 'bg-[#0b1220] text-white'
                            : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                        }`}
                      >
                        Sections ({projectSections.length})
                      </button>
                      <button
                        type="button"
                        onClick={() => setReportView('final')}
                        disabled={!finalizedReport}
                        className={`rounded-xl px-3 py-2 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                          reportView === 'final'
                            ? 'bg-[#0b1220] text-white'
                            : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                        }`}
                      >
                        Final report
                      </button>
                    </div>
                  </div>

                  <div className="mt-4 space-y-2">
                    {projectSections.map((section, index) => (
                      <div
                        key={section.id}
                        className="flex items-start justify-between gap-3 rounded-2xl border border-amber-100 bg-white px-3 py-2"
                      >
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-slate-900">
                            Part {index + 1}: {section.label}
                          </p>
                          <p className="text-xs text-slate-500">
                            {new Date(section.addedAt).toLocaleString(undefined, {
                              dateStyle: 'medium',
                              timeStyle: 'short',
                            })}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleRemoveProjectSection(section.id)}
                          className="shrink-0 rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-red-50 hover:text-red-700"
                          title="Remove this section"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                    ))}
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    {projectSections.length > 1 && (
                      <button
                        type="button"
                        onClick={() => void handleFinalizeProjectReport()}
                        disabled={finalizingProject}
                        className="inline-flex items-center gap-1.5 rounded-xl bg-[#9a7a35] px-3 py-2 text-xs font-semibold text-white hover:bg-[#7c5f1e] disabled:cursor-wait disabled:opacity-60"
                      >
                        <Sparkles className="h-3.5 w-3.5" />
                        {finalizingProject ? 'Generating final report…' : 'Generate final report'}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => void handleExportProjectPdf()}
                      disabled={!projectReport || !canExportProject || exportingPdf}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                      title={
                        canExportProject
                          ? 'Download unified final report as PDF'
                          : 'Generate the final report before exporting'
                      }
                    >
                      <Download className="h-3.5 w-3.5" />
                      {exportingPdf ? 'Creating PDF…' : 'Export final PDF'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleExportProjectJpeg()}
                      disabled={!projectReport || !canExportProject || exportingJpeg}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                      title={
                        canExportProject
                          ? 'Download a branded JPG for LinkedIn or social posts'
                          : 'Generate the final report before exporting'
                      }
                    >
                      <Image className="h-3.5 w-3.5" />
                      {exportingJpeg ? 'Creating JPG…' : 'Export final JPG'}
                    </button>
                    <button
                      type="button"
                      onClick={handleClearProjectReport}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      Clear project
                    </button>
                  </div>
                </div>
              )}

              {projectNotice && (
                <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
                  {projectNotice}
                </div>
              )}

              {reportView === 'current' && report && (
                <>
                  <div className="flex flex-wrap items-center gap-2 rounded-[26px] border border-slate-200/80 bg-white/90 p-4 shadow-sm">
                    <button
                      type="button"
                      onClick={addCurrentRunToProject}
                      className="inline-flex items-center gap-1.5 rounded-xl bg-[#9a7a35] px-3 py-2 text-xs font-semibold text-white hover:bg-[#7c5f1e]"
                    >
                      <FolderPlus className="h-3.5 w-3.5" />
                      Add to project report
                    </button>
                    <button
                      type="button"
                      onClick={() => exportContext && downloadDesignPlanningMarkdown(exportContext)}
                      disabled={!exportContext}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                    >
                      <Download className="h-3.5 w-3.5" />
                      Download Markdown
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleExportCurrentRunPdf()}
                      disabled={!exportContext || exportingPdf}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      title="Download this planning run as PDF"
                    >
                      <Download className="h-3.5 w-3.5" />
                      {exportingPdf ? 'Creating PDF…' : 'Export PDF'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleExportCurrentRunJpeg()}
                      disabled={!exportContext || exportingJpeg}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      title="Download a branded JPG for LinkedIn or social posts (1200px wide)"
                    >
                      <Image className="h-3.5 w-3.5" />
                      {exportingJpeg ? 'Creating JPG…' : 'Export JPG'}
                    </button>
                  </div>

                  {statusCounts && (
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
                      {(['applies', 'conditional', 'not_applicable', 'needs_code', 'conflict'] as const).map((status) => (
                        <div key={status} className={`rounded-2xl border p-3 ${STATUS_META[status].className}`}>
                          <p className="text-2xl font-semibold">{statusCounts[status]}</p>
                          <p className="text-xs font-semibold">{STATUS_META[status].label}</p>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="rounded-[26px] border border-slate-200/80 bg-white/90 p-5 shadow-sm">
                    <div
                      className="prose prose-sm max-w-none"
                      dangerouslySetInnerHTML={{ __html: renderPractitionerMarkdown(report.report_markdown) }}
                    />
                  </div>
                  <div className="rounded-[26px] border border-slate-200/80 bg-white/80 p-5 shadow-sm">
                    <h3 className="text-lg font-semibold text-slate-950">Design requirements</h3>
                    <div className="mt-4 space-y-3">
                      {report.items.map((item) => {
                        const status = normalizeReportStatus(item.status);
                        const meta = STATUS_META[status];
                        return (
                          <div key={item.requirement_id} className="rounded-2xl border border-slate-200 bg-white p-4">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <h4 className="font-semibold text-slate-900">{item.domain || item.requirement_id}</h4>
                              <span
                                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${meta.className}`}
                              >
                                {meta.icon}
                                {meta.label}
                              </span>
                            </div>
                            <p className="mt-2 text-sm text-slate-700">{item.summary}</p>
                            <p className="mt-2 text-xs leading-5 text-slate-500">
                              <span className="font-semibold text-slate-600">Why it applies: </span>
                              {item.reason}
                            </p>
                            <DesignScopeBlock scope={item.design_scope} fallbackActions={item.design_actions} />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </>
              )}

              {reportView === 'final' && finalizedReport && projectReport && (
                <div className="space-y-5">
                  <div className="rounded-[26px] border border-emerald-200/80 bg-emerald-50/70 p-5 shadow-sm">
                    <p className="text-xs font-semibold uppercase tracking-wide text-emerald-800">Unified final report</p>
                    <h3 className="mt-1 text-lg font-semibold text-slate-950">
                      {projectSections.length} planning runs merged · {finalizedReport.items.length} checklist topics
                    </h3>
                    <p className="mt-2 text-sm text-slate-600">
                      Generated{' '}
                      {projectReport.finalized?.finalizedAt
                        ? new Date(projectReport.finalized.finalizedAt).toLocaleString(undefined, {
                            dateStyle: 'medium',
                            timeStyle: 'short',
                          })
                        : 'recently'}
                      . Export this view for the junior engineer handoff.
                    </p>
                  </div>
                  <div className="rounded-[26px] border border-slate-200/80 bg-white/90 p-5 shadow-sm">
                    <div
                      className="prose prose-sm max-w-none"
                      dangerouslySetInnerHTML={{ __html: renderPractitionerMarkdown(finalizedReport.report_markdown) }}
                    />
                  </div>
                </div>
              )}

              {reportView === 'sections' && projectReport && (
                <div className="space-y-5">
                  <div className="rounded-[26px] border border-slate-200/80 bg-white/90 p-5 shadow-sm">
                    <h3 className="text-lg font-semibold text-slate-950">Project brief</h3>
                    <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{projectReport.briefQuestion}</p>
                  </div>
                  {projectSections.map((section, index) => (
                    <div key={section.id} className="space-y-4 rounded-[26px] border border-slate-200/80 bg-white/90 p-5 shadow-sm">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-[#9a7a35]">
                          Part {index + 1}
                        </p>
                        <h3 className="mt-1 text-lg font-semibold text-slate-950">{section.label}</h3>
                        <p className="mt-1 text-xs text-slate-500">
                          Added{' '}
                          {new Date(section.addedAt).toLocaleString(undefined, {
                            dateStyle: 'medium',
                            timeStyle: 'short',
                          })}
                        </p>
                      </div>
                      <div
                        className="prose prose-sm max-w-none"
                        dangerouslySetInnerHTML={{ __html: renderPractitionerMarkdown(section.report.report_markdown) }}
                      />
                      {section.report.items.length > 0 && (
                        <div>
                          <h4 className="text-base font-semibold text-slate-950">Design requirements</h4>
                          <div className="mt-3 space-y-3">
                            {section.report.items.map((item) => {
                              const status = normalizeReportStatus(item.status);
                              const meta = STATUS_META[status];
                              return (
                                <div
                                  key={`${section.id}-${item.requirement_id}`}
                                  className="rounded-2xl border border-slate-200 bg-white p-4"
                                >
                                  <div className="flex flex-wrap items-center justify-between gap-2">
                                    <h5 className="font-semibold text-slate-900">{item.domain || item.requirement_id}</h5>
                                    <span
                                      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${meta.className}`}
                                    >
                                      {meta.icon}
                                      {meta.label}
                                    </span>
                                  </div>
                                  <p className="mt-2 text-sm text-slate-700">{item.summary}</p>
                                  <DesignScopeBlock scope={item.design_scope} fallbackActions={item.design_actions} />
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

export default DesignCompliancePanel;
