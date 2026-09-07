import React, { useMemo, useState } from 'react';
import {
  LibraryCatalogDocument,
  LibraryCountry,
  LibraryDocumentType,
  LibraryEditionStatus,
  documentIsProcessing,
  documentIsReady,
} from '../../lib/libraryCatalog';

interface LibraryTreeProps {
  countries: LibraryCountry[];
  types: LibraryDocumentType[];
  documents: LibraryCatalogDocument[];
  editions: LibraryEditionStatus[];
  countryId: string;
  typeId: string | null;
  documentId: string | null;
  selectedDocumentIds?: string[];
  multiSelect?: boolean;
  onCountryChange: (countryId: string) => void;
  onSelectType: (typeId: string) => void;
  onSelectDocument: (documentId: string) => void;
  showUnready?: boolean;
  searchEnabled?: boolean;
}

const LibraryTree: React.FC<LibraryTreeProps> = ({
  countries,
  types,
  documents,
  editions,
  countryId,
  typeId,
  documentId,
  selectedDocumentIds,
  multiSelect = false,
  onCountryChange,
  onSelectType,
  onSelectDocument,
  showUnready = true,
  searchEnabled = true,
}) => {
  const [query, setQuery] = useState('');
  const [openTypes, setOpenTypes] = useState<Record<string, boolean>>({});

  const countryTypes = useMemo(
    () => types.filter((t) => t.country_id === countryId).sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0)),
    [types, countryId]
  );

  const filteredDocs = useMemo(() => {
    const q = query.trim().toLowerCase();
    return documents.filter((d) => {
      if (d.country_id !== countryId) return false;
      if (d.is_active === false) return false;
      if (!showUnready && !documentIsReady(editions, d.id)) return false;
      if (!q) return true;
      return (
        d.title.toLowerCase().includes(q) ||
        (d.publisher || '').toLowerCase().includes(q) ||
        (d.discipline || '').toLowerCase().includes(q)
      );
    });
  }, [documents, countryId, editions, query, showUnready]);

  const toggleType = (id: string) => {
    setOpenTypes((prev) => ({ ...prev, [id]: !(prev[id] ?? id === typeId) }));
    onSelectType(id);
  };

  const isTypeOpen = (id: string) => openTypes[id] ?? id === typeId;

  return (
    <div className="flex h-full min-h-[420px] flex-col rounded-[26px] border border-white/70 bg-white/80 p-4 shadow-[0_18px_55px_rgba(15,23,42,0.08)] backdrop-blur-xl">
      <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        Country
      </label>
      <select
        value={countryId}
        onChange={(e) => onCountryChange(e.target.value)}
        className="augusta-input mb-3 w-full"
      >
        {countries.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>

      {searchEnabled && (
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search documents…"
          className="augusta-input mb-4 w-full"
        />
      )}

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
        {countryTypes.length === 0 ? (
          <p className="px-2 py-6 text-center text-sm text-slate-500">No document types yet.</p>
        ) : (
          countryTypes.map((type) => {
            const docs = filteredDocs
              .filter((d) => d.document_type_id === type.id)
              .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.title.localeCompare(b.title));
            const open = isTypeOpen(type.id);
            return (
              <div key={type.id}>
                <button
                  type="button"
                  onClick={() => toggleType(type.id)}
                  className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-sm font-semibold transition ${
                    typeId === type.id ? 'bg-[#0b1220] text-white' : 'text-slate-800 hover:bg-slate-100'
                  }`}
                >
                  <span>{type.name}</span>
                  <span className="text-xs opacity-70">{docs.length}</span>
                </button>
                {open && (
                  <div className="mt-1 space-y-1 pl-2">
                    {docs.length === 0 ? (
                      <p className="px-2 py-2 text-xs text-slate-400">No documents in this type.</p>
                    ) : (
                      docs.map((doc) => {
                        const ready = documentIsReady(editions, doc.id);
                        const processing = documentIsProcessing(editions, doc.id);
                        const selected = multiSelect
                          ? Boolean(selectedDocumentIds?.includes(doc.id))
                          : documentId === doc.id;
                        return (
                          <button
                            key={doc.id}
                            type="button"
                            disabled={!showUnready && !ready}
                            onClick={() => onSelectDocument(doc.id)}
                            className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition ${
                              selected
                                ? 'bg-[#fff7df] font-medium text-slate-950 ring-1 ring-[#c9a45c]'
                                : ready
                                  ? 'text-slate-700 hover:bg-slate-50'
                                  : 'text-slate-400 hover:bg-slate-50'
                            }`}
                          >
                            <span className="flex min-w-0 items-center gap-2">
                              {multiSelect && (
                                <span
                                  className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] ${
                                    selected ? 'border-[#c9a45c] bg-[#c9a45c] text-white' : 'border-slate-300 bg-white'
                                  }`}
                                >
                                  {selected ? '✓' : ''}
                                </span>
                              )}
                              <span className="truncate">{doc.title}</span>
                            </span>
                            <span
                              className={`ml-2 h-2 w-2 shrink-0 rounded-full ${
                                processing ? 'bg-amber-400' : ready ? 'bg-emerald-500' : 'bg-slate-300'
                              }`}
                              title={processing ? 'Extracting PDF' : ready ? 'Ready' : 'Not ingested yet'}
                            />
                          </button>
                        );
                      })
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default LibraryTree;
