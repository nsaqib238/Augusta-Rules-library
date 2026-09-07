import os
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from .supabase_client import get_supabase_client, get_supabase_client_with_anon_key
from .s3_storage import S3Storage

class SupabaseStorage:
    """Service for storing and retrieving processed chunks from Supabase"""
    
    def __init__(self):
        self.supabase = get_supabase_client()
        # Also create a client with anon key for storage operations
        try:
            self.supabase_anon = get_supabase_client_with_anon_key()
        except:
            self.supabase_anon = None
        
        # Initialize S3 storage
        self.s3_storage = S3Storage()
    
    def _extract_jurisdiction(self, chunk) -> Optional[str]:
        """Extract jurisdiction from chunk object (ClauseChunk)"""
        # Check if jurisdiction is already set
        juris = getattr(chunk, 'jurisdiction', None)
        if juris:
            return juris
        
        # Try to infer from metadata
        metadata = getattr(chunk, 'metadata', {})
        if isinstance(metadata, dict):
            return metadata.get('jurisdiction')
        return None
    
    def _infer_jurisdiction(self, chunk: Dict) -> Optional[str]:
        """Infer jurisdiction from chunk dictionary"""
        # Check if jurisdiction is already set
        if chunk.get('jurisdiction'):
            return chunk.get('jurisdiction')
        
        # Check metadata
        metadata = chunk.get('metadata', {})
        if isinstance(metadata, dict) and metadata.get('jurisdiction'):
            return metadata.get('jurisdiction')
        
        # Check text for jurisdiction hints
        text = chunk.get('text', '').lower()
        if 'australia only' in text or 'australian only' in text:
            return 'AU'
        elif 'new zealand only' in text or 'nz only' in text:
            return 'NZ'
        elif 'joint' in text or 'as/nzs' in text:
            return 'BOTH'
        
        return None  # Unknown jurisdiction
    
    def upload_file_to_storage(self, file_path: str, user_id: str, filename: str) -> str:
        """
        Upload file to S3 Storage only
        
        Args:
            file_path: Local path to the file
            user_id: User ID for organizing files
            filename: Original filename
            
        Returns:
            S3 URL of the uploaded file
        """
        try:
            # Use S3 storage only
            if not self.s3_storage.s3_client:
                raise Exception("S3 storage not available. Please check S3 configuration.")
            
            storage_url = self.s3_storage.upload_file(file_path, user_id, filename)
            
            if not storage_url:
                raise Exception("Failed to upload file to S3 storage")
                
            return storage_url
                
        except Exception as e:
            raise Exception(f"Error uploading file to S3 storage: {str(e)}")
    
    def delete_file_from_storage(self, storage_url: str) -> bool:
        """
        Delete a stored file referenced by a URL.

        Supports:
        - Supabase Storage object URLs (storage/v1/object/...)
        - Supabase S3 gateway URLs (storage/v1/s3/...) when S3 client is configured
        - Direct S3 gateway URLs that include the bucket name in the path
        """
        u = (storage_url or "").strip()
        if not u:
            return False

        # 1) Supabase Storage object URL (most common legacy path)
        # Examples:
        # - .../storage/v1/object/public/documents/uploads/<user>/<file>.pdf
        # - .../storage/v1/object/sign/documents/uploads/<user>/<file>.pdf?token=...
        if "/storage/v1/object/" in u:
            try:
                after = u.split("/storage/v1/object/", 1)[1]
                after = after.split("?", 1)[0]
                parts = [p for p in after.split("/") if p]
                # parts: [public|sign|authenticated, <bucket>, <path...>]
                if len(parts) >= 3 and parts[0] in ("public", "sign", "authenticated"):
                    bucket = parts[1]
                    path = "/".join(parts[2:])
                # Some deployments may omit the visibility segment (rare)
                elif len(parts) >= 2:
                    bucket = parts[0]
                    path = "/".join(parts[1:])
                else:
                    print(f"❌ Invalid Supabase object URL format: {u}")
                    return False

                if not bucket or not path:
                    print(f"❌ Could not extract bucket/path from URL: {u}")
                    return False

                print(f"🔍 Attempting to delete file from Supabase Storage: {bucket}/{path}")
                self.supabase.storage.from_(bucket).remove([path])
                print(f"✅ File deleted via Supabase Storage: {bucket}/{path}")
                return True
            except Exception as e:
                print(f"❌ Supabase Storage deletion failed: {e}")
                # Fall through to S3 attempt if configured

        # 2) S3 gateway deletion (newer path). Works only if S3 client configured.
        print(f"🔍 Attempting to delete file from S3 storage: {u}")
        if not self.s3_storage.s3_client:
            print("❌ S3 client not available")
            return False

        print("🔍 Deleting via S3...")
        s3_result = self.s3_storage.delete_file(u)
        if s3_result:
            print("✅ File deleted via S3")
            return True

        print("❌ S3 deletion failed")
        return False
    
    def save_document_metadata(self, user_id: str, filename: str, codebook: str, 
                              processing_model: str, file_size: int, 
                              chunks_count: int = None, processing_time: float = None,
                              model_path: str = None, analysis_depth: str = 'basic',
                              storage_url: str = None) -> str:
        """
        Save document metadata to Supabase using the safe insert function
        This works for new users who sign up through Supabase Auth
        
        Args:
            user_id: User ID (from Supabase Auth)
            filename: Original filename
            codebook: Codebook type (from frontend selection)
            processing_model: Processing model used (spacy/bert)
            file_size: File size in bytes
            chunks_count: Number of chunks generated
            processing_time: Processing time in seconds
            model_path: Path to the model used
            analysis_depth: Analysis depth (basic/detailed)
            storage_url: URL of the file in Supabase Storage (uploaded by frontend)
            
        Returns:
            Document ID (UUID)
        """
        try:
            # Use the safe_insert_document function for new users
            result = self.supabase.rpc('safe_insert_document', {
                'p_user_id': user_id,
                'p_filename': filename,
                'p_codebook': codebook,
                'p_file_size': file_size,
                'p_processing_model': processing_model,
                'p_analysis_depth': analysis_depth
            }).execute()
            
            if result.data:
                document_id = result.data
                
                # Update document with storage URL if available
                if storage_url:
                    self.supabase.table('documents').update({
                        'storage_url': storage_url
                    }).eq('id', document_id).execute()
                
                return document_id
            else:
                raise Exception("Failed to insert document metadata")
                
        except Exception as e:
            raise Exception(f"Error saving document metadata: {str(e)}")
    
    def update_document_status(self, document_id: str, status: str, chunks_count: int = None) -> bool:
        """
        Update document status and chunks count
        
        Args:
            document_id: Document ID
            status: New status (completed, failed, processing)
            chunks_count: Number of chunks processed
            
        Returns:
            True if successful
        """
        try:
            update_data = {'status': status}
            if chunks_count is not None:
                update_data['chunks_count'] = chunks_count
                
            result = self.supabase.table('documents').update(update_data).eq('id', document_id).execute()
            return True if result.data else False
            
        except Exception as e:
            raise Exception(f"Error updating document status: {str(e)}")
    
    def get_user_documents(self, user_id: str) -> List[Dict]:
        """
        Get documents for a user (own uploads + active company library).
        """
        try:
            own = (
                self.supabase.table('documents')
                .select('*')
                .eq('user_id', user_id)
                .order('created_at', desc=True)
                .execute()
            )
            docs = list(own.data or [])
            seen = {d['id'] for d in docs}

            try:
                from services.company_service import get_active_company_id_for_user

                company_id = get_active_company_id_for_user(user_id)
            except Exception:
                company_id = None

            if company_id:
                company_docs = (
                    self.supabase.table('documents')
                    .select('*')
                    .eq('company_id', company_id)
                    .order('created_at', desc=True)
                    .execute()
                )
                for d in company_docs.data or []:
                    if d['id'] not in seen:
                        docs.append(d)
                        seen.add(d['id'])

            docs.sort(key=lambda d: d.get('created_at') or '', reverse=True)
            return docs
            
        except Exception as e:
            raise Exception(f"Error retrieving user documents: {str(e)}")
    
    def get_document_metadata(self, document_id: str) -> Dict:
        """
        Get document metadata
        
        Args:
            document_id: Document ID
            
        Returns:
            Document metadata
        """
        try:
            result = self.supabase.table('documents').select('*').eq('id', document_id).single().execute()
            if not result.data:
                raise Exception("Document not found")
            
            return result.data
            
        except Exception as e:
            raise Exception(f"Error retrieving document metadata: {str(e)}")
    
    def get_document_with_chunks(self, document_id: str) -> Dict:
        """
        Get a document with all its chunks
        
        Args:
            document_id: Document ID
            
        Returns:
            Document with chunks
        """
        try:
            # Get document metadata
            document = self.get_document_metadata(document_id)
            
            # Get chunks for the document
            chunks = self.get_document_chunks(document_id)
            
            # Add chunks to document
            document['chunks'] = chunks
            
            return document
            
        except Exception as e:
            raise Exception(f"Error retrieving document with chunks: {str(e)}")
    
    def get_document_chunks(self, document_id: str) -> List[Dict]:
        """
        Get all chunks for a document
        
        Args:
            document_id: Document ID
            
        Returns:
            List of chunk data
        """
        try:
            # Get all chunks using multiple queries to bypass the 1000 row limit
            chunks = []
            
            # First, get chunks 0-999
            result1 = self.supabase.table('chunks').select('*').eq('document_id', document_id).order('chunk_index').limit(1000).execute()
            chunks.extend(result1.data if result1.data else [])
            # Then get chunks 1000+
            result2 = self.supabase.table('chunks').select('*').eq('document_id', document_id).order('chunk_index').gt('chunk_index', 999).execute()
            chunks.extend(result2.data if result2.data else [])
            
            # Parse JSON strings back to dictionaries for Pydantic validation
            import json
            for chunk in chunks:
                # Parse entities if it's a JSON string
                if isinstance(chunk.get('entities'), str):
                    try:
                        chunk['entities'] = json.loads(chunk['entities'])
                    except json.JSONDecodeError:
                        chunk['entities'] = {}
                
                # Parse detailed_analysis if it's a JSON string
                if isinstance(chunk.get('detailed_analysis'), str):
                    try:
                        chunk['detailed_analysis'] = json.loads(chunk['detailed_analysis'])
                    except json.JSONDecodeError:
                        chunk['detailed_analysis'] = {}
                
                # Parse metadata if it's a JSON string
                if isinstance(chunk.get('metadata'), str):
                    try:
                        chunk['metadata'] = json.loads(chunk['metadata'])
                    except json.JSONDecodeError:
                        chunk['metadata'] = {}
            
            return chunks
            
        except Exception as e:
            raise Exception(f"Error retrieving document chunks: {str(e)}")
    
    def search_chunks(self, user_id: str, query: str, limit: int = 10) -> List[Dict]:
        """
        Search chunks for a user
        
        Args:
            user_id: User ID
            query: Search query
            limit: Maximum number of results
            
        Returns:
            List of matching chunks
        """
        try:
            # Get user's documents first
            documents = self.get_user_documents(user_id)
            document_ids = [doc['id'] for doc in documents]
            
            if not document_ids:
                return []
            
            # Search in chunks table for user's documents
            result = self.supabase.table('chunks').select('*').in_('document_id', document_ids).textSearch('text', query).limit(limit).execute()
            
            chunks = result.data if result.data else []
            
            # Parse JSON strings back to dictionaries for Pydantic validation
            import json
            for chunk in chunks:
                # Parse entities if it's a JSON string
                if isinstance(chunk.get('entities'), str):
                    try:
                        chunk['entities'] = json.loads(chunk['entities'])
                    except json.JSONDecodeError:
                        chunk['entities'] = {}
                
                # Parse detailed_analysis if it's a JSON string
                if isinstance(chunk.get('detailed_analysis'), str):
                    try:
                        chunk['detailed_analysis'] = json.loads(chunk['detailed_analysis'])
                    except json.JSONDecodeError:
                        chunk['detailed_analysis'] = {}
                
                # Parse metadata if it's a JSON string
                if isinstance(chunk.get('metadata'), str):
                    try:
                        chunk['metadata'] = json.loads(chunk['metadata'])
                    except json.JSONDecodeError:
                        chunk['metadata'] = {}
            
            return chunks
            
        except Exception as e:
            raise Exception(f"Error searching chunks: {str(e)}")
    
    def delete_document(self, document_id: str) -> bool:
        """
        Delete a document and optionally its storage file
        
        Args:
            document_id: Document ID
            
        Returns:
            True if successful
        """
        try:
            print(f"🔍 Starting deletion of document: {document_id}")
            
            # Get document metadata first to get storage URL
            document_metadata = self.get_document_metadata(document_id)
            storage_url = document_metadata.get('storage_url')
            
            print(f"🔍 Document metadata: {document_metadata}")
            print(f"🔍 Storage URL: {storage_url}")

            uid = document_metadata.get("user_id")
            if uid:
                self.delete_document_benchmark_qna(uid, document_id)
            
            # Delete document from database (this will cascade delete chunks)
            result = self.supabase.table('documents').delete().eq('id', document_id).execute()
            
            if not result.data:
                print(f"❌ Failed to delete document from database")
                return False
            
            print(f"✅ Successfully deleted document from database")
            
            # Try to delete from storage if URL exists
            if storage_url:
                print(f"🔍 Attempting to delete from storage: {storage_url}")
                storage_deleted = self.delete_file_from_storage(storage_url)
                if storage_deleted:
                    print(f"✅ Successfully deleted from storage")
                else:
                    print(f"⚠️ Failed to delete from storage, but document was deleted from database")
            else:
                print(f"⚠️ No storage URL found, skipping storage deletion")
            
            return True
            
        except Exception as e:
            print(f"❌ Error in delete_document: {str(e)}")
            raise Exception(f"Error deleting document: {str(e)}")
    
    def delete_document_chunks(self, document_id: str) -> bool:
        """
        Delete all chunks associated with a document
        
        Args:
            document_id: Document ID
            
        Returns:
            True if chunks were deleted successfully
        """
        try:
            print(f"🗑️ Deleting chunks for document: {document_id}")
            
            # Delete chunks from database
            result = self.supabase.table('chunks').delete().eq('document_id', document_id).execute()
            
            # Check if deletion was successful by checking the data
            deleted_count = len(result.data) if result.data else 0
            print(f"✅ Successfully deleted {deleted_count} chunks for document {document_id}")
            return True
            
        except Exception as e:
            print(f"❌ Error deleting chunks: {str(e)}")
            return False

    def download_document_benchmark_qna(self, user_id: str, document_id: str) -> Optional[bytes]:
        """
        Download benchmarks/{user_id}/{document_id}/benchmark_qna.json from Storage.
        Returns None if the object is missing or download fails.
        """
        bucket = (os.getenv("BENCHMARK_SUPABASE_BUCKET") or "documents").strip() or "documents"
        path = f"benchmarks/{user_id}/{document_id}/benchmark_qna.json"
        try:
            return self.supabase.storage.from_(bucket).download(path)
        except Exception as e:
            print(f"⚠️ Benchmark Q&A download failed: {bucket}/{path} — {e}")
            return None

    def delete_document_benchmark_qna(self, user_id: str, document_id: str) -> bool:
        """
        Remove retrieval benchmark artifact from Supabase Storage (matches benchmark_generation_service path).
        Best-effort: no error if the object was never created.
        """
        bucket = (os.getenv("BENCHMARK_SUPABASE_BUCKET") or "documents").strip() or "documents"
        path = f"benchmarks/{user_id}/{document_id}/benchmark_qna.json"
        try:
            self.supabase.storage.from_(bucket).remove([path])
            print(f"✅ Removed benchmark Q&A artifact: {bucket}/{path}")
            return True
        except Exception as e:
            print(f"⚠️ Benchmark Q&A storage delete (ok if missing): {bucket}/{path} — {e}")
            return False
    
    def save_chunks_to_database(self, document_id: str, chunks: List[Dict], user_id: str, codebook: str = None) -> bool:
        """
        Save processed chunks to Supabase database with enhanced schema
        
        Args:
            document_id: Document ID
            chunks: List of chunk dictionaries
            user_id: User ID
            codebook: Codebook name (AS3000, AS3008, etc.)
            
        Returns:
            True if chunks were saved successfully
        """
        try:
            print(f"💾 Saving {len(chunks)} chunks to database for document {document_id}")
            
            # Get document info to extract codebook if not provided
            if not codebook:
                try:
                    doc_result = self.supabase.table('documents').select('codebook').eq('id', document_id).execute()
                    if doc_result.data:
                        codebook = doc_result.data[0].get('codebook', 'AS3000')
                except:
                    codebook = 'AS3000'  # Default fallback
            
            # Prepare chunks for database insertion
            chunks_data = []
            for i, chunk in enumerate(chunks):
                # Handle both ClauseChunk objects and dictionaries
                if hasattr(chunk, 'text'):
                    # ClauseChunk object
                    chunk_data = {
                        'document_id': document_id,
                        'user_id': user_id,
                        'chunk_index': i,
                        'text': chunk.text,
                        'heading': getattr(chunk, 'heading', None) or '',
                        'clause_number': chunk.clause_number,
                        'page_number': getattr(chunk, 'page_number', None),
                        'jurisdiction': self._extract_jurisdiction(chunk),
                        'codebook': codebook,
                        'entities': getattr(chunk, 'entities', []),
                        'detailed_analysis': getattr(chunk, 'detailed_analysis', {}),
                        'metadata': {
                            'printed_page_number': getattr(chunk, 'printed_page_number', None),
                            'source_name': getattr(chunk, 'source_name', None),
                            'level': getattr(chunk, 'level', 1),
                            'parent_clause': getattr(chunk, 'parent_clause', None),
                            'children': getattr(chunk, 'children', [])
                        }
                    }
                else:
                    # Dictionary format (from NERChunker or enhanced format)
                    chunk_data = {
                        'document_id': document_id,
                        'user_id': user_id,
                        'chunk_index': i,
                        'text': chunk['text'],
                        'heading': chunk.get('heading', '') or '',
                        'clause_number': chunk.get('clause_number') or chunk.get('clause_id'),
                        'page_number': chunk.get('page_number'),
                        'jurisdiction': chunk.get('jurisdiction') or self._infer_jurisdiction(chunk),
                        'codebook': chunk.get('codebook') or codebook,
                        'entities': chunk.get('entities', []),
                        'detailed_analysis': chunk.get('detailed_analysis', {}),
                        'metadata': chunk.get('metadata', {})
                    }
                
                chunks_data.append(chunk_data)
            
            # Insert chunks in batches to avoid timeout
            batch_size = 50
            for i in range(0, len(chunks_data), batch_size):
                batch = chunks_data[i:i + batch_size]
                result = self.supabase.table('chunks').insert(batch).execute()
                
                # Check if insertion was successful
                if not result.data:
                    print(f"❌ Error saving chunk batch {i//batch_size + 1}: No data returned")
                    return False
                
                print(f"✅ Saved chunk batch {i//batch_size + 1} ({len(batch)} chunks)")
            
            print(f"✅ Successfully saved all {len(chunks)} chunks to database")

            from services.chunk_embedding_service import sync_document_embeddings

            try:
                sync_document_embeddings(document_id, user_id)
            except Exception as emb_exc:
                print(f"⚠️ Embedding sync failed: {emb_exc}")

            return True
            
        except Exception as e:
            print(f"❌ Error saving chunks to database: {str(e)}")
            return False
    
    def get_document_chunks_from_database(self, document_id: str, limit: int = 100, offset: int = 0) -> List[Dict]:
        """
        Retrieve chunks from database for a specific document
        
        Args:
            document_id: Document ID
            limit: Maximum number of chunks to return
            offset: Number of chunks to skip
            
        Returns:
            List of chunk dictionaries
        """
        try:
            print(f"🔍 Retrieving chunks from database for document {document_id}")
            
            result = self.supabase.table('chunks').select('*').eq('document_id', document_id).order('chunk_index').range(offset, offset + limit - 1).execute()
            
            chunks = result.data or []
            print(f"✅ Retrieved {len(chunks)} chunks from database")
            
            # Parse JSON strings back to dictionaries for Pydantic validation
            import json
            for chunk in chunks:
                # Parse entities if it's a JSON string
                if isinstance(chunk.get('entities'), str):
                    try:
                        chunk['entities'] = json.loads(chunk['entities'])
                    except json.JSONDecodeError:
                        chunk['entities'] = {}
                
                # Parse detailed_analysis if it's a JSON string
                if isinstance(chunk.get('detailed_analysis'), str):
                    try:
                        chunk['detailed_analysis'] = json.loads(chunk['detailed_analysis'])
                    except json.JSONDecodeError:
                        chunk['detailed_analysis'] = {}
                
                # Parse metadata if it's a JSON string
                if isinstance(chunk.get('metadata'), str):
                    try:
                        chunk['metadata'] = json.loads(chunk['metadata'])
                    except json.JSONDecodeError:
                        chunk['metadata'] = {}
            
            return chunks
            
        except Exception as e:
            print(f"❌ Error retrieving chunks from database: {str(e)}")
            return []
    
    def _expand_search_terms(self, query: str) -> List[str]:
        """
        Expand search terms to catch related concepts and synonyms
        
        Args:
            query: Original search query
            
        Returns:
            List of expanded search terms
        """
        expanded_terms = [query]  # Always include original query
        
        # Define comprehensive term expansions for electrical concepts
        term_expansions = {
            'barriers or enclosures': ['barriers', 'enclosures', 'protection barriers', 'protective enclosures', 'IPXXB', 'IP2X', 'IP4X', 'degree of protection', 'live parts inside', 'behind barriers'],
            'protection by barriers': ['barriers', 'enclosures', 'protection barriers', 'protective enclosures', 'IPXXB', 'IP2X', 'IP4X', 'degree of protection'],
            'barriers enclosures': ['barriers', 'enclosures', 'protection barriers', 'protective enclosures', 'IPXXB', 'IP2X', 'IP4X'],
            'barriers': ['barriers', 'enclosures', 'protection barriers', 'protective enclosures', 'IPXXB', 'IP2X', 'IP4X', 'degree of protection'],
            'enclosures': ['enclosures', 'barriers', 'protection barriers', 'protective enclosures', 'IPXXB', 'IP2X', 'IP4X', 'degree of protection'],
            'live parts': ['live parts', 'energized parts', 'electrical parts', 'conductive parts', 'inside enclosures', 'behind barriers'],
            'automatic disconnection': ['automatic disconnection', 'RCD', 'residual current device', 'earth leakage', 'protection by automatic disconnection'],
            'circuit breaker': ['circuit breaker', 'MCB', 'miniature circuit breaker', 'overcurrent protection', 'moulded-case circuit-breakers'],
            'earthing': ['earthing', 'grounding', 'earth connection', 'protective conductor', 'earth electrode'],
            'protection': ['protection', 'protective', 'safety', 'barriers', 'enclosures', 'automatic disconnection', 'earthing']
        }
        
        query_lower = query.lower()
        
        # Check if query matches any expansion patterns (check all patterns, not just first match)
        for pattern, expansions in term_expansions.items():
            if pattern in query_lower:
                expanded_terms.extend(expansions)
                # Don't break - check all patterns for comprehensive coverage
        
        # Add individual words from the query
        words = query_lower.split()
        for word in words:
            if len(word) > 3:  # Only add words longer than 3 characters
                expanded_terms.append(word)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_terms = []
        for term in expanded_terms:
            if term not in seen:
                seen.add(term)
                unique_terms.append(term)
        
        print(f"🔍 Expanded search terms: {unique_terms}")
        return unique_terms

    def search_chunks_in_database(self, document_id: str, query: str, limit: int = 10) -> List[Dict]:
        """
        Search chunks in database using hybrid semantic + text search with expanded terms
        
        Args:
            document_id: Document ID
            query: Search query
            limit: Maximum number of results
            
        Returns:
            List of matching chunks
        """
        try:
            print(f"🔍 Performing hybrid search for query: {query}")
            
            # Expand search terms for better coverage
            expanded_queries = self._expand_search_terms(query)
            
            
            # Try hybrid search first (semantic + text)
            try:
                from .semantic_search_service import SemanticSearchService
                semantic_service = SemanticSearchService()
                
                all_results = []
                for expanded_query in expanded_queries[:5]:  # Try more expanded terms
                    # Use lower threshold for better recall
                    hybrid_results = semantic_service.hybrid_search(expanded_query, document_id, limit, threshold=0.4)
                    all_results.extend(hybrid_results)
                
                # Remove duplicates and sort by score
                unique_results = {}
                for result in all_results:
                    chunk_id = result['chunk_id']
                    if chunk_id not in unique_results or result['combined_score'] > unique_results[chunk_id]['combined_score']:
                        unique_results[chunk_id] = result
                
                hybrid_results = list(unique_results.values())
                hybrid_results.sort(key=lambda x: x['combined_score'], reverse=True)
                
                if hybrid_results:
                    print(f"✅ Hybrid search found {len(hybrid_results)} results")
                    # Convert to standard format
                    chunks = []
                    for result in hybrid_results[:limit]:
                        chunk = {
                            'id': result['chunk_id'],
                            'text': result['text'],
                            'document_id': result['document_id'],
                            'clause_number': result['clause_number'],
                            'page_number': result['page_number'],
                            'metadata': result['metadata'],
                            'similarity_score': result['combined_score'],
                            'search_source': result['source']
                        }
                        chunks.append(chunk)
                    
                    
                    return chunks
                else:
                    print("⚠️ Hybrid search returned no results, falling back to text search")
            except Exception as e:
                print(f"⚠️ Hybrid search failed: {str(e)}, falling back to text search")
            
            # Fallback to text search with clause number extraction
            print("🔄 Falling back to text search with clause extraction")
            
            # Extract clause numbers from query (e.g., "clause 2.4.3" -> "2.4.3")
            import re
            clause_pattern = r'(?:clause\s+)?(\d+(?:\.\d+)*)'
            clause_matches = re.findall(clause_pattern, query.lower())
            
            # If we found clause numbers, search for them specifically
            if clause_matches:
                print(f"🔢 Found clause numbers in query: {clause_matches}")
                for clause_num in clause_matches:
                    try:
                        # Search for the clause number directly
                        result = self.supabase.table('chunks').select('*').eq('document_id', document_id).ilike('text', f'%{clause_num}%').limit(limit).execute()
                        if result.data:
                            chunks = result.data
                            print(f"✅ Clause number search found {len(chunks)} matching chunks for {clause_num}")
                            return chunks
                    except Exception as e:
                        print(f"⚠️ Clause number search failed for {clause_num}: {str(e)}")
            
            # First try: simple text search with ILIKE using expanded terms (most reliable)
            try:
                all_chunks = []
                for expanded_query in expanded_queries[:5]:  # Try first 5 expanded terms
                    result = self.supabase.table('chunks').select('*').eq('document_id', document_id).ilike('text', f'%{expanded_query}%').limit(limit).execute()
                    if result.data:
                        all_chunks.extend(result.data)
                
                if all_chunks:
                    # Remove duplicates based on chunk ID
                    unique_chunks = {}
                    for chunk in all_chunks:
                        chunk_id = chunk.get('id')
                        if chunk_id not in unique_chunks:
                            unique_chunks[chunk_id] = chunk
                    
                    chunks = list(unique_chunks.values())[:limit]
                    
                    
                    print(f"✅ ILIKE search with expanded terms found {len(chunks)} matching chunks in database")
                    return chunks
            except Exception as e:
                print(f"⚠️ ILIKE search failed: {str(e)}")
            
            # Second try: try textSearch if available
            try:
                result = self.supabase.table('chunks').select('*').eq('document_id', document_id).textSearch('text', query).limit(limit).execute()
                if result.data:
                    chunks = result.data
                    print(f"✅ textSearch found {len(chunks)} matching chunks in database")
                    return chunks
            except Exception as e:
                print(f"⚠️ textSearch failed: {str(e)}")
            
            # Third try: get all chunks and filter manually
            try:
                print(f"🔄 Falling back to manual search for query: {query}")
                all_chunks = self.get_document_chunks_from_database(document_id, limit=500, offset=0)
                if all_chunks:
                    # Filter chunks manually
                    matching_chunks = []
                    query_lower = query.lower()
                    for chunk in all_chunks:
                        text = chunk.get('text', '').lower()
                        if query_lower in text:
                            matching_chunks.append(chunk)
                            if len(matching_chunks) >= limit:
                                break
                    
                    print(f"✅ Manual search found {len(matching_chunks)} matching chunks")
                    return matching_chunks
            except Exception as e:
                print(f"⚠️ Manual search failed: {str(e)}")
            
            print(f"❌ All search methods failed for query: {query}")
            return []
            
        except Exception as e:
            print(f"❌ Error searching chunks in database: {str(e)}")
            return []
    

    
 