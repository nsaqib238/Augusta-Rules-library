import React, { useState, useEffect } from 'react';
import { apiRequest } from '../../lib/api';
import { askQuestion } from '../../lib/askQuestion';
import { renderPractitionerMarkdown } from '../../lib/renderMarkdown';
import {
  OTHER_CODEBOOK,
  Discipline,
  codebooksForUserUploads,
  defaultUserCodebookForDiscipline,
  disciplineMeta,
  labelForCodebookId,
} from '../../lib/codebooks';

export interface PreparedQuery {
  codebook_id: string;
  codebook_label: string;
  original_question: string;
  rephrased_query: string;
  fts_terms: string[];
  vector_queries: string[];
  fuzzy_terms: string[];
  heading_clauses: string[];
  clause_topics: string[];
  rationale: string;
}

export interface ClauseHit {
  id: string;
  clause_number?: string;
  heading?: string;
  page_number?: number;
  text: string;
}

interface RetrievalMeta {
  chunk_pool?: number;
  returned?: number;
  signals?: Record<string, number>;
  vector_source?: string;
  rrf_ranking?: Array<Record<string, unknown>>;
}

interface PipelineTrace {
  total_ms?: number;
  steps?: Array<{ step: string; elapsed_ms?: number; [key: string]: unknown }>;
}

interface AskResponse {
  prepared: PreparedQuery;
  clauses: ClauseHit[];
  retrieval?: RetrievalMeta;
  trace?: PipelineTrace;
  answer?: { answer_markdown: string; cited_clauses?: string[]; confidence?: string; conclusion?: string };
}

type Step = 'idle' | 'prepare' | 'search' | 'answer' | 'done';

export interface RagQueryPanelProps {
  discipline: Discipline;
  codebook?: string;
  onCodebookChange?: (codebook: string) => void;
  resetKey?: string;
}

