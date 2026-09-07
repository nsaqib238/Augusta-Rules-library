import os
import asyncio
from typing import List, Dict, Any, Optional
from .supabase_client import get_supabase_client
from .async_utils import run_blocking
import logging
from uuid import UUID

logger = logging.getLogger(__name__)


def _validate_user_uuid(user_id: str) -> str:
    """Reject non-UUID user ids before interpolating into SQL."""
    return str(UUID((user_id or "").strip()))


class SchemaManager:
    """Service for managing user-specific schemas for maximum scalability"""
    
    def __init__(self):
        self.supabase = get_supabase_client()
    
    async def create_user_schema(self, user_id: str) -> bool:
        """Create a complete schema for a new user"""
        try:
            user_id = _validate_user_uuid(user_id)
            logger.info(f"Creating schema for user: {user_id}")
            
            # Create user schema
            schema_sql = f"""
            CREATE SCHEMA IF NOT EXISTS user_{user_id};
            """
            await self._execute_sql(schema_sql)
            
            # Create documents table in user schema
            documents_sql = f"""
            CREATE TABLE IF NOT EXISTS user_{user_id}.documents (
                id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                filename TEXT NOT NULL,
                processing_model TEXT DEFAULT 'admin' CHECK (processing_model IN ('spacy', 'bert', 'admin', 'modal_pdf_pipeline')),
                file_size INTEGER,
                storage_url TEXT,
                status TEXT DEFAULT 'pending_admin_review' CHECK (status IN ('pending_admin_review', 'admin_processing', 'pdf_processing', 'ready_for_search', 'failed')),
                chunk_count INTEGER DEFAULT 0,
                processing_time_seconds DECIMAL(5,2),
                admin_status TEXT DEFAULT 'pending' CHECK (admin_status IN ('pending', 'processing', 'completed', 'rejected')),
                admin_notes TEXT,
                admin_processed_at TIMESTAMP WITH TIME ZONE,
                admin_user_id UUID REFERENCES auth.users(id),
                original_chunk_count INTEGER DEFAULT 0,
                refined_chunk_count INTEGER DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            """
            await self._execute_sql(documents_sql)
            
            # Create chunks table in user schema
            chunks_sql = f"""
            CREATE TABLE IF NOT EXISTS user_{user_id}.chunks (
                id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                document_id UUID REFERENCES user_{user_id}.documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                clause_number TEXT,
                page_number INTEGER,
                entities JSONB DEFAULT '[]',
                detailed_analysis JSONB DEFAULT '{{}}',
                metadata JSONB DEFAULT '{{}}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            """
            await self._execute_sql(chunks_sql)
            
            # Create indexes for user schema
            await self._create_user_schema_indexes(user_id)
            
            # Enable RLS for user schema
            await self._enable_user_schema_rls(user_id)
            
            # Grant permissions
            await self._grant_user_schema_permissions(user_id)
            
            logger.info(f"Successfully created schema for user: {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating schema for user {user_id}: {e}")
            return False
    
    async def _execute_sql(self, sql: str):
        """Execute SQL statement (offloaded — supabase-py is sync)."""
        try:
            return await run_blocking(
                lambda: self.supabase.rpc('execute_sql', {'sql_text': sql}).execute()
            )
        except Exception as e:
            logger.error(f"SQL execution error: {e}")
            raise
    
    async def _create_user_schema_indexes(self, user_id: str):
        """Create indexes for user schema tables"""
        indexes_sql = f"""
        -- Documents table indexes
        CREATE INDEX IF NOT EXISTS idx_user_{user_id}_documents_status ON user_{user_id}.documents(status);
        CREATE INDEX IF NOT EXISTS idx_user_{user_id}_documents_admin_status ON user_{user_id}.documents(admin_status);
        CREATE INDEX IF NOT EXISTS idx_user_{user_id}_documents_codebook ON user_{user_id}.documents(codebook);
        CREATE INDEX IF NOT EXISTS idx_user_{user_id}_documents_created_at ON user_{user_id}.documents(created_at);
        CREATE INDEX IF NOT EXISTS idx_user_{user_id}_documents_filename ON user_{user_id}.documents(filename);
        
        -- Chunks table indexes
        CREATE INDEX IF NOT EXISTS idx_user_{user_id}_chunks_document_id ON user_{user_id}.chunks(document_id);
        CREATE INDEX IF NOT EXISTS idx_user_{user_id}_chunks_text ON user_{user_id}.chunks USING gin(to_tsvector('english', text));
        CREATE INDEX IF NOT EXISTS idx_user_{user_id}_chunks_clause_number ON user_{user_id}.chunks(clause_number);
        CREATE INDEX IF NOT EXISTS idx_user_{user_id}_chunks_page_number ON user_{user_id}.chunks(page_number);
        CREATE INDEX IF NOT EXISTS idx_user_{user_id}_chunks_created_at ON user_{user_id}.chunks(created_at);
        """
        await self._execute_sql(indexes_sql)
    
    async def _enable_user_schema_rls(self, user_id: str):
        """Enable RLS for user schema tables"""
        rls_sql = f"""
        -- Enable RLS on user schema tables
        ALTER TABLE user_{user_id}.documents ENABLE ROW LEVEL SECURITY;
        ALTER TABLE user_{user_id}.chunks ENABLE ROW LEVEL SECURITY;
        
        -- User can access their own schema
        CREATE POLICY "User can access own documents" ON user_{user_id}.documents
            FOR ALL USING (auth.uid() = '{user_id}');
            
        CREATE POLICY "User can access own chunks" ON user_{user_id}.chunks
            FOR ALL USING (auth.uid() = '{user_id}');
            
        -- Admins can access any user's schema
        CREATE POLICY "Admins can access user documents" ON user_{user_id}.documents
            FOR ALL USING (
                EXISTS (
                    SELECT 1 FROM public.profiles 
                    WHERE profiles.id = auth.uid() 
                    AND profiles.role IN ('admin', 'engineer', 'inspector')
                )
            );
            
        CREATE POLICY "Admins can access user chunks" ON user_{user_id}.chunks
            FOR ALL USING (
                EXISTS (
                    SELECT 1 FROM public.profiles 
                    WHERE profiles.id = auth.uid() 
                    AND profiles.role IN ('admin', 'engineer', 'inspector')
                )
            );
        """
        await self._execute_sql(rls_sql)
    
    async def _grant_user_schema_permissions(self, user_id: str):
        """Grant permissions for user schema"""
        permissions_sql = f"""
        -- Grant usage on schema
        GRANT USAGE ON SCHEMA user_{user_id} TO authenticated;
        
        -- Grant permissions on tables
        GRANT ALL ON user_{user_id}.documents TO authenticated;
        GRANT ALL ON user_{user_id}.chunks TO authenticated;
        
        -- Grant permissions on sequences
        GRANT ALL ON ALL SEQUENCES IN SCHEMA user_{user_id} TO authenticated;
        """
        await self._execute_sql(permissions_sql)
    
    async def get_user_documents(self, user_id: str, limit: int = 50, offset: int = 0) -> List[Dict]:
        """Get documents for a specific user from their schema"""
        try:
            result = self.supabase.table(f'user_{user_id}.documents').select('*').limit(limit).offset(offset).order('created_at', desc=True).execute()
            return result.data
        except Exception as e:
            logger.error(f"Error getting documents for user {user_id}: {e}")
            return []
    
    async def get_user_chunks(self, user_id: str, document_id: str) -> List[Dict]:
        """Get chunks for a specific user's document from their schema"""
        try:
            result = self.supabase.table(f'user_{user_id}.chunks').select('*').eq('document_id', document_id).order('chunk_index').execute()
            return result.data
        except Exception as e:
            logger.error(f"Error getting chunks for user {user_id}, document {document_id}: {e}")
            return []
    
    async def insert_user_document(self, user_id: str, document_data: Dict) -> Optional[str]:
        """Insert document into user's schema"""
        try:
            result = self.supabase.table(f'user_{user_id}.documents').insert(document_data).execute()
            return result.data[0]['id'] if result.data else None
        except Exception as e:
            logger.error(f"Error inserting document for user {user_id}: {e}")
            return None
    
    async def insert_user_chunks(self, user_id: str, chunks_data: List[Dict]) -> bool:
        """Insert chunks into user's schema"""
        try:
            result = self.supabase.table(f'user_{user_id}.chunks').insert(chunks_data).execute()
            return len(result.data) > 0
        except Exception as e:
            logger.error(f"Error inserting chunks for user {user_id}: {e}")
            return False
    
    async def update_user_document(self, user_id: str, document_id: str, update_data: Dict) -> bool:
        """Update document in user's schema"""
        try:
            result = self.supabase.table(f'user_{user_id}.documents').update(update_data).eq('id', document_id).execute()
            return len(result.data) > 0
        except Exception as e:
            logger.error(f"Error updating document for user {user_id}: {e}")
            return False
    
    async def delete_user_schema(self, user_id: str) -> bool:
        """Delete entire schema for a user (when user is deleted)"""
        try:
            drop_sql = f"""
            DROP SCHEMA IF EXISTS user_{user_id} CASCADE;
            """
            await self._execute_sql(drop_sql)
            logger.info(f"Successfully deleted schema for user: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting schema for user {user_id}: {e}")
            return False
    
    async def get_user_document(self, user_id: str, document_id: str) -> Optional[Dict]:
        """Get a specific document from user's schema"""
        try:
            result = self.supabase.table(f'user_{user_id}.documents').select('*').eq('id', document_id).single().execute()
            return result.data if result.data else None
        except Exception as e:
            logger.error(f"Error getting document for user {user_id}, document {document_id}: {e}")
            return None
    
    async def delete_user_document(self, user_id: str, document_id: str) -> bool:
        """Delete a document and its chunks from user's schema"""
        try:
            # Delete chunks first
            self.supabase.table(f'user_{user_id}.chunks').delete().eq('document_id', document_id).execute()
            
            # Delete document
            result = self.supabase.table(f'user_{user_id}.documents').delete().eq('id', document_id).execute()
            return len(result.data) > 0
        except Exception as e:
            logger.error(f"Error deleting document for user {user_id}, document {document_id}: {e}")
            return False

    async def get_user_stats(self, user_id: str) -> Dict:
        """Get statistics for a user from their schema"""
        try:
            # Get document count
            doc_result = self.supabase.table(f'user_{user_id}.documents').select('id', count='exact').execute()
            doc_count = doc_result.count if doc_result.count else 0
            
            # Get chunk count
            chunk_result = self.supabase.table(f'user_{user_id}.chunks').select('id', count='exact').execute()
            chunk_count = chunk_result.count if chunk_result.count else 0
            
            # Get status breakdown
            status_result = self.supabase.table(f'user_{user_id}.documents').select('status').execute()
            status_counts = {}
            for doc in status_result.data:
                status = doc['status']
                status_counts[status] = status_counts.get(status, 0) + 1
            
            return {
                'total_documents': doc_count,
                'total_chunks': chunk_count,
                'status_breakdown': status_counts
            }
        except Exception as e:
            logger.error(f"Error getting stats for user {user_id}: {e}")
            return {'total_documents': 0, 'total_chunks': 0, 'status_breakdown': {}}
    
    async def search_user_chunks(self, user_id: str, query: str, limit: int = 10) -> List[Dict]:
        """Search chunks for a specific user in their schema"""
        try:
            # Use full-text search
            result = self.supabase.table(f'user_{user_id}.chunks').select('*').text_search('text', query).limit(limit).execute()
            return result.data
        except Exception as e:
            logger.error(f"Error searching chunks for user {user_id}: {e}")
            return []
    
    async def get_all_user_schemas(self) -> List[str]:
        """Get list of all user schemas (admin function)"""
        try:
            # Query to get all schemas that start with 'user_'
            result = self.supabase.rpc('execute_sql', {
                'sql': """
                SELECT schema_name 
                FROM information_schema.schemata 
                WHERE schema_name LIKE 'user_%'
                ORDER BY schema_name;
                """
            }).execute()
            
            if result.data and isinstance(result.data, str):
                # Parse the result if it's a string
                return []
            return []
        except Exception as e:
            logger.error(f"Error getting user schemas: {e}")
            return []
    
    async def schema_exists(self, user_id: str) -> bool:
        """Check if user schema exists"""
        try:
            result = self.supabase.rpc('execute_sql', {
                'sql': f"""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.schemata 
                    WHERE schema_name = 'user_{user_id}'
                );
                """
            }).execute()
            return True  # If no error, schema exists
        except Exception as e:
            logger.error(f"Error checking schema existence for user {user_id}: {e}")
            return False
