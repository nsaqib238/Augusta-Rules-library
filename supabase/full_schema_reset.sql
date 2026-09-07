-- ===========================================
-- FULL SCHEMA RESET (optional — destructive)
-- Drops app objects. Run before combined_setup.sql for a clean deploy.
-- Schema setup lives only in combined_setup.sql (no patch files).
-- ===========================================

DROP VIEW IF EXISTS has_subscription CASCADE;
DROP VIEW IF EXISTS access_level CASCADE;

DROP FUNCTION IF EXISTS public.search_chunks_vector(vector(384), UUID, UUID, INTEGER, TEXT, TEXT) CASCADE;
DROP FUNCTION IF EXISTS public.finish_pdf_processing_job(UUID, BOOLEAN, TEXT) CASCADE;
DROP FUNCTION IF EXISTS public.try_claim_pdf_processing_job(UUID, UUID, INT, INT, TEXT, INT) CASCADE;
DROP FUNCTION IF EXISTS public.get_document_tables(UUID) CASCADE;
DROP FUNCTION IF EXISTS public.search_standard_tables(UUID, TEXT, UUID, TEXT, INTEGER) CASCADE;
DROP FUNCTION IF EXISTS public.get_document_stats(UUID) CASCADE;
DROP FUNCTION IF EXISTS public.search_chunks_enhanced(UUID, TEXT, TEXT, TEXT, INTEGER) CASCADE;
DROP FUNCTION IF EXISTS public.safe_insert_document(UUID, TEXT, TEXT, INTEGER, TEXT, TEXT) CASCADE;
DROP FUNCTION IF EXISTS public.execute_sql(TEXT) CASCADE;
DROP FUNCTION IF EXISTS public.chunks_fill_codebook_from_document() CASCADE;
DROP FUNCTION IF EXISTS public.documents_propagate_codebook_to_chunks() CASCADE;
DROP FUNCTION IF EXISTS public.delete_document_chunks() CASCADE;
DROP FUNCTION IF EXISTS public.update_updated_at_column() CASCADE;
DROP FUNCTION IF EXISTS public.handle_new_user() CASCADE;
DROP FUNCTION IF EXISTS public.update_conversation_timestamp() CASCADE;

DROP TABLE IF EXISTS public.conversation_messages CASCADE;
DROP TABLE IF EXISTS public.conversations CASCADE;
DROP TABLE IF EXISTS public.user_subscriptions CASCADE;

DROP TABLE IF EXISTS public.pdf_processing_jobs CASCADE;
DROP TABLE IF EXISTS public.chunk_embeddings CASCADE;
DROP TABLE IF EXISTS public.standard_table_rows CASCADE;
DROP TABLE IF EXISTS public.standard_table_columns CASCADE;
DROP TABLE IF EXISTS public.standard_tables CASCADE;
DROP TABLE IF EXISTS public.admin_processing_history CASCADE;
DROP TABLE IF EXISTS public.admin_queue CASCADE;
DROP TABLE IF EXISTS public.chunks CASCADE;
DROP TABLE IF EXISTS public.documents CASCADE;
DROP TABLE IF EXISTS public.profiles CASCADE;

DO $$
BEGIN
  RAISE NOTICE 'Schema reset complete. Run combined_setup.sql next.';
END $$;
