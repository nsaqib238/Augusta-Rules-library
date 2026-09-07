from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from services.supabase_storage import SupabaseStorage
from middleware.subscription_check import get_current_user, check_admin_access

router = APIRouter()

class DocumentResponse(BaseModel):
    id: str
    filename: str
    codebook: str
    processing_model: str
    status: str
    file_size: int
    chunks_count: Optional[int]
    processing_time_seconds: Optional[float]
    created_at: str
    storage_url: Optional[str]

class ChunkResponse(BaseModel):
    id: str
    chunk_index: int
    text: str
    clause_number: Optional[str]
    page_number: Optional[int]
    entities: dict
    detailed_analysis: Optional[dict]
    metadata: dict

class DocumentWithChunksResponse(BaseModel):
    id: str
    filename: str
    codebook: str
    processing_model: str
    status: str
    chunks_count: int
    processing_time_seconds: Optional[float]
    analysis_depth: str
    created_at: str
    updated_at: str
    chunks: List[ChunkResponse]

def _ensure_document_owner(document: dict, user_id: str) -> None:
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.get('user_id') == user_id:
        return
    company_id = document.get('company_id')
    if company_id:
        try:
            from services.company_service import get_active_company_id_for_user

            if get_active_company_id_for_user(user_id) == company_id:
                return
        except Exception:
            pass
    raise HTTPException(status_code=403, detail="Access denied")

@router.get("", response_model=List[DocumentResponse])
async def get_user_documents(
    current_user: str = Depends(get_current_user)
):
    """
    Get all documents for the authenticated user
    """
    try:
        # Initialize Supabase storage
        supabase_storage = SupabaseStorage()
        
        # Get documents for the user
        documents = supabase_storage.get_user_documents(current_user)
        
        return documents
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch documents: {str(e)}")

@router.get("/search", response_model=List[dict])
async def search_chunks(
    query: str = Query(..., description="Search query"),
    limit: int = Query(10, description="Maximum number of results to return"),
    user_id: Optional[str] = Query(None, description="Deprecated. User ID comes from the bearer token."),
    current_user: str = Depends(get_current_user),
):
    """
    Search chunks for a user
    """
    try:
        if user_id and user_id != current_user:
            raise HTTPException(status_code=403, detail="Access denied")
        supabase_storage = SupabaseStorage()
        results = supabase_storage.search_chunks(current_user, query, limit)

        return results
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/{document_id}", response_model=DocumentWithChunksResponse)
async def get_document_with_chunks(
    document_id: str,
    user_id: Optional[str] = Query(None, description="Deprecated. User ID comes from the bearer token."),
    current_user: str = Depends(get_current_user),
):
    """
    Get a specific document with all its chunks
    """
    try:
        supabase_storage = SupabaseStorage()
        document = supabase_storage.get_document_with_chunks(document_id)
        
        if user_id and user_id != current_user:
            raise HTTPException(status_code=403, detail="Access denied")
        _ensure_document_owner(document, current_user)
        
        return document
    except HTTPException:
        raise
    except Exception as e:
        if "Document not found" in str(e):
            raise HTTPException(status_code=404, detail="Document not found")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve document: {str(e)}")

@router.get("/{document_id}/chunks", response_model=List[ChunkResponse])
async def get_document_chunks(
    document_id: str,
    user_id: Optional[str] = Query(None, description="Deprecated. User ID comes from the bearer token."),
    limit: int = Query(None, description="Maximum number of chunks to return (None = all chunks)"),
    offset: int = Query(0, description="Number of chunks to skip"),
    current_user: str = Depends(get_current_user),
):
    """
    Get chunks for a specific document with optional pagination
    """
    try:
        supabase_storage = SupabaseStorage()
        document_metadata = supabase_storage.get_document_metadata(document_id)
        if user_id and user_id != current_user:
            raise HTTPException(status_code=403, detail="Access denied")
        _ensure_document_owner(document_metadata, current_user)

        chunks = supabase_storage.get_document_chunks(document_id)
        
        # Add document_id and authenticated user_id to each chunk so stored embeddings can be used.
        for chunk in chunks:
            chunk['document_id'] = document_id
            chunk['user_id'] = current_user
        
        # Apply pagination only if limit is specified
        if limit is not None:
            chunks = chunks[offset:offset + limit]
        elif offset > 0:
            chunks = chunks[offset:]
        
        return chunks
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve chunks: {str(e)}")

