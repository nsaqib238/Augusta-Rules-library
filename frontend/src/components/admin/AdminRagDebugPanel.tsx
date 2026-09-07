import React, { useEffect, useState } from 'react';
import { Send, Info, FlaskConical } from 'lucide-react';
import { apiRequest } from '../../lib/api';
import { askQuestion } from '../../lib/askQuestion';
import { renderPractitionerMarkdown } from '../../lib/renderMarkdown';
import {
  OTHER_CODEBOOK,
  Discipline,
  CODEBOOKS,
  codebooksForUserUploads,
  defaultUserCodebookForDiscipline,
  disciplineMeta,
  labelForCodebookId,
} from '../../lib/codebooks';
import type { ClauseHit, PreparedQuery } from '../rag/RagQueryPanel';

interface RetrievalMeta {
  chunk_pool?: number;
  returned?: number;
  signals?: Record<string, number>;
  vector_source?: string;
  rrf_ranking?: Array<Record<string, unknown>>;
  message?: string;
}

interface PipelineTrace {
  total_ms?: number;
  steps?: Array<{ step: string; elapsed_ms?: number; [key: string]: unknown }>;
}

interface AdminRagDebugPanelProps {
  discipline: Discipline;
  selectedDocumentId?: string;
  selectedDocumentName?: string;
  selectedCodebook?: string;
  selectedCodebookSource?: string;
  resetKey?: string;
}

type RunMode = 'full' | 'search_only' | 'prepare_only';

