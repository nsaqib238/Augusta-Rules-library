import React, { useState, useEffect, useRef } from 'react';
import { MessageSquare, Send, Brain, Clock, BookOpen, Info } from 'lucide-react';
import { askQuestion } from '../../lib/askQuestion';
import { renderPractitionerMarkdown } from '../../lib/renderMarkdown';
import {
  OTHER_CODEBOOK,
  Discipline,
  CODEBOOKS,
  codebooksForDiscipline,
  defaultCodebookForDiscipline,
  defaultUserCodebookForDiscipline,
  codebooksForUserUploads,
  labelForCodebookId,
  disciplineMeta,
  SharedLibraryFamily,
} from '../../lib/codebooks';
import { QaExportProfile } from '../../lib/qaAnswerExport';
import { QaExportActions } from './QaExportActions';

interface ClauseHit {
  id: string;
  clause_number?: string;
  heading?: string;
  page_number?: number;
  text: string;
}

interface ChatMessage {
  id: string;
  type: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  sources?: ClauseHit[];
  confidence?: string;
  conclusion?: string;
  rephrasedQuery?: string;
  retryAttempted?: boolean;
  retryUsed?: boolean;
  error?: boolean;
}

interface LibraryEditionOption {
  codebook: string;
  label: string;
}

interface QaChatPanelProps {
  discipline: Discipline;
  userUploadsOnly?: boolean;
  selectedDocumentId?: string;
  selectedDocumentName?: string;
  selectedCodebook?: string;
  selectedCodebookSource?: string;
  exportProfile?: QaExportProfile;
  libraryFamily?: SharedLibraryFamily;
  libraryEditions?: LibraryEditionOption[];
  onLibraryEditionChange?: (codebook: string) => void;
}

function confidenceToExportValue(confidence?: string): number | undefined {
  if (!confidence) return undefined;
  const map: Record<string, number> = { high: 0.9, medium: 0.6, low: 0.3 };
  return map[confidence.toLowerCase()];
}