const RagQueryPanel: React.FC<RagQueryPanelProps> = ({
  discipline,
  codebook: controlledCodebook,
  onCodebookChange,
  resetKey,
}) => {
  const [internalCodebook, setInternalCodebook] = useState(defaultUserCodebookForDiscipline(discipline));
  const codebook = controlledCodebook ?? internalCodebook;
  const setCodebook = onCodebookChange ?? setInternalCodebook;

  const [customCodebook, setCustomCodebook] = useState('');
  const [question, setQuestion] = useState('');
  const [step, setStep] = useState<Step>('idle');
  const [error, setError] = useState('');
  const [prepared, setPrepared] = useState<PreparedQuery | null>(null);
  const [clauses, setClauses] = useState<ClauseHit[]>([]);
  const [retrievalMeta, setRetrievalMeta] = useState<RetrievalMeta>();
  const [pipelineTrace, setPipelineTrace] = useState<PipelineTrace | null>(null);
  const [showTrace, setShowTrace] = useState(true);
  const [answerMarkdown, setAnswerMarkdown] = useState('');
  const [answerMeta, setAnswerMeta] = useState<{ cited_clauses?: string[]; confidence?: string; conclusion?: string }>();
  const [runMode, setRunMode] = useState<'prepare_only' | 'full'>('full');

  useEffect(() => {
    if (controlledCodebook == null) {
      setInternalCodebook(defaultUserCodebookForDiscipline(discipline));
    }
  }, [discipline, controlledCodebook]);

  useEffect(() => {
    setCustomCodebook('');
    setQuestion('');
    setStep('idle');
    setError('');
    setPrepared(null);
    setClauses([]);
    setRetrievalMeta(undefined);
    setPipelineTrace(null);
    setAnswerMarkdown('');
    setAnswerMeta(undefined);
  }, [discipline, resetKey]);

  const codebookOptions = codebooksForUserUploads(discipline);

  const reset = () => {
    setStep('idle');
    setError('');
    setPrepared(null);
    setClauses([]);
    setRetrievalMeta(undefined);
    setPipelineTrace(null);
    setAnswerMarkdown('');
    setAnswerMeta(undefined);
  };

  const buildBody = () => ({
    question: question.trim(),
    codebook_id: codebook,
    ...(codebook === OTHER_CODEBOOK
      ? { codebook_custom: customCodebook.trim(), codebook_label: customCodebook.trim() }
      : {}),
  });

  const handlePrepare = async () => {
    if (!question.trim()) {
      setError('Enter a question.');
      return;
    }
    if (codebook === OTHER_CODEBOOK && !customCodebook.trim()) {
      setError('Enter a code name for Other.');
      return;
    }
    reset();
    setStep('prepare');
    setError('');
    try {
      const res = await apiRequest('/api/v1/query/prepare', {
        method: 'POST',
        body: JSON.stringify(buildBody()),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Prepare failed');
      setPrepared(data.prepared);
      setPipelineTrace(data.trace || null);
      setStep('done');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Prepare failed');
      setStep('idle');
    }
  };

  const handleAsk = async () => {
    if (!question.trim()) {
      setError('Enter a question.');
      return;
    }
    if (codebook === OTHER_CODEBOOK && !customCodebook.trim()) {
      setError('Enter a code name for Other.');
      return;
    }
    reset();
    setStep('prepare');
    setError('');
    try {
      setStep('search');
      const data: AskResponse = await askQuestion({
        ...buildBody(),
        skip_answer: runMode === 'prepare_only',
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
        setStep('done');
      } else {
        setStep(runMode === 'prepare_only' ? 'done' : 'answer');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ask failed');
      setStep('idle');
    }
  };

  const stepLabel =
    step === 'prepare'
      ? 'Preparing search query with OpenAI…'
      : step === 'search'
        ? 'Searching clauses (FTS · vector · fuzzy · heading → RRF)…'
        : step === 'answer'
          ? 'Synthesising answer in parcels…'
          : '';

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm sm:col-span-2">
          <span className="text-xs font-semibold uppercase tracking-widest text-slate-500">Codebook</span>
          <select
            className="augusta-input mt-2 w-full"
            value={codebook}
            onChange={(e) => setCodebook(e.target.value)}
          >
            <optgroup label={disciplineMeta(discipline).name}>
              {codebookOptions.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </optgroup>
            <option value={OTHER_CODEBOOK}>Other (custom)</option>
          </select>
        </label>
      </div>

      {codebook === OTHER_CODEBOOK && (
        <label className="block text-sm">
          <span className="text-xs font-semibold uppercase tracking-widest text-slate-500">Custom code name</span>
          <input
            className="augusta-input mt-2 w-full"
            value={customCodebook}
            onChange={(e) => setCustomCodebook(e.target.value)}
            placeholder="e.g. project site addendum or local code note"
          />
        </label>
      )}

      <label className="block text-sm">
        <span className="text-xs font-semibold uppercase tracking-widest text-slate-500">Your question</span>
        <textarea
          className="augusta-input mt-2 min-h-[120px] w-full"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. What clearance is required around a switchboard in a domestic installation?"
        />
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="radio" checked={runMode === 'full'} onChange={() => setRunMode('full')} />
          Full pipeline (search + answer)
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="radio" checked={runMode === 'prepare_only'} onChange={() => setRunMode('prepare_only')} />
          Prepare + search only (no answer)
        </label>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="augusta-button-secondary"
          disabled={step !== 'idle' && step !== 'done'}
          onClick={handlePrepare}
        >
          Step 1 — Prepare query
        </button>
        <button
          type="button"
          className="augusta-button-primary"
          disabled={step !== 'idle' && step !== 'done'}
          onClick={handleAsk}
        >
          {runMode === 'full' ? 'Ask (full pipeline)' : 'Prepare + search'}
        </button>
      </div>

      {stepLabel && <p className="text-sm font-medium text-amber-800">{stepLabel}</p>}
      {error && <p className="text-sm text-red-700">{error}</p>}

      {pipelineTrace?.steps && pipelineTrace.steps.length > 0 && (
        <section className="rounded-xl border border-indigo-200 bg-indigo-50/40 p-4">
          <button
            type="button"
            className="flex w-full items-center justify-between text-left"
            onClick={() => setShowTrace((v) => !v)}
          >
            <h3 className="font-semibold text-slate-900">
              Pipeline log
              {pipelineTrace.total_ms != null && (
                <span className="ml-2 text-xs font-normal text-slate-500">({pipelineTrace.total_ms} ms)</span>
              )}
            </h3>
            <span className="text-xs text-indigo-700">{showTrace ? 'Hide' : 'Show'}</span>
          </button>
          {showTrace && (
            <ol className="mt-3 max-h-[520px] space-y-3 overflow-y-auto text-xs">
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

      {prepared && (
        <section className="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
          <h3 className="font-semibold text-slate-900">Prepared for RAG</h3>
          <p className="mt-1 text-sm text-slate-600">
            <span className="font-medium">Code:</span>{' '}
            {prepared.codebook_label || labelForCodebookId(prepared.codebook_id)}
          </p>
          <p className="mt-2 text-sm">
            <span className="font-medium">Rephrased:</span> {prepared.rephrased_query}
          </p>
          {prepared.rationale && <p className="mt-1 text-sm text-slate-600">{prepared.rationale}</p>}
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

      {clauses.length > 0 && (
        <section>
          <h3 className="font-semibold text-slate-900">
            Retrieved clauses ({clauses.length}
            {retrievalMeta?.chunk_pool != null ? ` from pool of ${retrievalMeta.chunk_pool}` : ''})
          </h3>
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
              <summary className="cursor-pointer font-medium text-slate-600">RRF ranking (top selected)</summary>
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
          <ul className="mt-3 max-h-[480px] space-y-3 overflow-y-auto">
            {clauses.map((c, i) => (
              <li key={c.id} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
                <div className="font-medium text-slate-800">
                  #{i + 1}{' '}
                  {c.clause_number ? `Clause ${c.clause_number}` : 'Clause'}
                  {c.heading ? ` — ${c.heading}` : ''}
                  {c.page_number != null ? ` (p.${c.page_number})` : ''}
                </div>
                <pre className="mt-2 whitespace-pre-wrap font-sans text-slate-700">{c.text}</pre>
              </li>
            ))}
          </ul>
        </section>
      )}

      {answerMarkdown && (
        <section className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-semibold text-slate-900">Answer</h3>
            {answerMeta?.confidence && (
              <span className="rounded-full border border-emerald-200 bg-white px-2 py-0.5 text-xs font-semibold capitalize text-emerald-800">
                {answerMeta.confidence} confidence
              </span>
            )}
          </div>
          <div
            className="prose prose-sm mt-3 max-w-none text-slate-800"
            dangerouslySetInnerHTML={{ __html: renderPractitionerMarkdown(answerMarkdown) }}
          />
        </section>
      )}
    </div>
  );
};

export default RagQueryPanel;