const AdminRagDebugPanel: React.FC<AdminRagDebugPanelProps> = ({
  discipline,
  selectedDocumentId,
  selectedDocumentName,
  selectedCodebook,
  selectedCodebookSource,
  resetKey,
}) => {
  const [codebook, setCodebook] = useState(defaultUserCodebookForDiscipline(discipline));
  const [customCodebook, setCustomCodebook] = useState('');
  const [question, setQuestion] = useState('');
  const [runMode, setRunMode] = useState<RunMode>('full');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastQuestion, setLastQuestion] = useState('');
  const [prepared, setPrepared] = useState<PreparedQuery | null>(null);
  const [clauses, setClauses] = useState<ClauseHit[]>([]);
  const [retrievalMeta, setRetrievalMeta] = useState<RetrievalMeta>();
  const [pipelineTrace, setPipelineTrace] = useState<PipelineTrace | null>(null);
  const [showTrace, setShowTrace] = useState(true);
  const [answerMarkdown, setAnswerMarkdown] = useState('');
  const [answerMeta, setAnswerMeta] = useState<{
    cited_clauses?: string[];
    confidence?: string;
    conclusion?: string;
  }>();

  const codebookOptions = codebooksForUserUploads(discipline);

  const activeCodebookLabel =
    codebook === OTHER_CODEBOOK
      ? customCodebook.trim() || 'Custom codebook'
      : labelForCodebookId(codebook);

  const clearResults = () => {
    setError('');
    setLastQuestion('');
    setPrepared(null);
    setClauses([]);
    setRetrievalMeta(undefined);
    setPipelineTrace(null);
    setAnswerMarkdown('');
    setAnswerMeta(undefined);
  };

  useEffect(() => {
    setCodebook(defaultUserCodebookForDiscipline(discipline));
    setCustomCodebook('');
    setQuestion('');
    clearResults();
  }, [discipline, resetKey]);

  useEffect(() => {
    if (!selectedCodebook) return;
    const known = CODEBOOKS.find((c) => c.id === selectedCodebook);
    if (known) {
      setCodebook(known.id);
      setCustomCodebook('');
    } else {
      setCodebook(OTHER_CODEBOOK);
      setCustomCodebook(selectedCodebookSource || selectedCodebook);
    }
  }, [selectedCodebook, selectedCodebookSource]);

  const buildBody = () => {
    const payload: Record<string, string | boolean> = {
      question: question.trim(),
      codebook_id: codebook,
    };
    if (selectedDocumentId) payload.document_id = selectedDocumentId;
    if (codebook === OTHER_CODEBOOK) {
      payload.codebook_custom = customCodebook.trim();
      payload.codebook_label = customCodebook.trim();
    } else {
      payload.codebook_label = labelForCodebookId(codebook);
    }
    return payload;
  };

  const runPipeline = async (mode: RunMode) => {
    if (!question.trim()) {
      setError('Enter a question.');
      return;
    }
    if (codebook === OTHER_CODEBOOK && !customCodebook.trim()) {
      setError('Enter a custom code name.');
      return;
    }

    setLoading(true);
    setError('');
    clearResults();
    setLastQuestion(question.trim());

    try {
      if (mode === 'prepare_only') {
        const res = await apiRequest('/api/v1/query/prepare', {
          method: 'POST',
          body: JSON.stringify(buildBody()),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Prepare failed');
        setPrepared(data.prepared);
        setPipelineTrace(data.trace || null);
        return;
      }

      const data = await askQuestion({
        ...buildBody(),
        skip_answer: mode === 'search_only',
      });

      setPrepared(data.prepared);
      setClauses(data.clauses || []);
      setRetrievalMeta(data.retrieval);
      setPipelineTrace(data.trace || null);
      if (data.answer?.answer_markdown) {
        setAnswerMarkdown(data.answer.answer_markdown);
        setAnswerMeta({
          cited_clauses: data.answer.cited_clauses,
          confidence: data.answer.confidence,
          conclusion: data.answer.conclusion,
        });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Pipeline failed');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void runPipeline(runMode);
  };

  const canRun =
    question.trim().length > 0 &&
    !loading &&
    (codebook !== OTHER_CODEBOOK || customCodebook.trim().length > 0);

  const hasResults = Boolean(prepared || clauses.length > 0 || answerMarkdown || pipelineTrace);

  return (
    <div className="flex flex-col rounded-[28px] border border-white/70 bg-white/82 shadow-[0_22px_70px_rgba(15,23,42,0.10)] backdrop-blur-xl">
      <div className="border-b border-slate-200/70 bg-gradient-to-r from-[#0b1220] to-[#1f2937] px-6 py-5 text-white">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center space-x-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-white/10 bg-white/10 shadow-lg backdrop-blur">
              <FlaskConical className="h-6 w-6 text-[#f1ddab]" />
            </div>
            <div>
              <h2 className="text-xl font-semibold tracking-tight text-white">RAG pipeline debugger</h2>
              {selectedDocumentName ? (
                <p className="text-sm font-medium text-slate-300">Document: {selectedDocumentName}</p>
              ) : (
                <p className="text-sm font-medium text-amber-200">No document selected — search uses all ready docs for codebook</p>
              )}
              <p className="text-xs text-slate-400">Code for LLM: {activeCodebookLabel}</p>
            </div>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <select
              className="min-w-[220px] rounded-2xl border border-white/15 bg-white/10 px-3 py-2 text-sm text-white backdrop-blur focus:border-[#c9a45c] focus:outline-none focus:ring-4 focus:ring-[#f1ddab]/25"
              value={codebook}
              onChange={(e) => setCodebook(e.target.value)}
              disabled={loading}
            >
              <optgroup label={disciplineMeta(discipline).name}>
                {codebookOptions.map((c) => (
                  <option key={c.id} value={c.id} className="text-slate-900">
                    {c.label}
                  </option>
                ))}
              </optgroup>
              <option value={OTHER_CODEBOOK} className="text-slate-900">
                Other (custom)
              </option>
            </select>
            {codebook === OTHER_CODEBOOK && (
              <input
                type="text"
                value={customCodebook}
                onChange={(e) => setCustomCodebook(e.target.value)}
                placeholder="Custom code name"
                className="min-w-[200px] rounded-2xl border border-white/15 bg-white/10 px-3 py-2 text-sm text-white placeholder:text-slate-400"
                disabled={loading}
              />
            )}
          </div>
        </div>
      </div>

      <div className="flex flex-col">
        <div className="space-y-4 p-6">
          {!hasResults && !loading && (
            <div className="py-10 text-center text-slate-500">
              <p className="text-sm font-medium text-slate-700">Run a test question to inspect each pipeline stage.</p>
              <p className="mx-auto mt-2 max-w-lg text-xs leading-relaxed">
                You will see prepare (LLM rephrase + search signals), retrieved clauses, final answer, and the full trace log.
              </p>
            </div>
          )}

          {loading && (
            <div className="rounded-2xl border border-amber-200 bg-amber-50/80 px-4 py-3 text-sm font-medium text-amber-900">
              Running pipeline… prepare → multi-signal search → answer
            </div>
          )}

          {error && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
          )}

          {lastQuestion && (
            <section className="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
              <h3 className="text-xs font-semibold uppercase tracking-widest text-slate-500">Question</h3>
              <p className="mt-2 text-sm font-medium text-slate-900">{lastQuestion}</p>
            </section>
          )}

          {prepared && (
            <section className="rounded-xl border border-violet-200 bg-violet-50/40 p-4">
              <h3 className="font-semibold text-slate-900">Step 1 — Prepare (LLM)</h3>
              <p className="mt-1 text-sm text-slate-600">
                <span className="font-medium">Code:</span>{' '}
                {prepared.codebook_label || labelForCodebookId(prepared.codebook_id)}
              </p>
              <p className="mt-2 text-sm">
                <span className="font-medium">Rephrased query:</span> {prepared.rephrased_query}
              </p>
              {prepared.rationale && (
                <p className="mt-1 text-sm text-slate-600">
                  <span className="font-medium">Rationale:</span> {prepared.rationale}
                </p>
              )}
              <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                <div>
                  <span className="font-semibold uppercase text-slate-500">FTS terms</span>
                  <p>{prepared.fts_terms.join(' · ') || '—'}</p>
                </div>
                <div>
                  <span className="font-semibold uppercase text-slate-500">Vector paraphrases</span>
                  <p>{prepared.vector_queries.join(' · ') || '—'}</p>
                </div>
                <div>
                  <span className="font-semibold uppercase text-slate-500">Fuzzy</span>
                  <p>{prepared.fuzzy_terms.join(' · ') || '—'}</p>
                </div>
                <div>
                  <span className="font-semibold uppercase text-slate-500">Headings / clauses</span>
                  <p>{prepared.heading_clauses.join(' · ') || '—'}</p>
                </div>
              </div>
            </section>
          )}

          {(clauses.length > 0 || retrievalMeta?.message) && (
            <section className="rounded-xl border border-blue-200 bg-blue-50/30 p-4">
              <h3 className="font-semibold text-slate-900">
                Step 2 — Retrieved clauses ({clauses.length}
                {retrievalMeta?.chunk_pool != null ? ` / pool ${retrievalMeta.chunk_pool}` : ''})
              </h3>
              {retrievalMeta?.message && (
                <p className="mt-1 text-sm text-amber-800">{retrievalMeta.message}</p>
              )}
              {retrievalMeta?.signals && (
                <p className="mt-1 text-xs text-slate-500">
                  Signals:{' '}
                  {Object.entries(retrievalMeta.signals)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(', ')}
                  {retrievalMeta.vector_source && ` · vector=${retrievalMeta.vector_source}`}
                </p>
              )}
              {retrievalMeta?.rrf_ranking && retrievalMeta.rrf_ranking.length > 0 && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer font-medium text-slate-600">RRF ranking</summary>
                  <ul className="mt-2 space-y-1 text-slate-600">
                    {retrievalMeta.rrf_ranking.map((r, i) => (
                      <li key={i}>
                        #{String(r.rank)} score={String(r.score)} clause {String(r.clause_number || '—')}{' '}
                        {r.heading ? `— ${String(r.heading)}` : ''}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <ul className="mt-3 space-y-2">
                {clauses.map((c, i) => (
                  <li key={c.id} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
                    <div className="font-medium text-slate-800">
                      #{i + 1}{' '}
                      {c.clause_number ? `Clause ${c.clause_number}` : 'Clause'}
                      {c.heading ? ` — ${c.heading}` : ''}
                      {c.page_number != null ? ` (p.${c.page_number})` : ''}
                    </div>
                    <pre className="mt-2 whitespace-pre-wrap font-sans text-xs text-slate-700">{c.text}</pre>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {answerMarkdown && (
            <section className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-semibold text-slate-900">Step 3 — Answer (LLM)</h3>
                {answerMeta?.confidence && (
                  <span className="rounded-full border border-emerald-200 bg-white px-2 py-0.5 text-xs font-semibold capitalize text-emerald-800">
                    {answerMeta.confidence} confidence
                  </span>
                )}
              </div>
              {answerMeta?.conclusion && (
                <p className="mt-2 text-xs font-medium text-emerald-900">Outcome: {answerMeta.conclusion}</p>
              )}
              <div
                className="prose prose-sm mt-3 max-w-none text-slate-800"
                dangerouslySetInnerHTML={{ __html: renderPractitionerMarkdown(answerMarkdown) }}
              />
            </section>
          )}

          {pipelineTrace?.steps && pipelineTrace.steps.length > 0 && (
            <section className="rounded-xl border border-indigo-200 bg-indigo-50/40 p-4">
              <button
                type="button"
                className="flex w-full items-center justify-between text-left"
                onClick={() => setShowTrace((v) => !v)}
              >
                <h3 className="font-semibold text-slate-900">
                  Pipeline trace
                  {pipelineTrace.total_ms != null && (
                    <span className="ml-2 text-xs font-normal text-slate-500">({pipelineTrace.total_ms} ms)</span>
                  )}
                </h3>
                <span className="text-xs text-indigo-700">{showTrace ? 'Hide' : 'Show'}</span>
              </button>
              {showTrace && (
                <ol className="mt-3 max-h-[420px] space-y-3 overflow-y-auto text-xs">
                  {pipelineTrace.steps.map((s, i) => (
                    <li key={i} className="rounded-lg border border-indigo-100 bg-white p-3">
                      <div className="font-semibold text-indigo-900">
                        {i + 1}. {s.step}
                        {s.elapsed_ms != null && (
                          <span className="ml-2 font-normal text-slate-500">+{s.elapsed_ms} ms</span>
                        )}
                      </div>
                      <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-slate-700">
                        {JSON.stringify(
                          Object.fromEntries(Object.entries(s).filter(([k]) => k !== 'step' && k !== 'elapsed_ms')),
                          null,
                          2
                        )}
                      </pre>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          )}
        </div>

        <div className="border-t border-slate-200/80 bg-gradient-to-r from-[#fffaf1] to-slate-50 p-6">
          <div className="mb-3 flex flex-wrap items-center gap-4 text-sm text-slate-600">
            <span className="text-xs font-semibold uppercase tracking-widest text-slate-500">Run mode</span>
            <label className="flex items-center gap-2">
              <input type="radio" checked={runMode === 'full'} onChange={() => setRunMode('full')} />
              Full (prepare + search + answer)
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" checked={runMode === 'search_only'} onChange={() => setRunMode('search_only')} />
              Prepare + search only
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" checked={runMode === 'prepare_only'} onChange={() => setRunMode('prepare_only')} />
              Prepare only
            </label>
          </div>

          <div className="mb-3 flex flex-wrap gap-2">
            <button
              type="button"
              className="augusta-button-secondary text-xs"
              disabled={!canRun}
              onClick={() => void runPipeline('prepare_only')}
            >
              Step 1 — Prepare
            </button>
            <button
              type="button"
              className="augusta-button-secondary text-xs"
              disabled={!canRun}
              onClick={() => void runPipeline('search_only')}
            >
              Steps 1–2 — Search
            </button>
            <button
              type="button"
              className="augusta-button-primary text-xs"
              disabled={!canRun}
              onClick={() => void runPipeline('full')}
            >
              Full pipeline
            </button>
          </div>

          <div className="mb-3 flex items-start gap-2.5 rounded-2xl border border-[#f1ddab]/70 bg-white/70 p-3 text-sm">
            <Info className="mt-0.5 h-4 w-4 flex-shrink-0 text-[#9a7a35]" />
            <p className="leading-relaxed text-slate-700">
              Same API as user Q&A (<code className="text-xs">/api/v1/query/ask</code>). Select a test PDF in the
              library to scope retrieval to one document.
            </p>
          </div>

          <form onSubmit={handleSubmit} className="flex space-x-4">
            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Test question…"
              className="augusta-input flex-1"
              disabled={loading}
            />
            <button
              type="submit"
              disabled={!canRun}
              className="rounded-2xl bg-[#0b1220] px-6 py-3 font-semibold text-white shadow-lg shadow-slate-900/20 transition hover:-translate-y-0.5 hover:bg-[#182033] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send className="h-5 w-5" />
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};

export default AdminRagDebugPanel;