@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    user_id: Optional[str] = Query(None, description="Deprecated. User ID comes from the bearer token."),
    current_user: str = Depends(get_current_user),
):
    """
    Delete a document and its associated storage file
    """
    try:
        print(f"🔍 DELETE request for document: {document_id}")
        print(f"🔍 Authenticated user ID: {current_user}")
        if user_id and user_id != current_user:
            raise HTTPException(status_code=403, detail="Access denied")
        user_id = current_user
        
        # Initialize Supabase storage
        supabase_storage = SupabaseStorage()
        
        # Get document metadata first to verify ownership and get storage URL
        document_metadata = supabase_storage.get_document_metadata(document_id)
        print(f"🔍 Document metadata: {document_metadata}")
        
        # Verify the document belongs to the authenticated user
        if document_metadata.get('user_id') != user_id:
            print(f"❌ Access denied: document belongs to {document_metadata.get('user_id')}, user is {user_id}")
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Get storage URL before deleting the document
        storage_url = document_metadata.get('storage_url')
        print(f"🔍 Storage URL: {storage_url}")
        
        # Delete document from database
        result = supabase_storage.supabase.table('documents').delete().eq('id', document_id).eq('user_id', user_id).execute()
        
        # Check if deletion was successful by checking the data
        if not result.data:
            print(f"❌ No document found to delete or deletion failed")
            raise HTTPException(status_code=404, detail="Document not found or deletion failed")
        
        print(f"✅ Document deleted from database: {result.data}")
        
        # Delete associated chunks from database
        print(f"🗑️ Deleting chunks for document {document_id}")
        chunks_deleted = supabase_storage.delete_document_chunks(document_id)
        if chunks_deleted:
            print(f"✅ Successfully deleted chunks for document {document_id}")
        else:
            print(f"⚠️ Failed to delete chunks for document {document_id}, but document deletion continues")

        # Remove benchmark_qna.json from Supabase Storage (same path as benchmark generation)
        supabase_storage.delete_document_benchmark_qna(user_id, document_id)
        
        # Delete file from storage
        if storage_url:
            print(f"🔍 Attempting to delete file from storage: {storage_url}")
            storage_deleted = supabase_storage.delete_file_from_storage(storage_url)
            if storage_deleted:
                print(f"✅ Successfully deleted file from storage")
            else:
                print(f"⚠️ Failed to delete file from storage, but document was deleted from database")
        else:
            print("⚠️ No storage URL found for document")
        
        print(f"✅ Document deleted successfully")
        return {"message": "Document deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in delete_document endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")

@router.post("/test-storage-delete")
async def test_storage_deletion(
    storage_url: str,
    current_admin: str = Depends(check_admin_access),
):
    """
    Test endpoint to manually test storage deletion
    """
    try:
        print(f"🧪 Testing storage deletion for URL: {storage_url}")
        
        # Initialize Supabase storage
        supabase_storage = SupabaseStorage()
        
        # Test storage deletion
        success = supabase_storage.delete_file_from_storage(storage_url)
        
        # List objects after deletion to verify
        try:
            objects_result = supabase_storage.supabase.storage.from_('documents').list()
            print(f"🧪 Objects in bucket after deletion: {objects_result}")
        except Exception as e:
            print(f"❌ Error listing objects after deletion: {e}")
        
        return {
            "success": success,
            "storage_url": storage_url,
            "message": "Storage deletion test completed"
        }
        
    except Exception as e:
        print(f"❌ Error in test storage deletion: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Test failed: {str(e)}")

