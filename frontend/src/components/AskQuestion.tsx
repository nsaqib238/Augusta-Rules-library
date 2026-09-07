import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { supabase } from '../lib/supabase';
import { Discipline, disciplineMeta, labelForCodebookId, SharedLibraryFamily } from '../lib/codebooks';
import { typography } from '../styles/typography';
import { isDocumentProcessing, isDocumentReadyForSearch } from '../lib/uploadDisplay';
import { fetchVisibleDocuments } from '../lib/companyDocuments';
import QaChatPanel from './qa/QaChatPanel';

interface UserDocument {
  id: string;
  filename: string;
  original_filename?: string;
  codebook: string;
  source?: string;
  status: string;
  created_at: string;
  is_shared_library?: boolean;
}

interface LibraryEdition {
  codebook: string;
  label: string;
}

interface AskQuestionProps {
  discipline: Discipline;
  libraryFamily?: SharedLibraryFamily;
}

const LIBRARY_META: Record<SharedLibraryFamily, { name: string; icon: string; blurb: string }> = {
  sir: {
    name: 'SIR',
    icon: '📋',
    blurb: 'Ask evidence-led questions across shared Service Installation Rules editions.',
  },
  ncc: {
    name: 'NCC',
    icon: '🏛️',
    blurb: 'Ask evidence-led questions across shared National Construction Code volumes.',
  },
};

