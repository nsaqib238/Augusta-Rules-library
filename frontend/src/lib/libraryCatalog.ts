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