const QaChatPanel: React.FC<QaChatPanelProps> = ({
  discipline,
  userUploadsOnly = false,
  selectedDocumentId,
  selectedDocumentName,
  selectedCodebook,
  selectedCodebookSource,
  exportProfile = 'qna',
  libraryFamily,
  libraryEditions,
  onLibraryEditionChange,
}) => {
  const isLibraryMode = Boolean(libraryEditions);
  const [codebook, setCodebook] = useState(
    userUploadsOnly ? defaultUserCodebookForDiscipline(discipline) : defaultCodebookForDiscipline(discipline)
  );
  const [customCodebook, setCustomCodebook] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState('');
  const [expandedSources, setExpandedSources] = useState<Record<string, boolean>>({});
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const codebookOptions = userUploadsOnly ? codebooksForUserUploads(discipline) : codebooksForDiscipline(discipline);

  const selectedLibraryEdition = libraryEditions?.find((e) => e.codebook === codebook);

  const activeCodebookLabel = isLibraryMode
    ? selectedLibraryEdition?.label || selectedCodebookSource || selectedDocumentName || codebook
    : codebook === OTHER_CODEBOOK
      ? customCodebook.trim() || 'Custom codebook'
      : labelForCodebookId(codebook);

  const documentSubtitle = selectedDocumentName
    ? `${activeCodebookLabel} — ${selectedDocumentName}`
    : activeCodebookLabel;

  useEffect(() => {
    if (isLibraryMode) {
      if (selectedCodebook) {
        setCodebook(selectedCodebook);
      } else if (libraryEditions?.[0]) {
        setCodebook(libraryEditions[0].codebook);
      }
      setCustomCodebook('');
      return;
    }
    setCodebook(
      userUploadsOnly ? defaultUserCodebookForDiscipline(discipline) : defaultCodebookForDiscipline(discipline)
    );
    setCustomCodebook('');
  }, [discipline, userUploadsOnly, isLibraryMode, libraryEditions, selectedCodebook]);

  useEffect(() => {
    if (isLibraryMode) return;
    if (!selectedCodebook) return;
    const known = CODEBOOKS.find((c) => c.id === selectedCodebook);
    if (known) {
      setCodebook(known.id);
      setCustomCodebook('');
    } else {
      setCodebook(OTHER_CODEBOOK);
      setCustomCodebook(selectedCodebookSource || selectedCodebook);
    }
  }, [isLibraryMode, selectedCodebook, selectedCodebookSource]);

  useEffect(() => {
    setMessages([]);
    setInputValue('');
    setExpandedSources({});
  }, [selectedDocumentId, discipline]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  const toggleSources = (messageId: string) => {
    setExpandedSources((prev) => ({
      ...prev,
      [messageId]: !(prev[messageId] ?? false),
    }));
  };

  const formatTimestamp = (timestamp: Date) =>
    timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  const buildAskPayload = (question: string) => {
    const payload: Record<string, string> = {
      question,
      codebook_id: codebook,
    };
    if (selectedDocumentId) {
      payload.document_id = selectedDocumentId;
    }
    if (isLibraryMode) {
      payload.codebook_label = activeCodebookLabel;
    } else if (codebook === OTHER_CODEBOOK) {
      payload.codebook_custom = customCodebook.trim();
      payload.codebook_label = customCodebook.trim();
    } else {
      payload.codebook_label = labelForCodebookId(codebook);
    }
    return payload;
  };

  const handleLibraryCodebookChange = (nextCodebook: string) => {
    setCodebook(nextCodebook);
    onLibraryEditionChange?.(nextCodebook);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const question = inputValue.trim();
    if (!question || isLoading || !selectedDocumentId) return;
    if (codebook === OTHER_CODEBOOK && !customCodebook.trim()) return;

    const userMessage: ChatMessage = {
      id: `${Date.now()}-user`,
      type: 'user',
      content: question,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsLoading(true);

    try {
      const data = await askQuestion(buildAskPayload(question), {
        onStatus: ({ status, queuePosition }) => {
          if (status === 'queued') {
            setLoadingStatus(
              queuePosition && queuePosition > 1
                ? `Queued (position ${queuePosition})...`
                : 'Queued, waiting for a free slot...'
            );
          } else {
            setLoadingStatus('Answering your question...');
          }
        },
      });

      const assistantMessage: ChatMessage = {
        id: `${Date.now()}-assistant`,
        type: 'assistant',
        content: data.answer?.answer_markdown || 'No answer was generated.',
        timestamp: new Date(),
        sources: data.clauses || [],
        confidence: data.answer?.confidence,
        conclusion: data.answer?.conclusion,
        rephrasedQuery: data.prepared?.rephrased_query,
        retryAttempted: Boolean(data.retry?.attempted),
        retryUsed: Boolean(data.retry?.used),
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      const assistantMessage: ChatMessage = {
        id: `${Date.now()}-error`,
        type: 'assistant',
        content:
          error instanceof Error
            ? error.message
            : 'Sorry, I encountered an error while processing your question. Please try again.',
        timestamp: new Date(),
        error: true,
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } finally {
      setIsLoading(false);
      setLoadingStatus('');
    }
  };

  const canSubmit = Boolean(
    inputValue.trim().length > 0 &&
      !isLoading &&
      selectedDocumentId &&
      (isLibraryMode || codebook !== OTHER_CODEBOOK || customCodebook.trim().length > 0)
  );

  const libraryDropdownLabel = 'Library editions';

  return (
    <div className="flex flex-col rounded-[28px] border border-white/70 bg-white/82 shadow-[0_22px_70px_rgba(15,23,42,0.10)] backdrop-blur-xl">
      <div className="border-b border-slate-200/70 bg-gradient-to-r from-[#0b1220] to-[#1f2937] px-6 py-5 text-white">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center space-x-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-white/10 bg-white/10 shadow-lg backdrop-blur">
              <MessageSquare className="h-6 w-6 text-[#f1ddab]" />
            </div>
            <div>
              <h2 className="text-xl font-semibold tracking-tight text-white">Augusta Search Q&A</h2>
              {selectedDocumentName ? (
                <p className="text-sm font-medium text-slate-300">Document: {selectedDocumentName}</p>
              ) : (
                <p className="text-sm font-medium text-slate-400">Select a document to begin</p>
              )}
              <p className="text-xs text-slate-400">Code for LLM: {activeCodebookLabel}</p>
            </div>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            {isLibraryMode ? (
              <select
                className="min-w-[280px] rounded-2xl border border-white/15 bg-white/10 px-3 py-2 text-sm text-white backdrop-blur focus:border-[#c9a45c] focus:outline-none focus:ring-4 focus:ring-[#f1ddab]/25"
                value={codebook}
                onChange={(e) => handleLibraryCodebookChange(e.target.value)}
                disabled={isLoading || !libraryEditions?.length}
                aria-label="Edition for LLM context"
              >
                <optgroup label={libraryDropdownLabel}>
                  {(libraryEditions ?? []).map((edition) => (
                    <option key={edition.codebook} value={edition.codebook} className="text-slate-900">
                      {edition.label}
                    </option>
                  ))}
                </optgroup>
              </select>
            ) : (
              <>
                <select
                  className="min-w-[220px] rounded-2xl border border-white/15 bg-white/10 px-3 py-2 text-sm text-white backdrop-blur focus:border-[#c9a45c] focus:outline-none focus:ring-4 focus:ring-[#f1ddab]/25"
                  value={codebook}
                  onChange={(e) => setCodebook(e.target.value)}
                  disabled={isLoading}
                  aria-label="Codebook for LLM context"
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
                    className="min-w-[200px] rounded-2xl border border-white/15 bg-white/10 px-3 py-2 text-sm text-white placeholder:text-slate-400 focus:border-[#c9a45c] focus:outline-none focus:ring-4 focus:ring-[#f1ddab]/25"
                    disabled={isLoading}
                  />
                )}
              </>
            )}
          </div>
        </div>
      </div>

      <div className="flex flex-col">
        <div className="space-y-6 p-6">
          {messages.length === 0 ? (
            <div className="py-14 text-center text-slate-500">
              <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-3xl bg-[#fff7df] shadow-inner shadow-[#f1ddab]/50">
                <Brain className="h-8 w-8 text-[#9a7a35]" />
              </div>
              <h3 className="mb-3 text-xl font-semibold text-slate-950">
                {selectedDocumentId ? 'Ready when you are' : 'Get started in three steps'}
              </h3>
              <p className="mx-auto max-w-lg text-slate-600">
                {selectedDocumentId
                  ? 'Ask your engineering question in plain English below. Augusta Search will answer from the standard you have selected, with cited clauses.'
                  : isLibraryMode
                    ? 'Choose an edition from the sidebar, confirm the code label in the dropdown, then ask your engineering question in plain English.'
                    : 'Select a standard from the sidebar, pick the matching code label from the dropdown, then ask your engineering question in plain English.'}
              </p>
              <ul className="mx-auto mt-6 max-w-md space-y-2 text-left text-sm text-slate-700">
                <li className="flex items-start gap-2">
                  <span className="mt-0.5 font-semibold text-emerald-700" aria-hidden>
                    ✓
                  </span>
                  <span>Clause-based answers</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="mt-0.5 font-semibold text-emerald-700" aria-hidden>
                    ✓
                  </span>
                  <span>Grounded in your selected standards</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="mt-0.5 font-semibold text-emerald-700" aria-hidden>
                    ✓
                  </span>
                  <span>No unsupported AI responses</span>
                </li>
              </ul>
            </div>
          ) : (
            messages.map((message) => (
              <div
                key={message.id}
                className={`flex w-full ${message.type === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`rounded-2xl px-6 py-4 shadow-sm ${
                    message.type === 'user'
                      ? 'max-w-lg bg-gradient-to-r from-[#0b1220] to-[#263245] text-white shadow-[0_14px_34px_rgba(15,23,42,0.22)]'
                      : `w-full border text-slate-900 ${
                          message.error ? 'border-red-200 bg-red-50' : 'border-slate-200/80 bg-white'
                        }`
                  }`}
                >
                  <div className="text-sm leading-relaxed">
                    {message.type === 'assistant' && !message.error ? (
                      <div
                        className="prose prose-sm max-w-none text-slate-800"
                        dangerouslySetInnerHTML={{
                          __html: renderPractitionerMarkdown(message.content),
                        }}
                      />
                    ) : (
                      message.content
                    )}
                  </div>

                  {message.type === 'assistant' && message.retryAttempted && !message.error && (
                    <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700" aria-hidden />
                      <span>
                        {message.retryUsed
                          ? 'Expanded search (attempt 2) found a better answer.'
                          : 'Expanded search (attempt 2) did not improve the answer.'}
                      </span>
                    </div>
                  )}

                  {message.type === 'assistant' && message.rephrasedQuery && !message.error && (
                    <div className="mt-3 flex items-start gap-2 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden />
                      <span>
                        <span className="font-medium text-slate-700">Search query:</span> {message.rephrasedQuery}
                      </span>
                    </div>
                  )}

                  {message.type === 'assistant' && message.conclusion && !message.error && (
                    <div className="mt-3 rounded-lg border border-emerald-100 bg-emerald-50/70 px-3 py-2 text-xs font-medium text-emerald-900">
                      Outcome: {message.conclusion}
                    </div>
                  )}

                  <div className="mt-3 flex items-center justify-between text-xs opacity-75">
                    <div className="flex items-center space-x-2">
                      <Clock className="h-3 w-3" />
                      <span>{formatTimestamp(message.timestamp)}</span>
                    </div>
                    {message.type === 'assistant' && message.confidence && !message.error && (
                      <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold capitalize text-emerald-800">
                        {message.confidence} confidence
                      </span>
                    )}
                  </div>

                  {message.sources && message.sources.length > 0 && (
                    <div className="mt-4 border-t border-slate-200 pt-4">
                      <div className="mb-3 flex items-center justify-between text-xs font-semibold text-slate-700">
                        <div className="flex items-center space-x-2">
                          <BookOpen className="h-3 w-3" />
                          <span>Sources ({message.sources.length})</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => toggleSources(message.id)}
                          className="text-[11px] font-medium text-slate-500 transition-colors hover:text-slate-700"
                        >
                          {expandedSources[message.id] ? 'Hide' : 'Show'}
                        </button>
                      </div>
                      {expandedSources[message.id] && (
                        <div className="space-y-3">
                          {message.sources.map((source, index) => (
                            <div
                              key={source.id || index}
                              className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs"
                            >
                              <div className="mb-2 font-semibold text-slate-700">
                                {source.clause_number ? `Clause ${source.clause_number}` : 'Clause'}
                                {source.heading ? ` — ${source.heading}` : ''}
                                {source.page_number != null ? ` (p.${source.page_number})` : ''}
                              </div>
                              <div className="leading-relaxed text-slate-600">{source.text}</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {message.type === 'assistant' && !message.error && (
                    <div className="mt-4 border-t border-slate-200 pt-4">
                      <QaExportActions
                        message={{
                          id: message.id,
                          type: message.type,
                          content: message.content,
                          timestamp: message.timestamp,
                          confidence: confidenceToExportValue(message.confidence),
                          retrievalNote: message.rephrasedQuery,
                          sources: message.sources?.map((s) => ({
                            clause_number: s.clause_number,
                            text: s.text,
                            page_number: s.page_number,
                          })),
                        }}
                        messages={messages.map((m) => ({
                          id: m.id,
                          type: m.type,
                          content: m.content,
                          timestamp: m.timestamp,
                        }))}
                        profile={exportProfile}
                        documentSubtitle={documentSubtitle}
                      />
                    </div>
                  )}
                </div>
              </div>
            ))
          )}

          {isLoading && (
            <div className="flex justify-start">
              <div className="max-w-lg rounded-2xl border border-slate-200 bg-white px-6 py-4 text-slate-900 shadow-sm">
                <div className="flex items-center space-x-3">
                  <div className="flex space-x-1">
                    <div className="h-2 w-2 animate-bounce rounded-full bg-gray-400" />
                    <div
                      className="h-2 w-2 animate-bounce rounded-full bg-gray-400"
                      style={{ animationDelay: '0.1s' }}
                    />
                    <div
                      className="h-2 w-2 animate-bounce rounded-full bg-gray-400"
                      style={{ animationDelay: '0.2s' }}
                    />
                  </div>
                  <span className="text-sm font-medium">{loadingStatus || 'AI is thinking...'}</span>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        <div className="sticky bottom-0 border-t border-slate-200/80 bg-gradient-to-r from-[#fffaf1] to-slate-50 p-6">
          {discipline && (
            <div className="mb-3 flex items-start gap-2.5 rounded-2xl border border-[#f1ddab]/70 bg-white/70 p-3 text-sm">
              <Info className="mt-0.5 h-4 w-4 flex-shrink-0 text-[#9a7a35]" />
              <p className="leading-relaxed text-slate-700">
                <span className="font-medium text-slate-800">Document search</span> matches your question to passages
                in the document you have open. Reuse phrases, defined terms, and clause or section numbers as they
                appear in the code for the most accurate results.
              </p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="flex space-x-4">
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Ask a question about your document..."
              className="augusta-input flex-1"
              disabled={isLoading || !selectedDocumentId}
            />
            <button
              type="submit"
              disabled={!canSubmit}
              className="rounded-2xl bg-[#0b1220] px-6 py-3 font-semibold text-white shadow-lg shadow-slate-900/20 transition hover:-translate-y-0.5 hover:bg-[#182033] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send className="h-5 w-5" />
            </button>
          </form>

          <div className="mt-4 border-t border-slate-200 pt-3">
            <p className="text-center text-xs text-gray-500">
              AI-generated guidance only. Users must hold a valid licence to the applicable codes and standards and
              verify all outputs against the current official publications.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default QaChatPanel;