const AskQuestion: React.FC<AskQuestionProps> = ({ discipline, libraryFamily }) => {
  const { user, loading: authLoading } = useAuth();
  const info = libraryFamily ? LIBRARY_META[libraryFamily] : disciplineMeta(discipline);
  const isLibraryOnly = Boolean(libraryFamily);
  const [libraryEditions, setLibraryEditions] = useState<LibraryEdition[]>([]);
  const libraryCodebooks = useMemo(() => libraryEditions.map((e) => e.codebook), [libraryEditions]);
  const [documents, setDocuments] = useState<UserDocument[]>([]);
  const [sharedDocuments, setSharedDocuments] = useState<UserDocument[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState('');
  const [selectedDocumentName, setSelectedDocumentName] = useState('');
  const [selectedCodebook, setSelectedCodebook] = useState('');
  const [selectedCodebookSource, setSelectedCodebookSource] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!libraryFamily) {
      setLibraryEditions([]);
      return;
    }
    const family = libraryFamily === 'sir' ? 'SIR' : 'NCC';
    void supabase
      .from('shared_library_editions')
      .select('codebook, label')
      .eq('family', family)
      .order('label', { ascending: true })
      .then(({ data, error }) => {
        if (error) {
          setLibraryEditions([]);
          return;
        }
        setLibraryEditions(
          (data ?? []).map((row) => ({
            codebook: row.codebook,
            label: row.label || row.codebook,
          }))
        );
      });
  }, [libraryFamily]);

  const loadUserDocuments = useCallback(async () => {
    if (!user?.id && !isLibraryOnly) {
      setDocuments([]);
      setSharedDocuments([]);
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      setLoadError(null);

      if (isLibraryOnly) {
        if (libraryCodebooks.length === 0) {
          setDocuments([]);
          setSharedDocuments([]);
          setLoading(false);
          return;
        }
        const sharedRes = await supabase
          .from('documents')
          .select('id, filename, original_filename, codebook, source, status, created_at, discipline')
          .eq('status', 'ready_for_search')
          .in('codebook', libraryCodebooks)
          .order('original_filename', { ascending: true });

        if (sharedRes.error) {
          setLoadError(sharedRes.error.message);
        } else {
          setDocuments([]);
          setSharedDocuments(
            (sharedRes.data || []).map((doc) => ({ ...doc, is_shared_library: true }))
          );
        }
      } else {
        const userDocs = await fetchVisibleDocuments<UserDocument>(user!.id, {
          select: 'id, filename, original_filename, codebook, source, status, created_at',
          discipline,
        });
        setDocuments(userDocs);
        setSharedDocuments([]);
      }
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load documents');
    } finally {
      setLoading(false);
    }
  }, [discipline, isLibraryOnly, libraryCodebooks, user?.id]);

  useEffect(() => {
    setSelectedDocumentId('');
    setSelectedDocumentName('');
    setSelectedCodebook('');
    setSelectedCodebookSource('');
    void loadUserDocuments();
  }, [discipline, libraryFamily, loadUserDocuments]);

  useEffect(() => {
    if (!libraryFamily || loading || selectedDocumentId || sharedDocuments.length === 0) return;
    const first = sharedDocuments.find((doc) => isDocumentReadyForSearch(doc.status));
    if (!first) return;
    setSelectedDocumentId(first.id);
    setSelectedDocumentName(first.original_filename || first.filename || '');
    setSelectedCodebook(first.codebook || '');
    setSelectedCodebookSource(first.source || first.original_filename || '');
  }, [libraryFamily, loading, selectedDocumentId, sharedDocuments]);

  const hasProcessingDocs = documents.some((doc) => isDocumentProcessing(doc.status));

  useEffect(() => {
    if (!user?.id || !hasProcessingDocs) return;
    const interval = setInterval(() => {
      void loadUserDocuments();
    }, 30000);
    return () => clearInterval(interval);
  }, [user?.id, hasProcessingDocs, loadUserDocuments]);

  const sidebarDocuments = isLibraryOnly ? sharedDocuments : documents;

  const handleDocumentChange = (documentId: string) => {
    const document = sidebarDocuments.find((doc) => doc.id === documentId);
    setSelectedDocumentId(documentId);
    setSelectedDocumentName(document?.original_filename || document?.filename || '');
    setSelectedCodebook(document?.codebook || '');
    setSelectedCodebookSource(document?.source || document?.original_filename || '');
  };

  const handleLibraryEditionChange = (codebook: string) => {
    const document = sharedDocuments.find((doc) => doc.codebook === codebook);
    if (document) {
      handleDocumentChange(document.id);
      return;
    }
    const edition = libraryEditions.find((e) => e.codebook === codebook);
    setSelectedDocumentId('');
    setSelectedDocumentName('');
    setSelectedCodebook(codebook);
    setSelectedCodebookSource(edition?.label || codebook);
  };

  const renderDocumentCard = (document: UserDocument) => {
    const selectable = isDocumentReadyForSearch(document.status);
    const displayName = document.original_filename || document.filename;
    return (
      <div
        key={document.id}
        className={`rounded-2xl border p-4 transition-all ${
          selectable ? 'cursor-pointer' : 'cursor-not-allowed opacity-80'
        } ${
          selectedDocumentId === document.id
            ? 'border-[#c9a45c] bg-[#fff7df] shadow-[0_12px_30px_rgba(201,164,92,0.18)]'
            : 'border-slate-200/80 bg-white/80 hover:-translate-y-0.5 hover:border-[#d6bf82] hover:bg-[#fffaf1]'
        }`}
        onClick={() => {
          if (selectable) handleDocumentChange(document.id);
        }}
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <h3 className="truncate text-sm font-medium text-gray-900">{displayName}</h3>
          <div className="flex items-center gap-1">
            {document.is_shared_library && (
              <span className="rounded-full bg-[#fff7df] px-2 py-0.5 text-[10px] font-semibold text-[#7c5f1e]">
                Shared
              </span>
            )}
            <span className={`text-xs ${getStatusColor(document.status)}`}>
              {getStatusIcon(document.status)}
            </span>
          </div>
        </div>

        <div className="space-y-1 text-xs text-gray-500">
          <div>Code: {document.source || labelForCodebookId(document.codebook)}</div>
          {!document.is_shared_library && <div>Uploaded: {formatDate(document.created_at)}</div>}
          {document.status === 'failed' && (
            <div className="font-medium text-red-600">
              Processing failed — remove and re-upload from Upload.
            </div>
          )}
        </div>
      </div>
    );
  };

  const formatDate = (dateString: string) => new Date(dateString).toLocaleDateString();

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'ready_for_search':
        return 'text-green-600';
      case 'pdf_processing':
      case 'admin_processing':
      case 'pending_admin_review':
        return 'text-yellow-600';
      case 'failed':
        return 'text-red-600';
      default:
        return 'text-gray-600';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'ready_for_search':
        return '✓';
      case 'pdf_processing':
      case 'admin_processing':
      case 'pending_admin_review':
        return '⏳';
      case 'failed':
        return '✗';
      default:
        return '?';
    }
  };

  if (authLoading) {
    return (
      <div className="flex min-h-[420px] items-center justify-center">
        <div className="h-12 w-12 animate-spin rounded-full border-2 border-slate-200 border-t-[#c9a45c]" />
      </div>
    );
  }

  return (
    <div className="rounded-[26px] bg-gradient-to-br from-white/70 to-[#fff7e3]/35">
      <div className="mx-auto max-w-7xl px-2 py-2 sm:px-4 sm:py-4">
        <div className="mb-6 rounded-[26px] border border-white/70 bg-white/75 p-6 shadow-sm backdrop-blur">
          <p className="augusta-eyebrow mb-3">{info.name} intelligence</p>
          <h1 className={`${typography.sectionTitle} mb-2 flex items-center gap-2 tracking-tight text-slate-950`}>
            <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#0b1220] text-lg shadow-lg shadow-slate-900/20">
              {info.icon}
            </span>
            {isLibraryOnly ? `${info.name} Q&A` : 'Q&A Assistant'}
          </h1>
          <p className={`${typography.helper} max-w-2xl`}>
            {isLibraryOnly
              ? LIBRARY_META[libraryFamily!].blurb
              : `Ask evidence-led questions about your uploaded ${info.name.toLowerCase()} documents and get grounded answers with source references.`}
          </p>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-4">
          <div className="lg:col-span-1">
            <div className="rounded-[26px] border border-white/70 bg-white/80 p-5 shadow-[0_18px_55px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <h2 className={`${typography.sectionTitle} mb-1 text-slate-950`}>
                {isLibraryOnly ? 'Select edition' : 'Your Documents'}
              </h2>
              <p className="mb-5 text-xs leading-5 text-slate-500">
                {isLibraryOnly
                  ? `Choose a ${info.name} volume or edition before asking Augusta Search.`
                  : 'Select a source library before asking Augusta Search.'}
              </p>

              {loading ? (
                <div className="flex items-center justify-center py-8">
                  <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
                </div>
              ) : loadError ? (
                <div className="py-8 text-center text-sm text-red-600">{loadError}</div>
              ) : sidebarDocuments.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 py-8 text-center text-gray-500">
                  <div className="mb-4">
                    <svg
                      className="mx-auto h-12 w-12 text-[#c9a45c]"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                      />
                    </svg>
                  </div>
                  <p className="text-sm">
                    {isLibraryOnly
                      ? `No ${info.name} editions loaded yet`
                      : `No ${info.name.toLowerCase()} documents uploaded yet`}
                  </p>
                  <p className="mt-1 text-xs text-gray-400">
                    {isLibraryOnly
                      ? 'An admin must upload the shared library before Q&A is available.'
                      : 'Upload a PDF from the Upload tab to get started'}
                  </p>
                </div>
              ) : (
                <div className="space-y-3">{sidebarDocuments.map(renderDocumentCard)}</div>
              )}
            </div>
          </div>

          <div className="lg:col-span-3">
            <QaChatPanel
              key={`${discipline}-${libraryFamily || 'std'}-${selectedDocumentId || 'none'}`}
              discipline={discipline}
              userUploadsOnly={!isLibraryOnly}
              selectedDocumentId={selectedDocumentId}
              selectedDocumentName={selectedDocumentName}
              selectedCodebook={selectedCodebook}
              selectedCodebookSource={selectedCodebookSource}
              exportProfile={libraryFamily ?? 'qna'}
              libraryFamily={libraryFamily}
              libraryEditions={isLibraryOnly ? libraryEditions : undefined}
              onLibraryEditionChange={isLibraryOnly ? handleLibraryEditionChange : undefined}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default AskQuestion;
