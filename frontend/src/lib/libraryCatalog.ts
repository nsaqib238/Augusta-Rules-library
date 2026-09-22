import { Discipline } from './codebooks';

export interface LibraryCountry {
  id: string;
  code: string;
  name: string;
  is_active?: boolean;
  sort_order?: number;
}

export interface LibraryDocumentType {
  id: string;
  country_id: string;
  slug: string;
  name: string;
  sort_order?: number;
}

export interface LibraryCatalogDocument {
  id: string;
  country_id: string;
  document_type_id: string;
  slug: string;
  title: string;
  discipline: string;
  publisher?: string | null;
  priority: 'critical' | 'high' | 'medium' | string;
  sort_order?: number;
  is_active?: boolean;
}

export interface LibraryEditionStatus {
  codebook: string;
  label: string;
  family?: string;
  library_document_id?: string | null;
  document_id?: string | null;
  status?: string | null;
  chunk_count?: number;
  table_count?: number;
  ready?: boolean;
  processing?: boolean;
}

export interface LibraryTreeData {
  countries: LibraryCountry[];
  types: LibraryDocumentType[];
  documents: LibraryCatalogDocument[];
  editions: LibraryEditionStatus[];
}

export function mapCatalogDiscipline(value?: string | null): Discipline {
  const key = (value || '').split('/')[0].trim().toLowerCase();
  if (key === 'fire') return 'fire';
  if (key === 'hydraulic' || key === 'hydraulics' || key === 'plumbing') return 'hydraulics';
  if (key === 'mechanical') return 'mechanical';
  return 'electrical';
}

export function editionsForDocument(
  editions: LibraryEditionStatus[],
  documentId: string
): LibraryEditionStatus[] {
  return editions.filter((e) => e.library_document_id === documentId);
}

export function documentIsReady(editions: LibraryEditionStatus[], documentId: string): boolean {
  return editionsForDocument(editions, documentId).some((e) => e.ready);
}

export function documentIsProcessing(editions: LibraryEditionStatus[], documentId: string): boolean {
  return editionsForDocument(editions, documentId).some(
    (e) => e.processing || e.status === 'pdf_processing' || e.status === 'admin_processing'
  );
}

const LEFTOVER_SIR_SLUGS = new Set(['nsw-sir', 'sa-sir', 'tasnetworks-sir', 'vic-sir']);

export function isNccCatalogDocument(
  doc: LibraryCatalogDocument,
  editions: LibraryEditionStatus[]
): boolean {
  const slug = (doc.slug || '').toLowerCase();
  if (LEFTOVER_SIR_SLUGS.has(slug)) return false;
  if (editions.some((e) => e.library_document_id === doc.id && (e.family || '').toUpperCase() === 'NCC')) {
    return true;
  }
  const title = (doc.title || '').toLowerCase();
  return slug.includes('ncc') || title.includes('ncc') || title.includes('national construction code');
}

export function nccCatalogDocuments(
  documents: LibraryCatalogDocument[],
  editions: LibraryEditionStatus[]
): LibraryCatalogDocument[] {
  return documents.filter((doc) => isNccCatalogDocument(doc, editions));
}
