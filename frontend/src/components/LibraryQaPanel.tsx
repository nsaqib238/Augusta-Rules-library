import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { supabase } from '../lib/supabase';
import { typography } from '../styles/typography';
import QaChatPanel from './qa/QaChatPanel';
import LibraryTree from './library/LibraryTree';
import {
  LibraryCatalogDocument,
  LibraryCountry,
  LibraryDocumentType,
  LibraryEditionStatus,
  editionsForDocument,
  mapCatalogDiscipline,
} from '../lib/libraryCatalog';

const LibraryQaPanel: React.FC = () => {
  const [countries, setCountries] = useState<LibraryCountry[]>([]);
  const [types, setTypes] = useState<LibraryDocumentType[]>([]);
  const [documents, setDocuments] = useState<LibraryCatalogDocument[]>([]);
  const [editions, setEditions] = useState<LibraryEditionStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [countryId, setCountryId] = useState('');
  const [typeId, setTypeId] = useState<string | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [selectedCodebook, setSelectedCodebook] = useState('');
  const [selectedDocumentId, setSelectedDocumentId] = useState('');

  const loadTree = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [countryRes, typeRes, docRes, editionRes, readyRes] = await Promise.all([
        supabase.from('library_countries').select('*').eq('is_active', true).order('sort_order'),
        supabase.from('library_document_types').select('*').order('sort_order'),
        supabase.from('library_documents').select('*').eq('is_active', true).order('sort_order'),
        supabase.from('shared_library_editions').select('codebook, label, family, library_document_id, discipline'),
        supabase
          .from('documents')
          .select('id, codebook, status, source, original_filename, filename')
          .eq('is_shared_library', true)
          .eq('status', 'ready_for_search'),
      ]);

      if (countryRes.error) {
        setError(countryRes.error.message);
        setCountries([]);
        return;
      }

      const readyByCodebook = new Map(
        (readyRes.data || []).map((row) => [String(row.codebook).toUpperCase(), row])
      );
      const editionRows: LibraryEditionStatus[] = (editionRes.data || []).map((row) => {
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
      });

      const countryRows = (countryRes.data || []) as LibraryCountry[];
      setCountries(countryRows);
      setTypes((typeRes.data || []) as LibraryDocumentType[]);
      setDocuments((docRes.data || []) as LibraryCatalogDocument[]);
      setEditions(editionRows);
      setCountryId((prev) => prev || countryRows[0]?.id || '');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load library');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTree();
  }, [loadTree]);

  const country = countries.find((c) => c.id === countryId);
  const selectedType = types.find((t) => t.id === typeId);
  const selectedDoc = documents.find((d) => d.id === documentId);

  const docEditions = useMemo(
    () => (documentId ? editionsForDocument(editions, documentId) : []),
    [editions, documentId]
  );
  const readyEditions = useMemo(() => docEditions.filter((e) => e.ready), [docEditions]);

  useEffect(() => {
    if (!documentId) {
      setSelectedCodebook('');
      setSelectedDocumentId('');
      return;
    }
    const first = readyEditions[0];
    setSelectedCodebook(first?.codebook || '');
    setSelectedDocumentId(first?.document_id || '');
  }, [documentId, readyEditions]);

  const onSelectDocument = (id: string) => {
    setDocumentId(id);
    const doc = documents.find((d) => d.id === id);
    if (doc) setTypeId(doc.document_type_id);
  };

  const handleEditionChange = (codebook: string) => {
    const edition = docEditions.find((e) => e.codebook === codebook);
    setSelectedCodebook(codebook);
    setSelectedDocumentId(edition?.document_id || '');
  };

  const crumb = [country?.name, selectedType?.name, selectedDoc?.title].filter(Boolean).join(' / ');
  const discipline = mapCatalogDiscipline(selectedDoc?.discipline);

  if (loading) {
    return (
      <div className="flex min-h-[420px] items-center justify-center">
        <div className="h-12 w-12 animate-spin rounded-full border-2 border-slate-200 border-t-[#c9a45c]" />
      </div>
    );
  }

  if (error || countries.length === 0) {
    return (
      <div className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900">
        <p className="font-semibold">Library is not ready yet.</p>
        <p className="mt-2">
          An admin must run <code className="rounded bg-white px-1">supabase/combined_setup.sql</code> (section 10b)
          so the country / type catalog exists.
        </p>
        {error && <p className="mt-2">{error}</p>}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-[26px] border border-white/70 bg-white/75 p-6 shadow-sm backdrop-blur">
        <p className="augusta-eyebrow mb-3">Library Q&amp;A</p>
        <h1 className={`${typography.sectionTitle} tracking-tight text-slate-950`}>Ask a question</h1>
        <p className={`${typography.helper} mt-2 max-w-2xl`}>
          Choose a country, open a document type, then pick one document. Answers are grounded in that document only.
        </p>
        {crumb && <p className="mt-3 text-sm font-medium text-slate-700">{crumb}</p>}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="lg:col-span-4">
          <LibraryTree
            countries={countries}
            types={types}
            documents={documents}
            editions={editions}
            countryId={countryId}
            typeId={typeId}
            documentId={documentId}
            onCountryChange={(id) => {
              setCountryId(id);
              setTypeId(null);
              setDocumentId(null);
            }}
            onSelectType={setTypeId}
            onSelectDocument={onSelectDocument}
            showUnready
            searchEnabled
          />
        </div>
        <div className="lg:col-span-8">
          {selectedDoc && readyEditions.length > 0 ? (
            <QaChatPanel
              key={`${selectedDoc.id}-${selectedCodebook}`}
              discipline={discipline}
              selectedDocumentId={selectedDocumentId}
              selectedDocumentName={selectedDoc.title}
              selectedCodebook={selectedCodebook}
              selectedCodebookSource={selectedDoc.title}
              exportProfile="qna"
              libraryFamily={readyEditions[0]?.family === 'NCC' ? 'ncc' : 'sir'}
              libraryEditions={readyEditions.map((e) => ({ codebook: e.codebook, label: e.label }))}
              onLibraryEditionChange={handleEditionChange}
            />
          ) : selectedDoc ? (
            <div className="rounded-[26px] border border-dashed border-slate-200 bg-slate-50/80 p-8 text-sm text-slate-600">
              <p className="font-semibold text-slate-900">{selectedDoc.title}</p>
              <p className="mt-2">Not ingested yet. An admin needs to upload the clause CSV before you can ask questions.</p>
            </div>
          ) : (
            <div className="rounded-[26px] border border-dashed border-slate-200 bg-slate-50/80 p-8 text-sm text-slate-600">
              Open a document type, then pick a document with a green status dot.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default LibraryQaPanel;
