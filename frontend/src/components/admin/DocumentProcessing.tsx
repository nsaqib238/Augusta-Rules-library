import React, { useState, useEffect } from 'react';
import { supabase } from '../../lib/supabase';
import { withApiBase } from '../../lib/api';

interface PendingDocument {
  id: string;
  user_id: string;
  document_id: string;
  filename: string;
  codebook: string;
  status: string;
  created_at: string;
  storage_url: string;
  file_size: number;
  priority?: number;
  admin_notes?: string;
  assigned_admin_id?: string;
}

interface ChunkData {
  text: string;
  clause_number?: string;
  page_number?: number;
  entities?: string[];
  detailed_analysis?: any;
  metadata?: any;
}

interface Codebook {
  id: string;
  name: string;
  description: string;
}

interface CSVFile {
  name: string;
  path: string;
  description: string;
  columns: Record<string, string>;
}

interface DocumentProcessingProps {
  selectedDocument?: PendingDocument | null;
  onDocumentProcessed?: () => void;
}

const DocumentProcessing: React.FC<DocumentProcessingProps> = ({
  selectedDocument,
  onDocumentProcessed
}) => {
  const [pendingDocs, setPendingDocs] = useState<PendingDocument[]>([]);
  const [selectedDoc, setSelectedDoc] = useState<PendingDocument | null>(selectedDocument || null);
  const [chunks, setChunks] = useState<ChunkData[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [adminNotes, setAdminNotes] = useState('');
  const [selectedChunkFile, setSelectedChunkFile] = useState<File | null>(null);
  const [selectedTableFile, setSelectedTableFile] = useState<File | null>(null);
  const [availableCodebooks, setAvailableCodebooks] = useState<Codebook[]>([]);
  const [selectedCodebook, setSelectedCodebook] = useState<string>('');
  const [availableCSVFiles, setAvailableCSVFiles] = useState<CSVFile[]>([]);
  const [selectedCSVFile, setSelectedCSVFile] = useState<string>('');
  const [loadingCodebooks, setLoadingCodebooks] = useState(false);
  const [codebookError, setCodebookError] = useState<string | null>(null);
  const [processingMode, setProcessingMode] = useState<'file' | 'tables'>('file');

  useEffect(() => {
    fetchPendingDocuments();
  }, []);

  useEffect(() => {
    if (selectedDocument) {
      setSelectedDoc(selectedDocument);
    }
  }, [selectedDocument]);

  useEffect(() => {
    if (processingMode === 'tables') {
      if (!availableCodebooks.length && !loadingCodebooks) {
        fetchAvailableCodebooks();
      } else if (selectedCodebook) {
        fetchCSVFilesForCodebook(selectedCodebook);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processingMode, selectedCodebook]);

  const fetchPendingDocuments = async () => {
    try {
      setLoading(true);
      
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        throw new Error('No active session');
      }

      const response = await fetch(withApiBase('/api/v1/admin/pending-documents'), {
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
        },
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to fetch pending documents');
      }

      const data = await response.json();
      setPendingDocs(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch pending documents');
    } finally {
      setLoading(false);
    }
  };

  const fetchAvailableCodebooks = async () => {
    try {
      setLoadingCodebooks(true);
      setCodebookError(null);
      const response = await fetch(withApiBase('/api/v1/admin/tables/codebooks'));
      if (!response.ok) {
        throw new Error('Failed to fetch codebooks');
      }
      const data = await response.json();
      const list = Array.isArray(data) ? data : (data?.codebooks || []);
      setAvailableCodebooks(list);
      
      if (list.length > 0 && !selectedCodebook) {
        setSelectedCodebook(list[0].id);
      }
    } catch (err) {
      console.error('Error fetching codebooks:', err);
      setCodebookError('Optional tables feature unavailable (codebooks endpoint unreachable).');
    } finally {
      setLoadingCodebooks(false);
    }
  };

  const fetchCSVFilesForCodebook = async (codebookId: string) => {
    if (!codebookId) return;
    try {
      const response = await fetch(withApiBase(`/api/v1/admin/tables/codebooks/${codebookId}/csv-files`));
      if (!response.ok) {
        throw new Error('Failed to fetch CSV files');
      }
      const data = await response.json();
      const csvFiles = Array.isArray(data) ? data : (data?.csv_files || data?.files || []);
      setAvailableCSVFiles(csvFiles);
      
      if (csvFiles.length > 0 && !selectedCSVFile) {
        setSelectedCSVFile(csvFiles[0].name);
      }
    } catch (err) {
      console.error('Error fetching CSV files:', err);
      setCodebookError(null);
    }
  };

  const handleDeleteDocument = async (documentId: string) => {
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        alert('Please log in');
        return;
      }

      const response = await fetch(withApiBase(`/api/v1/admin/delete-document/${documentId}`), {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
        },
      });

      if (response.ok) {
        alert('Document deleted successfully');
        // Refresh the pending documents list
        fetchPendingDocuments();
        // Clear selection if the deleted document was selected
        if (selectedDoc?.document_id === documentId) {
          setSelectedDoc(null);
        }
      } else {
        const errorData = await response.json();
        alert(`Failed to delete document: ${errorData.detail || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Error deleting document:', error);
      alert('Error deleting document');
    }
  };

  const handleSelectDocument = (doc: PendingDocument) => {
    setSelectedDoc(doc);
    setChunks([]);
    setAdminNotes('');
    setSelectedChunkFile(null);
  };

  const handleDownloadPDF = async (documentId: string) => {
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        throw new Error('No active session');
      }

      const response = await fetch(withApiBase(`/api/v1/admin/proxy-pdf/${documentId}`), {
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
        },
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to access PDF');
      }

      const contentDisposition = response.headers.get('Content-Disposition');
      const filename = contentDisposition 
        ? contentDisposition.split('filename=')[1]?.replace(/"/g, '')
        : `document_${documentId}.pdf`;

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
      
    } catch (err) {
      setError(`Failed to download PDF: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  const addChunk = () => {
    setChunks([...chunks, {
      text: '',
      clause_number: '',
      page_number: 1,
      entities: [],
      detailed_analysis: {},
      metadata: {}
    }]);
  };

  const updateChunk = (index: number, field: keyof ChunkData, value: any) => {
    const updatedChunks = [...chunks];
    updatedChunks[index] = { ...updatedChunks[index], [field]: value };
    setChunks(updatedChunks);
  };

  const removeChunk = (index: number) => {
    setChunks(chunks.filter((_, i) => i !== index));
  };

  const uploadChunks = async () => {
    if (!selectedDoc || chunks.length === 0) {
      setError('Please select a document and add at least one chunk');
      return;
    }

    try {
      setUploading(true);
      
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        throw new Error('No active session');
      }

      const response = await fetch(withApiBase('/api/v1/admin/upload-chunks'), {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          user_id: selectedDoc.user_id,
          document_id: selectedDoc.document_id,
          codebook: selectedDoc.codebook,
          chunks: chunks,
          admin_notes: adminNotes
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to upload chunks');
      }

      const result = await response.json();
      alert(`Successfully uploaded ${result.chunks_uploaded} chunks!`);
      
      await fetchPendingDocuments();
      setSelectedDoc(null);
      setChunks([]);
      setAdminNotes('');
      
      if (onDocumentProcessed) {
        onDocumentProcessed();
      }
      
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload chunks');
    } finally {
      setUploading(false);
    }
  };

  const uploadChunkFile = async () => {
    if (!selectedDoc || !selectedChunkFile) {
      setError('Please select a document and a chunk file');
      return;
    }

    try {
      setUploading(true);
      setError(null);
      
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        throw new Error('No active session');
      }

      const formData = new FormData();
      formData.append('file', selectedChunkFile);
      formData.append('user_id', selectedDoc.user_id);
      formData.append('document_id', selectedDoc.document_id);
      formData.append('admin_notes', adminNotes);

      const response = await fetch(withApiBase('/api/v1/admin/upload-processed-chunks-file'), {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
        },
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to upload chunk file');
      }

      const result = await response.json();
      alert(`Successfully uploaded ${result.chunks_count} chunks from file!`);
      
      await fetchPendingDocuments();
      setSelectedDoc(null);
      setSelectedChunkFile(null);
      setAdminNotes('');
      
      if (onDocumentProcessed) {
        onDocumentProcessed();
      }
      
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload chunk file');
    } finally {
      setUploading(false);
    }
  };

  const uploadTableFile = async () => {
    if (!selectedDoc || !selectedTableFile) {
      setError('Please select a document and a tables CSV file');
      return;
    }

    try {
      setUploading(true);
      setError(null);
      
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        throw new Error('No active session');
      }

      const formData = new FormData();
      formData.append('file', selectedTableFile);
      formData.append('user_id', selectedDoc.user_id);
      formData.append('document_id', selectedDoc.document_id);
      formData.append('admin_notes', adminNotes);

      const response = await fetch(withApiBase('/api/v1/admin/tables/upload-tables'), {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
        },
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to upload tables file');
      }

      const result = await response.json();
      alert(`✅ Successfully uploaded ${result.tables_count} tables!\n\nDocument now has both chunks and tables.`);
      
      await fetchPendingDocuments();
      setSelectedDoc(null);
      setSelectedTableFile(null);
      setAdminNotes('');
      
      if (onDocumentProcessed) {
        onDocumentProcessed();
      }
      
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload tables file');
    } finally {
      setUploading(false);
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'pending': return 'bg-yellow-100 text-yellow-800';
      case 'processing': return 'bg-blue-100 text-blue-800';
      case 'completed': return 'bg-green-100 text-green-800';
      case 'rejected': return 'bg-red-100 text-red-800';
      default: return 'bg-gray-100 text-gray-800';
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold text-gray-900">Document Processing</h1>
        <button
          onClick={fetchPendingDocuments}
          className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-md p-4">
          <p className="text-red-700">{error}</p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left Column - Document Selection */}
        <div className="bg-white shadow rounded-lg">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-medium text-gray-900">Select Document</h2>
            <p className="text-sm text-gray-600">Choose a document to process</p>
          </div>
          
          <div className="p-6">
            {pendingDocs.length === 0 ? (
              <div className="text-center py-8">
                <div className="text-gray-400 mb-4">
                  <svg className="mx-auto h-12 w-12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <h3 className="text-lg font-medium text-gray-900 mb-2">No pending documents</h3>
                <p className="text-gray-600">
                  All documents have been processed or no documents have been uploaded yet.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {pendingDocs.map((doc) => (
                  <div
                    key={doc.id}
                    className={`p-4 border rounded-lg cursor-pointer transition-colors ${
                      selectedDoc?.id === doc.id
                        ? 'border-blue-500 bg-blue-50'
                        : 'border-gray-200 hover:border-gray-300'
                    }`}
                    onClick={() => handleSelectDocument(doc)}
                  >
                    <div className="flex justify-between items-start">
                      <div className="flex-1">
                        <h3 className="font-medium text-gray-900">{doc.filename}</h3>
                        <p className="text-sm text-gray-500">User: {doc.user_id.slice(0, 8)}...</p>
                        <p className="text-sm text-gray-500">Codebook: {doc.codebook}</p>
                        <p className="text-sm text-gray-500">
                          Size: {formatFileSize(doc.file_size)} | 
                          Uploaded: {new Date(doc.created_at).toLocaleDateString()}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            if (window.confirm(`Delete "${doc.filename}"? This will permanently remove it from the queue.`)) {
                              handleDeleteDocument(doc.document_id);
                            }
                          }}
                          className="text-red-600 hover:text-red-800 p-1"
                          title="Delete document"
                        >
                          🗑️
                        </button>
                        <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getStatusColor(doc.status)}`}>
                          {doc.status.replace('_', ' ')}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right Column - Processing Interface */}
        <div className="bg-white shadow rounded-lg">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-medium text-gray-900">Process Document</h2>
            <p className="text-sm text-gray-600">
              {selectedDoc ? `Processing: ${selectedDoc.filename}` : 'Select a document to process'}
            </p>
          </div>
          
          <div className="p-6">
            {selectedDoc ? (
              <div className="space-y-6">
                {/* Document Info */}
                <div className="bg-gray-50 rounded-lg p-4">
                  <h3 className="font-medium text-gray-900 mb-2">Document Information</h3>
                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <span className="text-gray-500">Filename:</span>
                      <span className="ml-2 font-medium">{selectedDoc.filename}</span>
                    </div>
                    <div>
                      <span className="text-gray-500">Codebook:</span>
                      <span className="ml-2 font-medium">{selectedDoc.codebook}</span>
                    </div>
                    <div>
                      <span className="text-gray-500">Size:</span>
                      <span className="ml-2 font-medium">{formatFileSize(selectedDoc.file_size)}</span>
                    </div>
                    <div>
                      <span className="text-gray-500">Status:</span>
                      <span className={`ml-2 inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${getStatusColor(selectedDoc.status)}`}>
                        {selectedDoc.status}
                      </span>
                    </div>
                  </div>
                  <div className="mt-3">
                    <button
                      onClick={() => handleDownloadPDF(selectedDoc.document_id)}
                      className="inline-flex items-center px-3 py-2 border border-gray-300 shadow-sm text-sm leading-4 font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50"
                    >
                      <svg className="mr-2 h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                      </svg>
                      Download PDF
                    </button>
                  </div>
                </div>

                {/* Processing Mode Selection */}
                <div className="border rounded-lg p-4">
                  <h3 className="font-medium text-gray-900 mb-3">Processing Mode</h3>
                  <div className="flex gap-3">
                    <button
                      onClick={() => setProcessingMode('file')}
                      className={`flex-1 px-6 py-3 rounded-lg text-sm font-medium transition-colors ${
                        processingMode === 'file'
                          ? 'bg-blue-100 text-blue-800 border-2 border-blue-300 shadow-sm'
                          : 'bg-gray-100 text-gray-700 border-2 border-gray-200 hover:bg-gray-200'
                      }`}
                    >
                      📄 Upload Chunks (Clauses)
                    </button>
                    <button
                      onClick={() => setProcessingMode('tables')}
                      className={`flex-1 px-6 py-3 rounded-lg text-sm font-medium transition-colors ${
                        processingMode === 'tables'
                          ? 'bg-purple-100 text-purple-800 border-2 border-purple-300 shadow-sm'
                          : 'bg-gray-100 text-gray-700 border-2 border-gray-200 hover:bg-gray-200'
                      }`}
                    >
                      📊 Upload Tables
                    </button>
                  </div>
                </div>

                {/* File Upload Processing */}
                {processingMode === 'file' && (
                  <div>
                    <h3 className="font-medium text-gray-900 mb-3">Upload Processed Chunks File</h3>
                    <div className="space-y-3">
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2">
                          Select Processed Chunks File (CSV)
                        </label>
                        <input
                          type="file"
                          accept=".csv"
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file) {
                              setSelectedChunkFile(file);
                            }
                          }}
                          className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-green-50 file:text-green-700 hover:file:bg-green-100"
                        />
                      </div>
                      
                      {selectedChunkFile && (
                        <div className="flex items-center justify-between p-3 bg-white rounded border">
                          <div className="flex items-center">
                            <svg className="w-5 h-5 text-green-500 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                            </svg>
                            <span className="text-sm text-gray-700">{selectedChunkFile.name}</span>
                            <span className="text-xs text-gray-500 ml-2">
                              ({(selectedChunkFile.size / 1024).toFixed(1)} KB)
                            </span>
                          </div>
                          <button
                            onClick={() => setSelectedChunkFile(null)}
                            className="text-red-500 hover:text-red-700 text-sm"
                          >
                            Remove
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Upload Tables Processing */}
                {processingMode === 'tables' && (
                  <div>
                    <h3 className="font-medium text-gray-900 mb-3">Upload Tables CSV File</h3>
                    <div className="space-y-3">
                      {codebookError && (
                        <div className="p-3 rounded border border-amber-300 bg-amber-50 text-sm text-amber-700">
                          {codebookError}
                        </div>
                      )}
                      <div className="p-4 bg-purple-50 border border-purple-200 rounded-lg">
                        <h4 className="text-sm font-medium text-purple-800 mb-2">📊 Tables Upload</h4>
                        <p className="text-xs text-purple-700 mb-3">
                          Upload a CSV file containing standard tables. Tables will be linked to this document 
                          and can be queried together with chunks.
                        </p>
                        <div className="text-xs text-purple-600">
                          <strong>Required CSV columns:</strong> table_no, text, standard_name
                          <br/>
                          <strong>Optional columns:</strong> table_id, title, source_clause_number, notes, exceptions
                        </div>
                      </div>

                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2">
                          Select Tables CSV File
                        </label>
                        <input
                          type="file"
                          accept=".csv"
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file) {
                              setSelectedTableFile(file);
                            }
                          }}
                          className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-purple-50 file:text-purple-700 hover:file:bg-purple-100"
                        />
                      </div>
                      
                      {selectedTableFile && (
                        <div className="flex items-center justify-between p-3 bg-white rounded border border-purple-200">
                          <div className="flex items-center">
                            <svg className="w-5 h-5 text-purple-500 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                            </svg>
                            <span className="text-sm text-gray-700">{selectedTableFile.name}</span>
                            <span className="text-xs text-gray-500 ml-2">
                              ({(selectedTableFile.size / 1024).toFixed(1)} KB)
                            </span>
                          </div>
                          <button
                            onClick={() => setSelectedTableFile(null)}
                            className="text-red-500 hover:text-red-700 text-sm"
                          >
                            Remove
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Admin Notes */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Admin Notes
                  </label>
                  <textarea
                    value={adminNotes}
                    onChange={(e) => setAdminNotes(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md"
                    rows={3}
                    placeholder="Add any notes about the processing..."
                  />
                </div>

                {/* Upload Button */}
                <button
                  onClick={processingMode === 'file' ? uploadChunkFile : uploadTableFile}
                  disabled={uploading || (processingMode === 'file' && !selectedChunkFile) || (processingMode === 'tables' && !selectedTableFile)}
                  className={`w-full px-4 py-3 text-white rounded-lg font-medium hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-all ${
                    processingMode === 'tables' ? 'bg-purple-600 hover:bg-purple-700' : 'bg-blue-600 hover:bg-blue-700'
                  }`}
                >
                  {uploading ? '⏳ Uploading...' : 
                   processingMode === 'file' ? '📄 Upload Chunks File' : 
                   '📊 Upload Tables CSV'}
                </button>
              </div>
            ) : (
              <div className="text-center py-8">
                <div className="text-gray-400 mb-4">
                  <svg className="mx-auto h-12 w-12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <h3 className="text-lg font-medium text-gray-900 mb-2">Select a Document</h3>
                <p className="text-gray-600">
                  Choose a pending document from the left to start processing chunks.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default DocumentProcessing;