@router.get("/debug-storage-list")
async def debug_storage_list(
    current_admin: str = Depends(check_admin_access),
):
    """
    Debug endpoint to list all files in storage
    """
    try:
        print(f"🔍 Debugging storage list...")
        
        # Initialize Supabase storage
        supabase_storage = SupabaseStorage()
        
        # List all objects in storage
        try:
            objects_result = supabase_storage.supabase.storage.from_('documents').list()
            print(f"🔍 Objects in storage: {objects_result}")
            
            return {
                "objects": objects_result,
                "message": "Storage list debug completed"
            }
        except Exception as e:
            print(f"❌ Error listing storage objects: {e}")
            return {
                "error": str(e),
                "message": "Failed to list storage objects"
            }
        
    except Exception as e:
        print(f"❌ Error in debug storage list: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Debug failed: {str(e)}")

@router.post("/debug-storage")
async def debug_storage_info(
    current_admin: str = Depends(check_admin_access),
):
    """
    Debug endpoint to check storage configuration
    """
    try:
        print(f"🔍 Debugging storage configuration...")
        
        # Initialize Supabase storage
        supabase_storage = SupabaseStorage()
        
        # Test basic storage operations
        try:
            # List buckets
            buckets_result = supabase_storage.supabase.storage.list_buckets()
            print(f"🔍 Buckets: {buckets_result}")
        except Exception as e:
            print(f"❌ Error listing buckets: {e}")
        
        try:
            # List objects in documents bucket
            objects_result = supabase_storage.supabase.storage.from_('documents').list()
            print(f"🔍 Objects in documents bucket: {objects_result}")
        except Exception as e:
            print(f"❌ Error listing objects: {e}")
        
        return {
            "message": "Storage debug completed",
            "service_role_available": supabase_storage.supabase is not None,
            "anon_key_available": supabase_storage.supabase_anon is not None
        }
        
    except Exception as e:
        print(f"❌ Error in debug storage: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Debug failed: {str(e)}")

@router.get("/{document_id}/status")
async def get_document_status(
    document_id: str,
    user_id: Optional[str] = Query(None, description="Deprecated. User ID comes from the bearer token."),
    current_user: str = Depends(get_current_user),
):
    """
    Get processing status of a document
    """
    try:
        if user_id and user_id != current_user:
            raise HTTPException(status_code=403, detail="Access denied")
        supabase_storage = SupabaseStorage()
        documents = supabase_storage.get_user_documents(current_user)
        
        # Find the specific document
        document = next((doc for doc in documents if doc['id'] == document_id), None)
        
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        return {
            "document_id": document_id,
            "status": document['status'],
            "chunks_count": document.get('chunks_count', 0),
            "processing_time_seconds": document.get('processing_time_seconds'),
            "created_at": document['created_at'],
            "updated_at": document['updated_at']
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get document status: {str(e)}") 

@router.get("/{document_id}/chunks/debug")
async def debug_document_chunks(
    document_id: str,
    user_id: Optional[str] = Query(None, description="Deprecated. User ID comes from the bearer token."),
    current_user: str = Depends(get_current_user),
):
    """
    Debug endpoint to check chunks for a document
    """
    try:
        print(f"🔍 Debug: Checking chunks for document {document_id}")
        
        # Initialize Supabase storage
        supabase_storage = SupabaseStorage()
        document_metadata = supabase_storage.get_document_metadata(document_id)
        if user_id and user_id != current_user:
            raise HTTPException(status_code=403, detail="Access denied")
        _ensure_document_owner(document_metadata, current_user)
        
        # Check if chunks exist
        result = supabase_storage.supabase.table('chunks').select('*').eq('document_id', document_id).execute()
        
        chunks = result.data or []
        print(f"✅ Found {len(chunks)} chunks for document {document_id}")
        
        # Show first few chunks
        chunk_previews = []
        for i, chunk in enumerate(chunks[:3]):
            chunk_previews.append({
                'index': chunk.get('chunk_index'),
                'text_preview': chunk.get('text', '')[:100] + "...",
                'page_number': chunk.get('page_number'),
                'clause_number': chunk.get('clause_number')
            })
        
        return {
            "document_id": document_id,
            "chunks_count": len(chunks),
            "chunk_previews": chunk_previews,
            "total_chunks": len(chunks)
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in debug endpoint: {str(e)}")
        return {"error": str(e), "chunks_count": 0} 