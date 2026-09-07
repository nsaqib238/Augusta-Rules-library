-- ===========================================
-- COMBINED SETUP (MASTER COPY — single source of truth)
-- Database schema for Augusta: upload, ingest, RAG search.
--
-- Run ONLY this file (+ optional full_schema_reset.sql for a clean wipe).
-- No separate patch SQL files — upgrades re-run this script (idempotent).
--
-- Includes: profiles, documents (codebook + metadata, is_shared_library for NCC/SIR),
--           chunks, chunk_embeddings, standard_tables, admin queue, PDF job queue, storage,
--           search_chunks_vector (incl. shared library + S0 auth hardening),
--           S2 hybrid search (search_vector, FTS/trgm/heading RPCs), conversations, billing/Stripe, passcodes.
--
-- Fresh deploy:  full_schema_reset.sql  →  combined_setup.sql
-- Existing DB:   combined_setup.sql only
-- ===========================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- ===========================================
-- 1. PROFILES
-- ===========================================

CREATE TABLE IF NOT EXISTS profiles (
    id UUID REFERENCES auth.users(id) ON DELETE CASCADE PRIMARY KEY,
    email TEXT UNIQUE,
    full_name TEXT,
    avatar_url TEXT,
    role TEXT DEFAULT 'user' CHECK (role IN ('user', 'admin', 'engineer', 'inspector')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own profile" ON profiles;
CREATE POLICY "Users can view own profile" ON profiles
    FOR SELECT USING (auth.uid() = id);

DROP POLICY IF EXISTS "Users can update own profile" ON profiles;
CREATE POLICY "Users can update own profile" ON profiles
    FOR UPDATE USING (auth.uid() = id);

DROP POLICY IF EXISTS "Users can insert own profile" ON profiles;
CREATE POLICY "Users can insert own profile" ON profiles
    FOR INSERT WITH CHECK (auth.uid() = id);

-- Auto-create profile on signup (SECURITY DEFINER — works before email confirm / session).
-- Columns like account_type are added in section 6; function is refreshed again there.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_full_name TEXT;
BEGIN
    v_full_name := COALESCE(
        NULLIF(TRIM(NEW.raw_user_meta_data->>'full_name'), ''),
        NULLIF(TRIM(NEW.raw_user_meta_data->>'name'), ''),
        ''
    );

    INSERT INTO public.profiles (
        id,
        email,
        full_name,
        role,
        account_type,
        subscription_status,
        documents_remaining,
        created_at,
        updated_at
    )
    VALUES (
        NEW.id,
        NEW.email,
        v_full_name,
        'user',
        'sole',
        'inactive',
        0,
        NOW(),
        NOW()
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = COALESCE(EXCLUDED.email, profiles.email),
        full_name = CASE
            WHEN profiles.full_name IS NULL OR TRIM(profiles.full_name) = '' THEN EXCLUDED.full_name
            ELSE profiles.full_name
        END,
        updated_at = NOW();

    RETURN NEW;
EXCEPTION
    WHEN unique_violation THEN
        -- Email unique clash or race: still ensure a row for this auth user id
        UPDATE public.profiles
        SET
            full_name = CASE
                WHEN full_name IS NULL OR TRIM(full_name) = '' THEN v_full_name
                ELSE full_name
            END,
            updated_at = NOW()
        WHERE id = NEW.id;

        IF NOT FOUND THEN
            INSERT INTO public.profiles (id, email, full_name, role, account_type, subscription_status, documents_remaining)
            VALUES (NEW.id, NULL, v_full_name, 'user', 'sole', 'inactive', 0)
            ON CONFLICT (id) DO NOTHING;
        END IF;
        RETURN NEW;
    WHEN undefined_column THEN
        -- Older DBs mid-migration without account_type yet
        INSERT INTO public.profiles (id, email, full_name)
        VALUES (NEW.id, NEW.email, v_full_name)
        ON CONFLICT (id) DO UPDATE
        SET email = COALESCE(EXCLUDED.email, profiles.email),
            updated_at = NOW();
        RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ===========================================
-- 2. DOCUMENTS
-- ===========================================

CREATE TABLE IF NOT EXISTS documents (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    original_filename TEXT,
    codebook TEXT DEFAULT 'AS3000',
    source TEXT,
    discipline TEXT DEFAULT 'electrical'
        CHECK (discipline IN ('electrical', 'mechanical', 'fire', 'hydraulics')),
    standard_family TEXT DEFAULT 'AS_NZS'
        CHECK (standard_family IN ('AS_NZS', 'ISO', 'IEC', 'ASTM', 'NFPA', 'API', 'IEEE')),
    processing_model TEXT DEFAULT 'admin'
        CHECK (processing_model IN ('spacy', 'bert', 'admin', 'modal_pdf_pipeline')),
    file_size INTEGER,
    storage_url TEXT,
    storage_bucket TEXT DEFAULT 'documents',
    storage_etag TEXT,
    file_hash TEXT,
    version_group_id UUID,
    version_label INTEGER DEFAULT 1 CHECK (version_label >= 1),
    status TEXT DEFAULT 'pending_admin_review'
        CHECK (status IN (
            'pending_admin_review',
            'admin_processing',
            'pdf_processing',
            'ready_for_search',
            'failed'
        )),
    chunk_count INTEGER DEFAULT 0,
    processing_time_seconds DECIMAL(5,2),
    admin_status TEXT DEFAULT 'pending'
        CHECK (admin_status IN ('pending', 'processing', 'completed', 'rejected')),
    admin_notes TEXT,
    admin_processed_at TIMESTAMPTZ,
    admin_user_id UUID REFERENCES auth.users(id),
    original_chunk_count INTEGER DEFAULT 0,
    refined_chunk_count INTEGER DEFAULT 0,
    is_shared_library BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON COLUMN documents.codebook IS 'Short code id (AS3000, NCC2022_VOL1, custom) for search routing';
COMMENT ON COLUMN documents.source IS 'Human-readable standard name for LLM / display';
COMMENT ON COLUMN documents.original_filename IS 'Uploaded PDF filename as provided by the user';

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own documents" ON documents;
CREATE POLICY "Users can view own documents" ON documents
    FOR ALL USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Admins can view all documents" ON documents;
CREATE POLICY "Admins can view all documents" ON documents
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_codebook ON documents(codebook);
CREATE INDEX IF NOT EXISTS idx_documents_discipline ON documents(discipline);
CREATE INDEX IF NOT EXISTS idx_documents_user_discipline ON documents(user_id, discipline);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at);
CREATE INDEX IF NOT EXISTS idx_documents_filename_user ON documents(user_id, filename);

-- ===========================================
-- 3. CHUNKS
-- ===========================================

CREATE TABLE IF NOT EXISTS chunks (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    heading TEXT,
    clause_number TEXT,
    page_number INTEGER,
    jurisdiction TEXT CHECK (jurisdiction IN ('AU', 'NZ', 'BOTH', NULL)),
    codebook TEXT,
    discipline TEXT CHECK (discipline IN ('electrical', 'mechanical', 'fire', 'hydraulics', NULL)),
    entities JSONB DEFAULT '[]',
    detailed_analysis JSONB DEFAULT '{}',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can only access their own chunks" ON chunks;
CREATE POLICY "Users can only access their own chunks" ON chunks
    FOR ALL USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Admins can view all chunks" ON chunks;
CREATE POLICY "Admins can view all chunks" ON chunks
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_user_id ON chunks(user_id);
CREATE INDEX IF NOT EXISTS idx_chunks_text ON chunks USING gin(to_tsvector('english', text));
CREATE INDEX IF NOT EXISTS idx_chunks_clause_number ON chunks(clause_number) WHERE clause_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chunks_codebook ON chunks(codebook) WHERE codebook IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chunks_document_clause ON chunks(document_id, clause_number) WHERE clause_number IS NOT NULL;

-- S2 scaling: weighted tsvector + hybrid search indexes (Postgres-native retrieval signals)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'chunks' AND column_name = 'search_vector'
    ) THEN
        ALTER TABLE chunks ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('english', coalesce(heading, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(clause_number, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(text, '')), 'C')
            ) STORED;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_chunks_search_vector ON chunks USING gin(search_vector);
CREATE INDEX IF NOT EXISTS idx_chunks_document_chunk ON chunks(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_chunks_trgm_heading ON chunks
    USING gin ((coalesce(heading, '') || ' ' || coalesce(clause_number, '')) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_trgm_text ON chunks
    USING gin (left(coalesce(text, ''), 2000) gin_trgm_ops);

-- ===========================================
-- 3b. CHUNK EMBEDDINGS (pgvector — semantic RAG search)
-- ===========================================

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    embedding vector(384),
    model TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(chunk_id, model)
);

CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_chunk_id ON chunk_embeddings(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_document_id ON chunk_embeddings(document_id);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_user_id ON chunk_embeddings(user_id);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_document_model ON chunk_embeddings(document_id, model);

-- IVFFlat index (no-op on empty table; builds as rows are inserted)
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_vector ON chunk_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

ALTER TABLE chunk_embeddings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view their own chunk embeddings" ON chunk_embeddings;
CREATE POLICY "Users can view their own chunk embeddings" ON chunk_embeddings
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Admins can manage chunk embeddings" ON chunk_embeddings;
CREATE POLICY "Admins can manage chunk embeddings" ON chunk_embeddings
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

-- S0 scaling: authenticated callers cannot pass another user's p_user_id (service_role unchanged).
CREATE OR REPLACE FUNCTION search_chunks_vector(
    p_query_embedding vector(384),
    p_user_id UUID DEFAULT NULL,
    p_document_id UUID DEFAULT NULL,
    p_limit INTEGER DEFAULT 10,
    p_codebook TEXT DEFAULT NULL,
    p_jurisdiction TEXT DEFAULT NULL
) RETURNS TABLE (
    chunk_id UUID,
    document_id UUID,
    similarity FLOAT,
    text TEXT,
    clause_number TEXT,
    heading TEXT,
    page_number INTEGER,
    codebook TEXT,
    jurisdiction TEXT
) AS $$
BEGIN
    IF auth.uid() IS NOT NULL
       AND p_user_id IS NOT NULL
       AND p_user_id IS DISTINCT FROM auth.uid() THEN
        RAISE EXCEPTION 'search_chunks_vector: p_user_id must match auth.uid()'
            USING ERRCODE = '42501';
    END IF;

    RETURN QUERY
    SELECT
        c.id AS chunk_id,
        c.document_id,
        (1 - (e.embedding <=> p_query_embedding))::FLOAT AS similarity,
        c.text,
        c.clause_number,
        c.heading,
        c.page_number,
        c.codebook,
        c.jurisdiction
    FROM chunk_embeddings e
    JOIN chunks c ON e.chunk_id = c.id
    JOIN documents d ON c.document_id = d.id
    WHERE d.status = 'ready_for_search'
      AND (
          p_user_id IS NULL
          OR e.user_id = p_user_id
          OR d.is_shared_library = true
      )
      AND (p_document_id IS NULL OR e.document_id = p_document_id)
      AND (p_codebook IS NULL OR c.codebook = p_codebook)
      AND (
          p_jurisdiction IS NULL
          OR c.jurisdiction IS NULL
          OR c.jurisdiction = p_jurisdiction
          OR c.jurisdiction = 'BOTH'
      )
    ORDER BY e.embedding <=> p_query_embedding
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

GRANT EXECUTE ON FUNCTION search_chunks_vector(vector(384), UUID, UUID, INTEGER, TEXT, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION search_chunks_vector(vector(384), UUID, UUID, INTEGER, TEXT, TEXT) TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON chunk_embeddings TO service_role;

-- ===========================================
-- 4. STANDARD TABLES
-- ===========================================

CREATE TABLE IF NOT EXISTS standard_tables (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE NOT NULL,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
    table_id TEXT NOT NULL,
    standard_name TEXT NOT NULL,
    table_number TEXT NOT NULL,
    title TEXT,
    source_clause_number TEXT,
    text TEXT NOT NULL,
    notes JSONB DEFAULT '[]',
    exceptions JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(document_id, table_number)
);

ALTER TABLE standard_tables ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own tables" ON standard_tables;
CREATE POLICY "Users can view own tables" ON standard_tables
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can insert own tables" ON standard_tables;
CREATE POLICY "Users can insert own tables" ON standard_tables
    FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can update own tables" ON standard_tables;
CREATE POLICY "Users can update own tables" ON standard_tables
    FOR UPDATE USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can delete own tables" ON standard_tables;
CREATE POLICY "Users can delete own tables" ON standard_tables
    FOR DELETE USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Admins can view all tables" ON standard_tables;
CREATE POLICY "Admins can view all tables" ON standard_tables
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

CREATE INDEX IF NOT EXISTS idx_standard_tables_document ON standard_tables(document_id);
CREATE INDEX IF NOT EXISTS idx_standard_tables_user ON standard_tables(user_id);
CREATE INDEX IF NOT EXISTS idx_standard_tables_standard ON standard_tables(standard_name);
CREATE INDEX IF NOT EXISTS idx_standard_tables_table_number ON standard_tables(table_number);
CREATE INDEX IF NOT EXISTS idx_standard_tables_text ON standard_tables USING gin(to_tsvector('english', text));

-- ===========================================
-- 5. ADMIN QUEUE & HISTORY
-- ===========================================

CREATE TABLE IF NOT EXISTS admin_queue (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    codebook TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_size INTEGER,
    storage_url TEXT,
    priority INTEGER DEFAULT 1 CHECK (priority BETWEEN 1 AND 5),
    admin_notes TEXT,
    assigned_admin_id UUID REFERENCES auth.users(id),
    status TEXT DEFAULT 'pending'
        CHECK (status IN ('pending', 'assigned', 'processing', 'completed', 'rejected')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS admin_processing_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    admin_user_id UUID REFERENCES auth.users(id),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id),
    codebook TEXT NOT NULL,
    filename TEXT NOT NULL,
    processing_time_minutes INTEGER,
    chunks_created INTEGER,
    quality_score DECIMAL(3,2),
    admin_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE admin_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_processing_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Admins can view admin queue" ON admin_queue;
CREATE POLICY "Admins can view admin queue" ON admin_queue
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

DROP POLICY IF EXISTS "Admins can update admin queue" ON admin_queue;
CREATE POLICY "Admins can update admin queue" ON admin_queue
    FOR UPDATE USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

DROP POLICY IF EXISTS "Admins can insert into admin queue" ON admin_queue;
CREATE POLICY "Admins can insert into admin queue" ON admin_queue
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

DROP POLICY IF EXISTS "Admins can view processing history" ON admin_processing_history;
CREATE POLICY "Admins can view processing history" ON admin_processing_history
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

CREATE INDEX IF NOT EXISTS idx_admin_queue_status ON admin_queue(status);
CREATE INDEX IF NOT EXISTS idx_admin_queue_document ON admin_queue(document_id);
CREATE INDEX IF NOT EXISTS idx_admin_queue_codebook ON admin_queue(codebook);

-- ===========================================
-- 6. PDF PROCESSING JOBS (background queue)
-- ===========================================

CREATE TABLE IF NOT EXISTS pdf_processing_jobs (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    upload_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    worker_id TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pdf_processing_jobs_status_created
    ON pdf_processing_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_pdf_processing_jobs_document
    ON pdf_processing_jobs (document_id);

CREATE UNIQUE INDEX IF NOT EXISTS pdf_processing_jobs_one_active_per_document
    ON pdf_processing_jobs (document_id)
    WHERE status IN ('queued', 'running');

ALTER TABLE pdf_processing_jobs ENABLE ROW LEVEL SECURITY;
-- Backend uses service role; no user-facing policies required.

-- ===========================================
-- 7. TRIGGERS & HELPERS
-- ===========================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_documents_updated_at ON documents;
CREATE TRIGGER trigger_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_chunks_updated_at ON chunks;
CREATE TRIGGER trigger_chunks_updated_at
    BEFORE UPDATE ON chunks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_admin_queue_updated_at ON admin_queue;
CREATE TRIGGER trigger_admin_queue_updated_at
    BEFORE UPDATE ON admin_queue
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE FUNCTION delete_document_chunks()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM chunks WHERE document_id = OLD.id;
    DELETE FROM standard_tables WHERE document_id = OLD.id;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_delete_document_chunks ON documents;
CREATE TRIGGER trigger_delete_document_chunks
    AFTER DELETE ON documents
    FOR EACH ROW EXECUTE FUNCTION delete_document_chunks();

-- Propagate documents.codebook → chunks.codebook
CREATE OR REPLACE FUNCTION chunks_fill_codebook_from_document()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    doc_codebook TEXT;
BEGIN
    IF NEW.document_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.codebook IS NOT NULL AND btrim(COALESCE(NEW.codebook, '')) <> '' THEN
        RETURN NEW;
    END IF;
    SELECT COALESCE(NULLIF(btrim(COALESCE(d.codebook, '')), ''), 'AS3000')
    INTO doc_codebook
    FROM documents d
    WHERE d.id = NEW.document_id;
    IF doc_codebook IS NOT NULL THEN
        NEW.codebook := doc_codebook;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trigger_chunks_fill_codebook ON chunks;
CREATE TRIGGER trigger_chunks_fill_codebook
    BEFORE INSERT OR UPDATE ON chunks
    FOR EACH ROW EXECUTE FUNCTION chunks_fill_codebook_from_document();

CREATE OR REPLACE FUNCTION documents_propagate_codebook_to_chunks()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.codebook IS DISTINCT FROM OLD.codebook THEN
        UPDATE chunks
        SET codebook = COALESCE(NULLIF(btrim(COALESCE(NEW.codebook, '')), ''), 'AS3000'),
            updated_at = NOW()
        WHERE document_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trigger_documents_propagate_codebook ON documents;
CREATE TRIGGER trigger_documents_propagate_codebook
    AFTER UPDATE OF codebook ON documents
    FOR EACH ROW EXECUTE FUNCTION documents_propagate_codebook_to_chunks();

CREATE OR REPLACE FUNCTION execute_sql(sql_text TEXT)
RETURNS TEXT AS $$
BEGIN
    EXECUTE sql_text;
    RETURN 'SQL executed successfully';
EXCEPTION
    WHEN OTHERS THEN
        RETURN 'Error: ' || SQLERRM;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE FUNCTION get_document_stats(user_uuid UUID)
RETURNS TABLE(
    total_documents BIGINT,
    pending_documents BIGINT,
    completed_documents BIGINT,
    total_chunks BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(d.id),
        COUNT(CASE WHEN d.status = 'pending_admin_review' THEN 1 END),
        COUNT(CASE WHEN d.status = 'ready_for_search' THEN 1 END),
        COALESCE(SUM(d.refined_chunk_count), 0)
    FROM documents d
    WHERE d.user_id = user_uuid;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION get_document_tables(p_document_id UUID)
RETURNS SETOF standard_tables AS $$
BEGIN
    RETURN QUERY
    SELECT * FROM standard_tables
    WHERE document_id = p_document_id
    ORDER BY table_number;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION safe_insert_document(
    p_user_id UUID,
    p_filename TEXT,
    p_codebook TEXT,
    p_file_size INTEGER,
    p_processing_model TEXT DEFAULT 'admin',
    p_analysis_depth TEXT DEFAULT 'basic'
)
RETURNS UUID AS $$
DECLARE
    new_id UUID;
BEGIN
    INSERT INTO documents (
        user_id, filename, original_filename, codebook, file_size, processing_model, status
    ) VALUES (
        p_user_id, p_filename, p_filename, p_codebook, p_file_size, p_processing_model, 'pending_admin_review'
    )
    RETURNING id INTO new_id;
    RETURN new_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- PDF job claim/finish (used by pdf_job_queue.py)
CREATE OR REPLACE FUNCTION public.try_claim_pdf_processing_job(
    p_job_id UUID,
    p_user_id UUID,
    p_max_global INT DEFAULT 1,
    p_max_per_user INT DEFAULT 1,
    p_worker_id TEXT DEFAULT NULL,
    p_stale_minutes INT DEFAULT 90
)
RETURNS TABLE (
    claimed BOOLEAN,
    queue_ahead INT,
    global_running INT,
    user_running INT,
    reason TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_status TEXT;
    v_global_running INT;
    v_user_running INT;
    v_queue_ahead INT;
BEGIN
    PERFORM pg_advisory_xact_lock(842001);

    UPDATE pdf_processing_jobs
    SET status = 'failed',
        error_message = 'Stale PDF job (no heartbeat). Re-upload the file.',
        finished_at = NOW(),
        updated_at = NOW()
    WHERE status = 'running'
      AND updated_at < NOW() - INTERVAL '15 minutes';

    SELECT j.status INTO v_status FROM pdf_processing_jobs j WHERE j.id = p_job_id;
    IF NOT FOUND THEN
        claimed := FALSE; queue_ahead := 0; global_running := 0; user_running := 0; reason := 'job_not_found';
        RETURN NEXT; RETURN;
    END IF;

    IF v_status <> 'queued' THEN
        claimed := FALSE; queue_ahead := 0; global_running := 0; user_running := 0; reason := v_status;
        RETURN NEXT; RETURN;
    END IF;

    SELECT count(*)::INT INTO v_global_running FROM pdf_processing_jobs WHERE status = 'running';
    SELECT count(*)::INT INTO v_user_running FROM pdf_processing_jobs WHERE status = 'running' AND user_id = p_user_id;
    SELECT count(*)::INT INTO v_queue_ahead
    FROM pdf_processing_jobs q
    JOIN pdf_processing_jobs me ON me.id = p_job_id
    WHERE q.status = 'queued' AND q.created_at < me.created_at;

    IF v_global_running >= GREATEST(p_max_global, 1)
       OR v_user_running >= GREATEST(p_max_per_user, 1) THEN
        claimed := FALSE; queue_ahead := v_queue_ahead;
        global_running := v_global_running; user_running := v_user_running; reason := 'slots_busy';
        RETURN NEXT; RETURN;
    END IF;

    UPDATE pdf_processing_jobs
    SET status = 'running', worker_id = p_worker_id, started_at = NOW(), updated_at = NOW()
    WHERE id = p_job_id AND status = 'queued';

    IF FOUND THEN
        claimed := TRUE; queue_ahead := v_queue_ahead;
        global_running := v_global_running + 1; user_running := v_user_running + 1; reason := 'claimed';
    ELSE
        claimed := FALSE; queue_ahead := v_queue_ahead;
        global_running := v_global_running; user_running := v_user_running; reason := 'race_lost';
    END IF;
    RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_pdf_processing_job(
    p_job_id UUID,
    p_success BOOLEAN,
    p_error_message TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    UPDATE pdf_processing_jobs
    SET
        status = CASE WHEN p_success THEN 'completed' ELSE 'failed' END,
        error_message = CASE WHEN p_success THEN NULL ELSE left(p_error_message, 2000) END,
        finished_at = NOW(),
        updated_at = NOW()
    WHERE id = p_job_id AND status IN ('running', 'queued');

    IF NOT p_success THEN
        UPDATE documents d
        SET status = 'failed',
            admin_notes = left(COALESCE(p_error_message, 'PDF processing failed. Re-upload.'), 1500)
        FROM pdf_processing_jobs j
        WHERE j.id = p_job_id AND j.document_id = d.id
          AND d.status IN ('pdf_processing', 'admin_processing', 'pending_admin_review');
    END IF;
END;
$$;

GRANT SELECT, INSERT, UPDATE ON pdf_processing_jobs TO service_role;
GRANT EXECUTE ON FUNCTION public.try_claim_pdf_processing_job TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_pdf_processing_job TO service_role;

-- ===========================================
-- 8. STORAGE
-- ===========================================

INSERT INTO storage.buckets (id, name, public)
VALUES ('documents', 'documents', false)
ON CONFLICT (id) DO NOTHING;

DROP POLICY IF EXISTS "Users can upload own files" ON storage.objects;
CREATE POLICY "Users can upload own files" ON storage.objects
    FOR INSERT WITH CHECK (
        bucket_id = 'documents' AND auth.uid()::text = (storage.foldername(name))[1]
    );

DROP POLICY IF EXISTS "Users can view own files" ON storage.objects;
CREATE POLICY "Users can view own files" ON storage.objects
    FOR SELECT USING (
        bucket_id = 'documents' AND auth.uid()::text = (storage.foldername(name))[1]
    );

DROP POLICY IF EXISTS "Users can delete own files" ON storage.objects;
CREATE POLICY "Users can delete own files" ON storage.objects
    FOR DELETE USING (
        bucket_id = 'documents' AND auth.uid()::text = (storage.foldername(name))[1]
    );

-- ===========================================
-- 9. IDEMPOTENT UPGRADES (existing DBs — safe to re-run)
-- Adds columns / constraints missing from older deployments.
-- Includes NCC/SIR shared library: is_shared_library, RLS, search_chunks_vector refresh.
-- ===========================================

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS original_filename TEXT,
    ADD COLUMN IF NOT EXISTS source TEXT,
    ADD COLUMN IF NOT EXISTS discipline TEXT DEFAULT 'electrical',
    ADD COLUMN IF NOT EXISTS storage_bucket TEXT DEFAULT 'documents',
    ADD COLUMN IF NOT EXISTS file_hash TEXT,
    ADD COLUMN IF NOT EXISTS version_group_id UUID,
    ADD COLUMN IF NOT EXISTS version_label INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS is_shared_library BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS standard_family TEXT DEFAULT 'AS_NZS';

COMMENT ON COLUMN documents.standard_family IS 'Publisher parser family for clause regex (AS_NZS, ISO, IEC, ASTM, NFPA, API, IEEE)';

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_standard_family_check;
ALTER TABLE documents ADD CONSTRAINT documents_standard_family_check
    CHECK (standard_family IN ('AS_NZS', 'ISO', 'IEC', 'ASTM', 'NFPA', 'API', 'IEEE'));

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS codebook TEXT,
    ADD COLUMN IF NOT EXISTS discipline TEXT,
    ADD COLUMN IF NOT EXISTS heading TEXT,
    ADD COLUMN IF NOT EXISTS jurisdiction TEXT;

UPDATE documents SET original_filename = filename
WHERE original_filename IS NULL AND filename IS NOT NULL;

UPDATE documents SET version_group_id = id WHERE version_group_id IS NULL;

UPDATE chunks c
SET codebook = COALESCE(NULLIF(btrim(d.codebook), ''), 'AS3000'), updated_at = NOW()
FROM documents d
WHERE c.document_id = d.id
  AND (c.codebook IS NULL OR btrim(c.codebook) = '');

COMMENT ON COLUMN documents.codebook IS 'Short code id (AS3000, NCC2022_VOL1, etc.) for search routing';
COMMENT ON COLUMN documents.source IS 'Human-readable standard name for LLM / display';
COMMENT ON COLUMN documents.original_filename IS 'Uploaded PDF filename as provided by the user';

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check
    CHECK (status IN (
        'pending_admin_review', 'admin_processing', 'pdf_processing', 'ready_for_search', 'failed'
    ));

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_processing_model_check;
ALTER TABLE documents ADD CONSTRAINT documents_processing_model_check
    CHECK (processing_model IN ('spacy', 'bert', 'admin', 'modal_pdf_pipeline'));

COMMENT ON COLUMN documents.is_shared_library IS 'Admin-uploaded NCC/SIR CSV library searchable by all users';

DROP POLICY IF EXISTS "Users can view shared library documents" ON documents;
CREATE POLICY "Users can view shared library documents" ON documents
    FOR SELECT USING (is_shared_library = true);

DROP POLICY IF EXISTS "Users can view shared library chunks" ON chunks;
CREATE POLICY "Users can view shared library chunks" ON chunks
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = chunks.document_id AND d.is_shared_library = true
        )
    );

DROP POLICY IF EXISTS "Users can view shared library embeddings" ON chunk_embeddings;
CREATE POLICY "Users can view shared library embeddings" ON chunk_embeddings
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = chunk_embeddings.document_id AND d.is_shared_library = true
        )
    );

CREATE INDEX IF NOT EXISTS idx_documents_shared_library ON documents(is_shared_library) WHERE is_shared_library = true;

-- Refresh vector RPC so existing DBs pick up shared-library visibility (after column exists)
-- S0 scaling: authenticated callers cannot pass another user's p_user_id (service_role unchanged).
CREATE OR REPLACE FUNCTION search_chunks_vector(
    p_query_embedding vector(384),
    p_user_id UUID DEFAULT NULL,
    p_document_id UUID DEFAULT NULL,
    p_limit INTEGER DEFAULT 10,
    p_codebook TEXT DEFAULT NULL,
    p_jurisdiction TEXT DEFAULT NULL
) RETURNS TABLE (
    chunk_id UUID,
    document_id UUID,
    similarity FLOAT,
    text TEXT,
    clause_number TEXT,
    heading TEXT,
    page_number INTEGER,
    codebook TEXT,
    jurisdiction TEXT
) AS $$
BEGIN
    IF auth.uid() IS NOT NULL
       AND p_user_id IS NOT NULL
       AND p_user_id IS DISTINCT FROM auth.uid() THEN
        RAISE EXCEPTION 'search_chunks_vector: p_user_id must match auth.uid()'
            USING ERRCODE = '42501';
    END IF;

    RETURN QUERY
    SELECT
        c.id AS chunk_id,
        c.document_id,
        (1 - (e.embedding <=> p_query_embedding))::FLOAT AS similarity,
        c.text,
        c.clause_number,
        c.heading,
        c.page_number,
        c.codebook,
        c.jurisdiction
    FROM chunk_embeddings e
    JOIN chunks c ON e.chunk_id = c.id
    JOIN documents d ON c.document_id = d.id
    WHERE d.status = 'ready_for_search'
      AND (
          p_user_id IS NULL
          OR e.user_id = p_user_id
          OR d.is_shared_library = true
      )
      AND (p_document_id IS NULL OR e.document_id = p_document_id)
      AND (p_codebook IS NULL OR c.codebook = p_codebook)
      AND (
          p_jurisdiction IS NULL
          OR c.jurisdiction IS NULL
          OR c.jurisdiction = p_jurisdiction
          OR c.jurisdiction = 'BOTH'
      )
    ORDER BY e.embedding <=> p_query_embedding
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

GRANT EXECUTE ON FUNCTION search_chunks_vector(vector(384), UUID, UUID, INTEGER, TEXT, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION search_chunks_vector(vector(384), UUID, UUID, INTEGER, TEXT, TEXT) TO service_role;

-- ===========================================
-- 3c. HYBRID SEARCH RPCs (Stage S2 — FTS, trgm, heading signals)
-- Requires documents.is_shared_library (section 9) for _hybrid_visible_chunks.
-- ===========================================

CREATE OR REPLACE FUNCTION _hybrid_visible_chunks(
    p_user_id UUID,
    p_document_ids UUID[],
    p_codebook TEXT DEFAULT NULL
) RETURNS SETOF UUID
LANGUAGE sql
STABLE
SET search_path = public
AS $$
    SELECT c.id
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE d.status = 'ready_for_search'
      AND (
          d.user_id = p_user_id
          OR COALESCE(d.is_shared_library, false) = true
      )
      AND (p_document_ids IS NULL OR c.document_id = ANY(p_document_ids))
      AND (p_codebook IS NULL OR c.codebook = p_codebook);
$$;

CREATE OR REPLACE FUNCTION search_chunks_fts(
    p_user_id UUID,
    p_document_ids UUID[],
    p_query TEXT,
    p_codebook TEXT DEFAULT NULL,
    p_limit INTEGER DEFAULT 40
) RETURNS TABLE (
    chunk_id UUID,
    rank REAL
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_query tsquery;
BEGIN
    IF p_user_id IS NULL OR p_document_ids IS NULL OR array_length(p_document_ids, 1) IS NULL THEN
        RETURN;
    END IF;
    IF COALESCE(trim(p_query), '') = '' THEN
        RETURN;
    END IF;

    v_query := plainto_tsquery('english', p_query);
    IF v_query IS NULL THEN
        RETURN;
    END IF;

    RETURN QUERY
    SELECT
        c.id AS chunk_id,
        ts_rank_cd(c.search_vector, v_query)::REAL AS rank
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.id IN (SELECT _hybrid_visible_chunks(p_user_id, p_document_ids, p_codebook))
      AND c.search_vector @@ v_query
    ORDER BY rank DESC
    LIMIT GREATEST(p_limit, 1);
END;
$$;

CREATE OR REPLACE FUNCTION search_chunks_trgm(
    p_user_id UUID,
    p_document_ids UUID[],
    p_term TEXT,
    p_codebook TEXT DEFAULT NULL,
    p_limit INTEGER DEFAULT 40
) RETURNS TABLE (
    chunk_id UUID,
    similarity REAL
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_user_id IS NULL OR p_document_ids IS NULL OR array_length(p_document_ids, 1) IS NULL THEN
        RETURN;
    END IF;
    IF COALESCE(trim(p_term), '') = '' THEN
        RETURN;
    END IF;

    RETURN QUERY
    SELECT
        c.id AS chunk_id,
        GREATEST(
            similarity(coalesce(c.heading, '') || ' ' || coalesce(c.clause_number, ''), p_term),
            similarity(left(coalesce(c.text, ''), 2000), p_term)
        )::REAL AS similarity
    FROM chunks c
    WHERE c.id IN (SELECT _hybrid_visible_chunks(p_user_id, p_document_ids, p_codebook))
      AND (
          (coalesce(c.heading, '') || ' ' || coalesce(c.clause_number, '')) % p_term
          OR left(coalesce(c.text, ''), 2000) % p_term
      )
    ORDER BY similarity DESC
    LIMIT GREATEST(p_limit, 1);
END;
$$;

CREATE OR REPLACE FUNCTION search_chunks_heading(
    p_user_id UUID,
    p_document_ids UUID[],
    p_needle TEXT,
    p_codebook TEXT DEFAULT NULL,
    p_limit INTEGER DEFAULT 40
) RETURNS TABLE (
    chunk_id UUID,
    score REAL
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_needle TEXT := lower(trim(coalesce(p_needle, '')));
BEGIN
    IF p_user_id IS NULL OR p_document_ids IS NULL OR array_length(p_document_ids, 1) IS NULL THEN
        RETURN;
    END IF;
    IF v_needle = '' THEN
        RETURN;
    END IF;

    RETURN QUERY
    SELECT
        c.id AS chunk_id,
        (
            CASE
                WHEN lower(coalesce(c.clause_number, '')) = v_needle THEN 100
                WHEN lower(coalesce(c.clause_number, '')) LIKE v_needle || '.%' THEN 95
                WHEN v_needle LIKE lower(coalesce(c.clause_number, '')) || '.%' THEN 90
                WHEN lower(coalesce(c.heading, '')) LIKE '%' || v_needle || '%' THEN 80
                WHEN lower(left(coalesce(c.text, ''), 200)) LIKE '%' || v_needle || '%' THEN 20
                ELSE 0
            END
        )::REAL AS score
    FROM chunks c
    WHERE c.id IN (SELECT _hybrid_visible_chunks(p_user_id, p_document_ids, p_codebook))
      AND (
          lower(coalesce(c.clause_number, '')) = v_needle
          OR lower(coalesce(c.clause_number, '')) LIKE v_needle || '.%'
          OR v_needle LIKE lower(coalesce(c.clause_number, '')) || '.%'
          OR lower(coalesce(c.heading, '')) LIKE '%' || v_needle || '%'
          OR lower(left(coalesce(c.text, ''), 200)) LIKE '%' || v_needle || '%'
      )
    ORDER BY score DESC
    LIMIT GREATEST(p_limit, 1);
END;
$$;

CREATE OR REPLACE FUNCTION count_visible_chunks(
    p_user_id UUID,
    p_document_ids UUID[],
    p_codebook TEXT DEFAULT NULL
) RETURNS INTEGER
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT count(*)::INTEGER
    FROM _hybrid_visible_chunks(p_user_id, p_document_ids, p_codebook);
$$;

GRANT EXECUTE ON FUNCTION search_chunks_fts(UUID, UUID[], TEXT, TEXT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION search_chunks_trgm(UUID, UUID[], TEXT, TEXT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION search_chunks_heading(UUID, UUID[], TEXT, TEXT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION count_visible_chunks(UUID, UUID[], TEXT) TO service_role;

-- ===========================================
-- 10. BACKUP-COMPAT (chat memory + admin subscriptions)
-- Ported from AUS-Augusta/supabase/combined_setup.sql — tables old scripts/UI expect.
-- Does NOT include full legacy RAG (clause_units, NCC, Stripe catalog, electrician_qa).
-- ===========================================

-- Chat sessions (backup QAPanel created rows here for continuity)
CREATE TABLE IF NOT EXISTS conversations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    codebook TEXT,
    title TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sources JSONB DEFAULT '[]',
    confidence TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_document_id ON conversations(document_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_id ON conversation_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_user_id ON conversation_messages(user_id);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_created_at ON conversation_messages(conversation_id, created_at);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own conversations" ON conversations;
CREATE POLICY "Users can view own conversations" ON conversations
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can insert own conversations" ON conversations;
CREATE POLICY "Users can insert own conversations" ON conversations
    FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can update own conversations" ON conversations;
CREATE POLICY "Users can update own conversations" ON conversations
    FOR UPDATE USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can delete own conversations" ON conversations;
CREATE POLICY "Users can delete own conversations" ON conversations
    FOR DELETE USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can view own conversation messages" ON conversation_messages;
CREATE POLICY "Users can view own conversation messages" ON conversation_messages
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can insert own conversation messages" ON conversation_messages;
CREATE POLICY "Users can insert own conversation messages" ON conversation_messages
    FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can update own conversation messages" ON conversation_messages;
CREATE POLICY "Users can update own conversation messages" ON conversation_messages
    FOR UPDATE USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can delete own conversation messages" ON conversation_messages;
CREATE POLICY "Users can delete own conversation messages" ON conversation_messages
    FOR DELETE USING (auth.uid() = user_id);

CREATE OR REPLACE FUNCTION update_conversation_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE conversations SET updated_at = NOW() WHERE id = NEW.conversation_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_conversation_on_message ON conversation_messages;
CREATE TRIGGER update_conversation_on_message
    AFTER INSERT ON conversation_messages
    FOR EACH ROW EXECUTE FUNCTION update_conversation_timestamp();

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS codebook TEXT;

-- Admin user list reads subscription plan names (empty table is OK; table must exist)
CREATE TABLE IF NOT EXISTS user_subscriptions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    subscription_type TEXT NOT NULL DEFAULT 'individual' CHECK (subscription_type = 'individual'),
    plan_name TEXT NOT NULL DEFAULT 'sole_trader_free',
    plan_price DECIMAL(10, 2) NOT NULL DEFAULT 0,
    currency TEXT DEFAULT 'AUD',
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    stripe_payment_method_id TEXT,
    status TEXT DEFAULT 'active'
        CHECK (status IN ('active', 'cancelled', 'suspended', 'past_due', 'trialing', 'incomplete', 'incomplete_expired', 'unpaid')),
    current_period_start TIMESTAMPTZ DEFAULT NOW(),
    current_period_end TIMESTAMPTZ DEFAULT NOW() + INTERVAL '1 year',
    cancel_at_period_end BOOLEAN DEFAULT false,
    cancelled_at TIMESTAMPTZ,
    trial_start TIMESTAMPTZ,
    trial_end TIMESTAMPTZ,
    max_documents INTEGER,
    max_questions INTEGER,
    documents_uploaded INTEGER DEFAULT 0,
    questions_asked INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user ON user_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_status ON user_subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_type ON user_subscriptions(subscription_type);

ALTER TABLE user_subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own subscriptions" ON user_subscriptions;
CREATE POLICY "Users can view own subscriptions" ON user_subscriptions
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Admins can manage all subscriptions" ON user_subscriptions;
CREATE POLICY "Admins can manage all subscriptions" ON user_subscriptions
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON user_subscriptions TO service_role;
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_stripe_customer ON user_subscriptions(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_stripe_subscription ON user_subscriptions(stripe_subscription_id);



-- Legacy helper from backup enhanced_master_setup (full_schema_reset drops this name)
CREATE OR REPLACE FUNCTION search_chunks_enhanced(
    p_user_id UUID,
    p_codebook TEXT DEFAULT NULL,
    p_jurisdiction TEXT DEFAULT NULL,
    p_clause_prefix TEXT DEFAULT NULL,
    p_limit INTEGER DEFAULT 10
)
RETURNS TABLE(
    id UUID,
    document_id UUID,
    text TEXT,
    heading TEXT,
    clause_number TEXT,
    jurisdiction TEXT,
    codebook TEXT,
    page_number INTEGER,
    entities JSONB,
    similarity_score REAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.document_id,
        c.text,
        c.heading,
        c.clause_number,
        c.jurisdiction,
        c.codebook,
        c.page_number,
        c.entities,
        1.0::REAL AS similarity_score
    FROM chunks c
    JOIN documents d ON c.document_id = d.id
    WHERE c.user_id = p_user_id
      AND d.status = 'ready_for_search'
      AND (p_codebook IS NULL OR c.codebook = p_codebook)
      AND (p_jurisdiction IS NULL OR c.jurisdiction IS NULL OR c.jurisdiction = p_jurisdiction OR c.jurisdiction = 'BOTH')
      AND (p_clause_prefix IS NULL OR c.clause_number LIKE p_clause_prefix || '%')
    ORDER BY c.chunk_index
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

GRANT EXECUTE ON FUNCTION search_chunks_enhanced(UUID, TEXT, TEXT, TEXT, INTEGER) TO authenticated;
GRANT EXECUTE ON FUNCTION search_chunks_enhanced(UUID, TEXT, TEXT, TEXT, INTEGER) TO service_role;

-- Shared library edition registry (admin creates new SIR/NCC editions without code deploy)
CREATE TABLE IF NOT EXISTS shared_library_editions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family TEXT NOT NULL CHECK (family IN ('SIR', 'NCC')),
    codebook TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    discipline TEXT NOT NULL DEFAULT 'electrical',
    edition_year INTEGER,
    volume TEXT,
    part TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID REFERENCES auth.users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_shared_library_editions_family ON shared_library_editions(family);

ALTER TABLE shared_library_editions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can read shared library editions" ON shared_library_editions;
CREATE POLICY "Authenticated users can read shared library editions" ON shared_library_editions
    FOR SELECT TO authenticated USING (true);

COMMENT ON TABLE shared_library_editions IS 'Admin-defined SIR/NCC editions; CSV upload targets codebook id';

INSERT INTO shared_library_editions (family, codebook, label, discipline, edition_year, volume, part)
VALUES
    ('SIR', 'NSW_SIR_2018', 'NSW SIR 2018', 'electrical', 2018, NULL, NULL),
    ('SIR', 'SA_SIR_2025', 'South Australia SIR 2025', 'electrical', 2025, NULL, NULL),
    ('SIR', 'TASNETWORK_SIR_V85', 'TasNetwork SIR V8-5', 'electrical', NULL, 'V8-5', NULL),
    ('SIR', 'VIC_SIR_2025', 'Victorian SIR 2025', 'electrical', 2025, NULL, NULL),
    ('NCC', 'NCC2022_VOL1', 'NCC 2022 Vol 1 — Class 2–9', 'fire', 2022, 'Vol1', 'All'),
    ('NCC', 'NCC2022_VOL2', 'NCC 2022 Vol 2 — Class 1 & 10', 'fire', 2022, 'Vol2', 'All'),
    ('NCC', 'NCC2022_VOL3', 'NCC 2022 Vol 3 — Plumbing Code', 'hydraulics', 2022, 'Vol3', 'All')
ON CONFLICT (codebook) DO NOTHING;

-- ===========================================
-- 10b. LIBRARY CATALOG (country → type → document)
-- Idempotent — safe to re-run. Does not wipe existing rows.
-- ===========================================

CREATE TABLE IF NOT EXISTS library_countries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    sort_order INTEGER NOT NULL DEFAULT 10,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS library_document_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id UUID NOT NULL REFERENCES library_countries(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 10,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (country_id, slug)
);

CREATE TABLE IF NOT EXISTS library_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id UUID NOT NULL REFERENCES library_countries(id) ON DELETE CASCADE,
    document_type_id UUID NOT NULL REFERENCES library_document_types(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    discipline TEXT NOT NULL DEFAULT 'Electrical',
    publisher TEXT,
    priority TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('critical', 'high', 'medium')),
    sort_order INTEGER NOT NULL DEFAULT 10,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    UNIQUE (country_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_library_document_types_country ON library_document_types(country_id);
CREATE INDEX IF NOT EXISTS idx_library_documents_country ON library_documents(country_id);
CREATE INDEX IF NOT EXISTS idx_library_documents_type ON library_documents(document_type_id);

ALTER TABLE shared_library_editions
    ADD COLUMN IF NOT EXISTS library_document_id UUID REFERENCES library_documents(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_shared_library_editions_library_doc
    ON shared_library_editions(library_document_id);

ALTER TABLE shared_library_editions DROP CONSTRAINT IF EXISTS shared_library_editions_family_check;
ALTER TABLE shared_library_editions
    ADD CONSTRAINT shared_library_editions_family_check CHECK (family IN ('SIR', 'NCC', 'LIB'));

ALTER TABLE library_countries ENABLE ROW LEVEL SECURITY;
ALTER TABLE library_document_types ENABLE ROW LEVEL SECURITY;
ALTER TABLE library_documents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can read library countries" ON library_countries;
CREATE POLICY "Authenticated users can read library countries"
    ON library_countries FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "Authenticated users can read library document types" ON library_document_types;
CREATE POLICY "Authenticated users can read library document types"
    ON library_document_types FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "Authenticated users can read library documents" ON library_documents;
CREATE POLICY "Authenticated users can read library documents"
    ON library_documents FOR SELECT TO authenticated USING (true);

GRANT SELECT ON library_countries TO authenticated;
GRANT SELECT ON library_document_types TO authenticated;
GRANT SELECT ON library_documents TO authenticated;

INSERT INTO library_countries (code, name, is_active, sort_order)
VALUES ('AU', 'Australia', true, 10)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

INSERT INTO library_document_types (country_id, slug, name, sort_order)
SELECT c.id, v.slug, v.name, v.sort_order
FROM library_countries c
CROSS JOIN (VALUES
    ('legislation', 'Legislation', 10),
    ('regulatory-instruments', 'Regulatory instruments', 20),
    ('network-rules', 'Network rules', 30),
    ('authority-requirements', 'Authority requirements', 40),
    ('technical-specifications', 'Technical specifications', 50),
    ('guidance', 'Guidance', 60)
) AS v(slug, name, sort_order)
WHERE c.code = 'AU'
ON CONFLICT (country_id, slug) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

INSERT INTO library_documents (country_id, document_type_id, slug, title, discipline, publisher, priority, sort_order)
SELECT c.id, t.id, v.slug, v.title, v.discipline, v.publisher, v.priority, v.sort_order
FROM library_countries c
JOIN library_document_types t ON t.country_id = c.id
JOIN (VALUES
    ('regulatory-instruments', 'ncc-volume-one', 'NCC Volume One', 'Building', 'ABCB', 'critical', 10),
    ('regulatory-instruments', 'ncc-volume-two', 'NCC Volume Two', 'Residential', 'ABCB', 'critical', 20),
    ('regulatory-instruments', 'ncc-volume-three', 'NCC Volume Three – Plumbing Code', 'Hydraulic', 'ABCB', 'critical', 30),
    ('network-rules', 'nsw-sir', 'NSW Service & Installation Rules', 'Electrical', 'NSW Govt', 'critical', 10),
    ('network-rules', 'sa-sir', 'SA Power Networks Service & Installation Rules', 'Electrical', 'SAPN', 'critical', 20),
    ('network-rules', 'tasnetworks-sir', 'TasNetworks connection / service requirements', 'Electrical', 'TasNetworks', 'high', 30),
    ('network-rules', 'vic-sir', 'Victorian Service & Installation Rules', 'Electrical', 'Victorian distributors', 'critical', 40)
) AS v(type_slug, slug, title, discipline, publisher, priority, sort_order)
    ON t.slug = v.type_slug
WHERE c.code = 'AU'
ON CONFLICT (country_id, slug) DO UPDATE SET
    title = EXCLUDED.title,
    document_type_id = EXCLUDED.document_type_id,
    discipline = EXCLUDED.discipline,
    publisher = EXCLUDED.publisher,
    priority = EXCLUDED.priority,
    sort_order = EXCLUDED.sort_order;

UPDATE shared_library_editions e
SET library_document_id = d.id
FROM library_documents d
JOIN library_countries c ON c.id = d.country_id
WHERE c.code = 'AU'
  AND e.library_document_id IS NULL
  AND (
    (e.codebook = 'NCC2022_VOL1' AND d.slug = 'ncc-volume-one')
    OR (e.codebook = 'NCC2022_VOL2' AND d.slug = 'ncc-volume-two')
    OR (e.codebook = 'NCC2022_VOL3' AND d.slug = 'ncc-volume-three')
    OR (e.codebook = 'NSW_SIR_2018' AND d.slug = 'nsw-sir')
    OR (e.codebook = 'SA_SIR_2025' AND d.slug = 'sa-sir')
    OR (e.codebook = 'TASNETWORK_SIR_V85' AND d.slug = 'tasnetworks-sir')
    OR (e.codebook = 'VIC_SIR_2025' AND d.slug = 'vic-sir')
  );

-- ===========================================
-- 11. SUBSCRIPTION / BILLING (Stripe, passcodes, views)
-- Plans: sole (free, NCC+SIR) | professional (paid, all features)
-- Idempotent — safe to re-run with the rest of this file
-- ===========================================

-- ===========================================
-- 2. ACCESS CODES TABLE
-- ===========================================

CREATE TABLE IF NOT EXISTS access_codes (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    
    -- Access details
    access_type TEXT DEFAULT 'trial' CHECK (access_type IN ('trial', 'promotional', 'educational', 'partner')),
    duration_days INTEGER DEFAULT 7,
    max_questions INTEGER DEFAULT 20,
    max_documents INTEGER DEFAULT 1,
    
    -- Usage limits
    max_uses INTEGER DEFAULT 1,
    times_used INTEGER DEFAULT 0,
    
    -- Valid dates
    valid_from TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    valid_until TIMESTAMP WITH TIME ZONE,
    
    -- Status
    is_active BOOLEAN DEFAULT true,
    
    -- Admin tracking
    created_by UUID REFERENCES auth.users(id),
    description TEXT,
    admin_notes TEXT,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_access_codes_code ON access_codes(code);
CREATE INDEX IF NOT EXISTS idx_access_codes_active ON access_codes(is_active);
CREATE INDEX IF NOT EXISTS idx_access_codes_type ON access_codes(access_type);
CREATE INDEX IF NOT EXISTS idx_access_codes_valid_until ON access_codes(valid_until);

-- ===========================================
-- 3. ACCESS CODE REDEMPTIONS TABLE
-- ===========================================

CREATE TABLE IF NOT EXISTS access_code_redemptions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    access_code_id UUID NOT NULL REFERENCES access_codes(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Redemption details
    redeemed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    access_granted_until TIMESTAMP WITH TIME ZONE NOT NULL,
    
    -- Usage tracking
    questions_used INTEGER DEFAULT 0,
    documents_used INTEGER DEFAULT 0,
    
    -- Status
    is_active BOOLEAN DEFAULT true,
    expired BOOLEAN DEFAULT false,
    
    UNIQUE(access_code_id, user_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_redemptions_access_code ON access_code_redemptions(access_code_id);
CREATE INDEX IF NOT EXISTS idx_redemptions_user ON access_code_redemptions(user_id);
CREATE INDEX IF NOT EXISTS idx_redemptions_active ON access_code_redemptions(is_active);
CREATE INDEX IF NOT EXISTS idx_redemptions_expires ON access_code_redemptions(access_granted_until);

-- ===========================================
-- 3b. PROMO PASSCODES (professional trial by passcode)
-- ===========================================
CREATE TABLE IF NOT EXISTS promo_passcodes (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    duration_months INTEGER NOT NULL CHECK (duration_months IN (3, 6)),
    max_redemptions INTEGER NOT NULL DEFAULT 1,
    redemptions_used INTEGER NOT NULL DEFAULT 0,
    valid_from TIMESTAMP WITH TIME ZONE,
    valid_until TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_by UUID REFERENCES auth.users(id),
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_promo_passcodes_code ON promo_passcodes(code);
CREATE INDEX IF NOT EXISTS idx_promo_passcodes_is_active ON promo_passcodes(is_active);
CREATE INDEX IF NOT EXISTS idx_promo_passcodes_valid_until ON promo_passcodes(valid_until);

-- ===========================================
-- 3c. PASSCODE REDEMPTIONS
-- ===========================================
CREATE TABLE IF NOT EXISTS passcode_redemptions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    passcode_id UUID NOT NULL REFERENCES promo_passcodes(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    redeemed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    access_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    UNIQUE(passcode_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_passcode_redemptions_passcode ON passcode_redemptions(passcode_id);
CREATE INDEX IF NOT EXISTS idx_passcode_redemptions_user ON passcode_redemptions(user_id);
CREATE INDEX IF NOT EXISTS idx_passcode_redemptions_expires ON passcode_redemptions(access_expires_at);

-- Trigger: increment promo_passcodes.redemptions_used on insert
CREATE OR REPLACE FUNCTION update_promo_passcode_usage()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE promo_passcodes SET redemptions_used = redemptions_used + 1 WHERE id = NEW.passcode_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trigger_passcode_redemption_usage ON passcode_redemptions;
CREATE TRIGGER trigger_passcode_redemption_usage
    AFTER INSERT ON passcode_redemptions
    FOR EACH ROW EXECUTE FUNCTION update_promo_passcode_usage();

-- Redeem promo passcode (SECURITY DEFINER — no table enumeration; row lock prevents races)
CREATE OR REPLACE FUNCTION public.redeem_promo_passcode(
    p_code TEXT,
    p_user_id UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_user_id UUID;
    v_jwt_role TEXT;
    v_auth_role TEXT;
    v_row promo_passcodes%ROWTYPE;
    v_now TIMESTAMPTZ := NOW();
    v_access_expires_at TIMESTAMPTZ;
    v_existing UUID;
BEGIN
    p_code := NULLIF(TRIM(p_code), '');
    IF p_code IS NULL THEN
        RAISE EXCEPTION 'Passcode is required.';
    END IF;

    -- Backend uses service_role; role claim may be on jwt.claim.role or auth.role(), or omitted
    v_jwt_role := COALESCE(current_setting('request.jwt.claim.role', true), '');
    BEGIN
        v_auth_role := COALESCE(auth.role(), '');
    EXCEPTION WHEN OTHERS THEN
        v_auth_role := '';
    END;

    IF (v_jwt_role = 'service_role' OR v_auth_role = 'service_role') AND p_user_id IS NOT NULL THEN
        v_user_id := p_user_id;
    ELSIF auth.uid() IS NOT NULL THEN
        v_user_id := auth.uid();
        IF p_user_id IS NOT NULL AND p_user_id IS DISTINCT FROM v_user_id THEN
            RAISE EXCEPTION 'Access denied';
        END IF;
    ELSIF p_user_id IS NOT NULL AND v_jwt_role = '' AND v_auth_role = '' THEN
        -- PostgREST service-role calls sometimes omit role claims; allow explicit p_user_id
        v_user_id := p_user_id;
    ELSE
        RAISE EXCEPTION 'Not authenticated';
    END IF;

    SELECT * INTO v_row
    FROM promo_passcodes
    WHERE upper(code) = upper(p_code)
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Invalid or expired passcode.';
    END IF;

    IF NOT v_row.is_active THEN
        RAISE EXCEPTION 'Invalid or expired passcode.';
    END IF;

    IF v_row.redemptions_used >= v_row.max_redemptions THEN
        RAISE EXCEPTION 'This passcode has already been used.';
    END IF;

    IF v_row.valid_from IS NOT NULL AND v_now < v_row.valid_from THEN
        RAISE EXCEPTION 'This passcode is not yet valid.';
    END IF;

    IF v_row.valid_until IS NOT NULL AND v_now > v_row.valid_until THEN
        RAISE EXCEPTION 'Invalid or expired passcode.';
    END IF;

    SELECT id INTO v_existing
    FROM passcode_redemptions
    WHERE passcode_id = v_row.id AND user_id = v_user_id
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION 'You have already used this passcode.';
    END IF;

    v_access_expires_at := v_now + make_interval(months => v_row.duration_months);

    INSERT INTO passcode_redemptions (passcode_id, user_id, redeemed_at, access_expires_at)
    VALUES (v_row.id, v_user_id, v_now, v_access_expires_at);

    UPDATE profiles SET
        account_type = 'professional',
        subscription_status = 'active',
        access_expires_at = v_access_expires_at,
        subscription_source = 'passcode',
        documents_remaining = NULL,
        company_id = NULL,
        updated_at = v_now
    WHERE id = v_user_id;

    RETURN jsonb_build_object(
        'success', true,
        'access_expires_at', v_access_expires_at,
        'message', 'You now have professional access until ' || to_char(v_access_expires_at, 'YYYY-MM-DD') || '.'
    );
END;
$$;

-- Redeem access code (SECURITY DEFINER — validates single code only)
CREATE OR REPLACE FUNCTION public.redeem_access_code(
    p_code TEXT,
    p_user_id UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_user_id UUID;
    v_jwt_role TEXT;
    v_auth_role TEXT;
    v_row access_codes%ROWTYPE;
    v_now TIMESTAMPTZ := NOW();
    v_access_granted_until TIMESTAMPTZ;
    v_existing UUID;
    v_redemption_id UUID;
BEGIN
    p_code := NULLIF(TRIM(p_code), '');
    IF p_code IS NULL THEN
        RAISE EXCEPTION 'Code is required.';
    END IF;

    -- Backend uses service_role; role claim may be on jwt.claim.role or auth.role(), or omitted
    v_jwt_role := COALESCE(current_setting('request.jwt.claim.role', true), '');
    BEGIN
        v_auth_role := COALESCE(auth.role(), '');
    EXCEPTION WHEN OTHERS THEN
        v_auth_role := '';
    END;

    IF (v_jwt_role = 'service_role' OR v_auth_role = 'service_role') AND p_user_id IS NOT NULL THEN
        v_user_id := p_user_id;
    ELSIF auth.uid() IS NOT NULL THEN
        v_user_id := auth.uid();
        IF p_user_id IS NOT NULL AND p_user_id IS DISTINCT FROM v_user_id THEN
            RAISE EXCEPTION 'Access denied';
        END IF;
    ELSIF p_user_id IS NOT NULL AND v_jwt_role = '' AND v_auth_role = '' THEN
        v_user_id := p_user_id;
    ELSE
        RAISE EXCEPTION 'Not authenticated';
    END IF;

    SELECT * INTO v_row
    FROM access_codes
    WHERE code = p_code
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Code not found';
    END IF;

    IF NOT v_row.is_active THEN
        RAISE EXCEPTION 'Code is inactive';
    END IF;

    IF v_row.valid_until IS NOT NULL AND v_now > v_row.valid_until THEN
        RAISE EXCEPTION 'Code has expired';
    END IF;

    IF v_row.times_used >= v_row.max_uses THEN
        RAISE EXCEPTION 'Code has reached maximum uses';
    END IF;

    SELECT id INTO v_existing
    FROM access_code_redemptions
    WHERE access_code_id = v_row.id AND user_id = v_user_id
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION 'You have already redeemed this code';
    END IF;

    v_access_granted_until := v_now + make_interval(days => v_row.duration_days);

    INSERT INTO access_code_redemptions (
        access_code_id, user_id, redeemed_at, access_granted_until,
        questions_used, documents_used, is_active, expired
    )
    VALUES (
        v_row.id, v_user_id, v_now, v_access_granted_until,
        0, 0, true, false
    )
    RETURNING id INTO v_redemption_id;

    UPDATE profiles SET
        account_type = 'professional',
        subscription_status = 'active',
        access_expires_at = v_access_granted_until,
        questions_remaining = v_row.max_questions,
        documents_remaining = v_row.max_documents,
        updated_at = v_now
    WHERE id = v_user_id;

    RETURN jsonb_build_object(
        'success', true,
        'message', 'Access granted for ' || v_row.duration_days || ' days',
        'redemption_id', v_redemption_id,
        'access_expires_at', v_access_granted_until,
        'max_questions', v_row.max_questions,
        'max_documents', v_row.max_documents
    );
END;
$$;

-- ===========================================
-- 3d. USER SESSIONS (concurrent login limit)
-- ===========================================
-- Tracks active sessions per user for enforcing max concurrent logins (e.g. 5).
-- Backend upserts (user_id, session_key) with last_seen_at; prunes stale rows; counts and rejects if > limit.
CREATE TABLE IF NOT EXISTS user_sessions (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    session_key TEXT NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, session_key)
);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_last_seen ON user_sessions(last_seen_at);
-- No RLS policies: only backend (service role) reads/writes this table.

-- ===========================================
-- 4. PAYMENT HISTORY TABLE
-- ===========================================

CREATE TABLE IF NOT EXISTS payment_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    subscription_id UUID REFERENCES user_subscriptions(id) ON DELETE SET NULL,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE SET NULL,
    
    -- Payment details
    stripe_payment_intent_id TEXT UNIQUE,
    stripe_invoice_id TEXT,
    amount DECIMAL(10, 2) NOT NULL,
    currency TEXT DEFAULT 'AUD',
    
    -- Status
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'pending', 'failed', 'refunded')),
    
    -- Details
    description TEXT,
    receipt_url TEXT,
    
    -- Timestamps
    payment_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_payment_history_subscription ON payment_history(subscription_id);
CREATE INDEX IF NOT EXISTS idx_payment_history_user ON payment_history(user_id);
CREATE INDEX IF NOT EXISTS idx_payment_history_stripe_intent ON payment_history(stripe_payment_intent_id);
CREATE INDEX IF NOT EXISTS idx_payment_history_status ON payment_history(status);
CREATE INDEX IF NOT EXISTS idx_payment_history_date ON payment_history(payment_date);

-- ===========================================
-- 5. SUBSCRIPTION EVENTS TABLE
-- ===========================================

CREATE TABLE IF NOT EXISTS subscription_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    subscription_id UUID REFERENCES user_subscriptions(id) ON DELETE CASCADE,
    
    -- Event details
    event_type TEXT NOT NULL,  -- 'created', 'updated', 'cancelled', 'suspended', 'reactivated', 'payment_failed', etc.
    event_data JSONB DEFAULT '{}',
    
    -- Source
    triggered_by TEXT DEFAULT 'system',  -- 'user', 'admin', 'stripe_webhook', 'system'
    triggered_by_user_id UUID REFERENCES auth.users(id),
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_subscription_events_subscription ON subscription_events(subscription_id);
CREATE INDEX IF NOT EXISTS idx_subscription_events_type ON subscription_events(event_type);
CREATE INDEX IF NOT EXISTS idx_subscription_events_created ON subscription_events(created_at);

-- ===========================================
-- 6. MODIFY EXISTING PROFILES TABLE (TWO PLANS ONLY)
-- ===========================================
-- Plans: sole (NCC + SIR only) | professional (all features).
-- Single source: account_type, subscription_status, access_expires_at; no separate subscription logic.
DO $$ 
BEGIN
    -- Add account_type column (sole | professional only)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='profiles' AND column_name='account_type') THEN
        ALTER TABLE profiles ADD COLUMN account_type TEXT DEFAULT 'sole' 
            CHECK (account_type IN ('sole', 'professional'));
        RAISE NOTICE 'Added account_type column to profiles table';
    END IF;
    
    -- Add subscription_status column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='profiles' AND column_name='subscription_status') THEN
        ALTER TABLE profiles ADD COLUMN subscription_status TEXT DEFAULT 'inactive' 
            CHECK (subscription_status IN ('active', 'inactive', 'suspended', 'expired'));
        RAISE NOTICE 'Added subscription_status column to profiles table';
    END IF;
    
    -- Add access_expires_at column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='profiles' AND column_name='access_expires_at') THEN
        ALTER TABLE profiles ADD COLUMN access_expires_at TIMESTAMP WITH TIME ZONE;
        RAISE NOTICE 'Added access_expires_at column to profiles table';
    END IF;
    
    -- Add documents_remaining (sole = 0, professional = unlimited via NULL)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='profiles' AND column_name='documents_remaining') THEN
        ALTER TABLE profiles ADD COLUMN documents_remaining INTEGER;
        RAISE NOTICE 'Added documents_remaining column to profiles table';
    END IF;

    -- Add questions_remaining (optional quota; NULL = unlimited for sole/professional defaults)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='profiles' AND column_name='questions_remaining') THEN
        ALTER TABLE profiles ADD COLUMN questions_remaining INTEGER;
        RAISE NOTICE 'Added questions_remaining column to profiles table';
    END IF;
    
    -- Add stripe_subscription_id for cancel/portal (optional)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='profiles' AND column_name='stripe_subscription_id') THEN
        ALTER TABLE profiles ADD COLUMN stripe_subscription_id TEXT;
        RAISE NOTICE 'Added stripe_subscription_id column to profiles table';
    END IF;

    -- Add subscription_source (stripe | passcode) for passcode trial → sole fallback
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='profiles' AND column_name='subscription_source') THEN
        ALTER TABLE profiles ADD COLUMN subscription_source TEXT
            CHECK (subscription_source IS NULL OR subscription_source IN ('stripe', 'passcode'));
        RAISE NOTICE 'Added subscription_source column to profiles table';
    END IF;
END $$;

-- Migrate legacy account_type to two-plan model (sole | professional)
UPDATE profiles SET account_type = 'sole' WHERE account_type IS NULL OR account_type IN ('free', 'trial', 'individual');
-- Enforce two-plan check (drop old constraint if exists, add new)
ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_account_type_check;
ALTER TABLE profiles ADD CONSTRAINT profiles_account_type_check CHECK (account_type IN ('sole', 'professional'));

-- Add indexes
CREATE INDEX IF NOT EXISTS idx_profiles_account_type ON profiles(account_type);
CREATE INDEX IF NOT EXISTS idx_profiles_subscription_status ON profiles(subscription_status);
CREATE INDEX IF NOT EXISTS idx_profiles_access_expires ON profiles(access_expires_at);

-- Sole: no AS document uploads (NCC + SIR only)
UPDATE profiles SET documents_remaining = 0 WHERE account_type = 'sole' AND (documents_remaining IS NULL OR documents_remaining != 0);

-- Refresh signup trigger after profile billing columns exist
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_full_name TEXT;
BEGIN
    v_full_name := COALESCE(
        NULLIF(TRIM(NEW.raw_user_meta_data->>'full_name'), ''),
        NULLIF(TRIM(NEW.raw_user_meta_data->>'name'), ''),
        ''
    );

    INSERT INTO public.profiles (
        id,
        email,
        full_name,
        role,
        account_type,
        subscription_status,
        documents_remaining,
        created_at,
        updated_at
    )
    VALUES (
        NEW.id,
        NEW.email,
        v_full_name,
        'user',
        'sole',
        'inactive',
        0,
        NOW(),
        NOW()
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = COALESCE(EXCLUDED.email, profiles.email),
        full_name = CASE
            WHEN profiles.full_name IS NULL OR TRIM(profiles.full_name) = '' THEN EXCLUDED.full_name
            ELSE profiles.full_name
        END,
        updated_at = NOW();

    RETURN NEW;
EXCEPTION
    WHEN unique_violation THEN
        UPDATE public.profiles
        SET
            full_name = CASE
                WHEN full_name IS NULL OR TRIM(full_name) = '' THEN v_full_name
                ELSE full_name
            END,
            updated_at = NOW()
        WHERE id = NEW.id;

        IF NOT FOUND THEN
            INSERT INTO public.profiles (id, email, full_name, role, account_type, subscription_status, documents_remaining)
            VALUES (NEW.id, NULL, v_full_name, 'user', 'sole', 'inactive', 0)
            ON CONFLICT (id) DO NOTHING;
        END IF;
        RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Repair: auth users that never got a profiles row (failed client insert / old trigger)
DO $$
DECLARE
    r RECORD;
    v_name TEXT;
BEGIN
    FOR r IN
        SELECT u.id, u.email, u.raw_user_meta_data
        FROM auth.users u
        LEFT JOIN public.profiles p ON p.id = u.id
        WHERE p.id IS NULL
    LOOP
        v_name := COALESCE(
            NULLIF(TRIM(r.raw_user_meta_data->>'full_name'), ''),
            NULLIF(TRIM(r.raw_user_meta_data->>'name'), ''),
            ''
        );
        BEGIN
            INSERT INTO public.profiles (id, email, full_name, role, account_type, subscription_status, documents_remaining)
            VALUES (r.id, r.email, v_name, 'user', 'sole', 'inactive', 0);
        EXCEPTION
            WHEN unique_violation THEN
                INSERT INTO public.profiles (id, email, full_name, role, account_type, subscription_status, documents_remaining)
                VALUES (r.id, NULL, v_name, 'user', 'sole', 'inactive', 0)
                ON CONFLICT (id) DO NOTHING;
        END;
    END LOOP;
END $$;

-- Block authenticated users from self-escalating via profile UPDATE (service_role / SQL editor exempt)
CREATE OR REPLACE FUNCTION public.protect_profile_sensitive_columns()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    v_jwt_role TEXT;
BEGIN
    v_jwt_role := COALESCE(current_setting('request.jwt.claim.role', true), '');

    IF v_jwt_role IN ('', 'service_role') THEN
        RETURN NEW;
    END IF;

    IF NEW.role IS DISTINCT FROM OLD.role THEN
        RAISE EXCEPTION 'Updating role is not allowed';
    END IF;
    IF NEW.account_type IS DISTINCT FROM OLD.account_type THEN
        RAISE EXCEPTION 'Updating account_type is not allowed';
    END IF;
    IF NEW.subscription_status IS DISTINCT FROM OLD.subscription_status THEN
        RAISE EXCEPTION 'Updating subscription_status is not allowed';
    END IF;
    IF NEW.subscription_source IS DISTINCT FROM OLD.subscription_source THEN
        RAISE EXCEPTION 'Updating subscription_source is not allowed';
    END IF;
    IF NEW.access_expires_at IS DISTINCT FROM OLD.access_expires_at THEN
        RAISE EXCEPTION 'Updating access_expires_at is not allowed';
    END IF;
    IF NEW.questions_remaining IS DISTINCT FROM OLD.questions_remaining THEN
        RAISE EXCEPTION 'Updating questions_remaining is not allowed';
    END IF;
    IF NEW.documents_remaining IS DISTINCT FROM OLD.documents_remaining THEN
        RAISE EXCEPTION 'Updating documents_remaining is not allowed';
    END IF;
    IF NEW.stripe_subscription_id IS DISTINCT FROM OLD.stripe_subscription_id THEN
        RAISE EXCEPTION 'Updating stripe_subscription_id is not allowed';
    END IF;
    IF NEW.company_id IS DISTINCT FROM OLD.company_id THEN
        RAISE EXCEPTION 'Updating company_id is not allowed';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trigger_protect_profile_sensitive_columns ON profiles;
CREATE TRIGGER trigger_protect_profile_sensitive_columns
    BEFORE UPDATE ON profiles
    FOR EACH ROW
    EXECUTE FUNCTION protect_profile_sensitive_columns();

-- ===========================================
-- 7. ROW LEVEL SECURITY POLICIES
-- ===========================================

-- USER SUBSCRIPTIONS TABLE
ALTER TABLE user_subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own subscriptions" ON user_subscriptions;
CREATE POLICY "Users can view own subscriptions" ON user_subscriptions
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Admins can manage all subscriptions" ON user_subscriptions;
CREATE POLICY "Admins can manage all subscriptions" ON user_subscriptions
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM profiles 
            WHERE profiles.id = auth.uid() 
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

-- ACCESS CODES TABLE
ALTER TABLE access_codes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view active access codes" ON access_codes;

DROP POLICY IF EXISTS "Admins can manage access codes" ON access_codes;
CREATE POLICY "Admins can manage access codes" ON access_codes
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM profiles 
            WHERE profiles.id = auth.uid() 
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

-- ACCESS CODE REDEMPTIONS TABLE
ALTER TABLE access_code_redemptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own redemptions" ON access_code_redemptions;
CREATE POLICY "Users can view own redemptions" ON access_code_redemptions
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can redeem codes" ON access_code_redemptions;

DROP POLICY IF EXISTS "Admins can view all redemptions" ON access_code_redemptions;
CREATE POLICY "Admins can view all redemptions" ON access_code_redemptions
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM profiles 
            WHERE profiles.id = auth.uid() 
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

-- PROMO PASSCODES TABLE (backend validates code via SELECT; admins manage)
ALTER TABLE promo_passcodes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Authenticated can read active passcodes for redeem" ON promo_passcodes;

DROP POLICY IF EXISTS "Admins can manage promo passcodes" ON promo_passcodes;
CREATE POLICY "Admins can manage promo passcodes" ON promo_passcodes
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM profiles 
            WHERE profiles.id = auth.uid() 
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

-- PASSCODE REDEMPTIONS TABLE
ALTER TABLE passcode_redemptions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Users can view own passcode redemptions" ON passcode_redemptions;
CREATE POLICY "Users can view own passcode redemptions" ON passcode_redemptions
    FOR SELECT USING (auth.uid() = user_id);
DROP POLICY IF EXISTS "Users can insert own passcode redemption" ON passcode_redemptions;

DROP POLICY IF EXISTS "Admins can view all passcode redemptions" ON passcode_redemptions;
CREATE POLICY "Admins can view all passcode redemptions" ON passcode_redemptions
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM profiles 
            WHERE profiles.id = auth.uid() 
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

-- PAYMENT HISTORY TABLE
ALTER TABLE payment_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own payments" ON payment_history;
CREATE POLICY "Users can view own payments" ON payment_history
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Admins can view all payments" ON payment_history;
CREATE POLICY "Admins can view all payments" ON payment_history
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM profiles 
            WHERE profiles.id = auth.uid() 
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

-- SUBSCRIPTION EVENTS TABLE
ALTER TABLE subscription_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own subscription events" ON subscription_events;
CREATE POLICY "Users can view own subscription events" ON subscription_events
    FOR SELECT USING (
        subscription_id IN (
            SELECT id FROM user_subscriptions WHERE user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS "Admins can view all subscription events" ON subscription_events;
CREATE POLICY "Admins can view all subscription events" ON subscription_events
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM profiles 
            WHERE profiles.id = auth.uid() 
            AND profiles.role IN ('admin', 'engineer', 'inspector')
        )
    );

-- ===========================================
-- 10. HELPER FUNCTIONS
-- ===========================================

-- Function to check if user has active subscription
CREATE OR REPLACE FUNCTION has_active_subscription(p_user_id UUID)
RETURNS BOOLEAN AS $$
DECLARE
    has_subscription BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM user_subscriptions 
        WHERE user_id = p_user_id 
        AND status = 'active'
        AND current_period_end > NOW()
    ) OR EXISTS (
        SELECT 1 FROM access_code_redemptions
        WHERE user_id = p_user_id
        AND is_active = true
        AND access_granted_until > NOW()
        AND NOT expired
    ) INTO has_subscription;
    
    RETURN has_subscription;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to get user's access level
CREATE OR REPLACE FUNCTION get_user_access_level(p_user_id UUID)
RETURNS TEXT AS $$
DECLARE
    access_level TEXT;
BEGIN
    -- Check individual subscription
    SELECT CASE 
        WHEN plan_name = 'professional' THEN 'professional'
        WHEN plan_name = 'individual_monthly' THEN 'individual'
        ELSE 'individual'
    END INTO access_level
    FROM user_subscriptions
    WHERE user_id = p_user_id
    AND status = 'active'
    AND current_period_end > NOW()
    LIMIT 1;
    
    IF access_level IS NOT NULL THEN
        RETURN access_level;
    END IF;
    
    -- Check trial access from access code
    SELECT 'trial' INTO access_level
    FROM access_code_redemptions
    WHERE user_id = p_user_id
    AND is_active = true
    AND access_granted_until > NOW()
    AND NOT expired
    LIMIT 1;
    
    IF access_level IS NOT NULL THEN
        RETURN access_level;
    END IF;
    
    RETURN 'free';
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to update access code usage count
CREATE OR REPLACE FUNCTION update_access_code_usage()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE access_codes
        SET times_used = times_used + 1
        WHERE id = NEW.access_code_id;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to automatically update access code usage
DROP TRIGGER IF EXISTS trigger_update_code_usage ON access_code_redemptions;
CREATE TRIGGER trigger_update_code_usage
    AFTER INSERT ON access_code_redemptions
    FOR EACH ROW
    EXECUTE FUNCTION update_access_code_usage();

-- Function to check and expire access codes
CREATE OR REPLACE FUNCTION expire_old_access_code_redemptions()
RETURNS INTEGER AS $$
DECLARE
    expired_count INTEGER;
BEGIN
    UPDATE access_code_redemptions
    SET expired = true, is_active = false
    WHERE access_granted_until < NOW()
    AND NOT expired;
    
    GET DIAGNOSTICS expired_count = ROW_COUNT;
    
    RETURN expired_count;
END;
$$ LANGUAGE plpgsql;

-- Function to get subscription statistics
CREATE OR REPLACE FUNCTION get_subscription_stats()
RETURNS TABLE(
    total_subscriptions BIGINT,
    active_subscriptions BIGINT,
    trial_subscriptions BIGINT,
    total_revenue DECIMAL,
    monthly_revenue DECIMAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        COUNT(*)::BIGINT as total_subscriptions,
        COUNT(CASE WHEN status = 'active' THEN 1 END)::BIGINT as active_subscriptions,
        COUNT(CASE WHEN status = 'trialing' THEN 1 END)::BIGINT as trial_subscriptions,
        COALESCE(SUM(plan_price), 0) as total_revenue,
        COALESCE(SUM(CASE WHEN status = 'active' THEN plan_price ELSE 0 END), 0) as monthly_revenue
    FROM user_subscriptions;
END;
$$ LANGUAGE plpgsql;

-- ===========================================
-- 11. AUTOMATIC TIMESTAMP UPDATES
-- ===========================================

-- Update updated_at for user_subscriptions
DROP TRIGGER IF EXISTS trigger_user_subscriptions_updated_at ON user_subscriptions;
CREATE TRIGGER trigger_user_subscriptions_updated_at
    BEFORE UPDATE ON user_subscriptions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Update updated_at for access_codes
DROP TRIGGER IF EXISTS trigger_access_codes_updated_at ON access_codes;
CREATE TRIGGER trigger_access_codes_updated_at
    BEFORE UPDATE ON access_codes
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ===========================================
-- 12. SUCCESS MESSAGE
-- ===========================================

DO $$
BEGIN
    RAISE NOTICE '✅ SUBSCRIPTION SYSTEM MIGRATION COMPLETED!';
    RAISE NOTICE '✅ Tables Created:';
    RAISE NOTICE '   - user_subscriptions';
    RAISE NOTICE '   - access_codes';
    RAISE NOTICE '   - access_code_redemptions';
    RAISE NOTICE '   - payment_history';
    RAISE NOTICE '   - subscription_events';
    RAISE NOTICE '✅ Profiles table updated with subscription fields';
    RAISE NOTICE '✅ RLS policies enabled for all tables';
    RAISE NOTICE '✅ Helper functions created';
    RAISE NOTICE '✅ Triggers configured for automatic updates';
    RAISE NOTICE '';
    RAISE NOTICE '📝 Next Steps:';
    RAISE NOTICE '   1. Set up Stripe account and create 2 products:';
    RAISE NOTICE '      - Individual Monthly: $29 AUD/month';
    RAISE NOTICE '      - Professional: $199 AUD/month';
    RAISE NOTICE '   2. Implement backend services (stripe_service.py, subscription_service.py, etc.)';
    RAISE NOTICE '   3. Implement frontend components';
    RAISE NOTICE '   4. Test all flows thoroughly';
    RAISE NOTICE '';
    RAISE NOTICE '🧪 Test Functions:';
    RAISE NOTICE '   SELECT has_active_subscription(''user-uuid'');';
    RAISE NOTICE '   SELECT get_user_access_level(''user-uuid'');';
    RAISE NOTICE '   SELECT * FROM get_subscription_stats();';
    RAISE NOTICE '   SELECT expire_old_access_code_redemptions();';
END $$;



-- >>> add_stripe_to_profiles.sql >>>
-- Add Stripe customer ID to profiles table
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT UNIQUE;

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_profiles_stripe_customer ON profiles(stripe_customer_id);

-- Add comment
COMMENT ON COLUMN profiles.stripe_customer_id IS 'Stripe customer ID for individual user subscriptions';

-- ===========================================
-- STRIPE PATTERN TABLES (Reference 2 Pattern)
-- ===========================================
-- This section creates the new Stripe-aligned tables for products, prices, and subscriptions
-- following the Reference 2 pattern

-- ===========================================
-- 1. CREATE CUSTOMERS TABLE
-- ===========================================
CREATE TABLE IF NOT EXISTS customers (
  -- UUID from auth.users
  id uuid references auth.users not null primary key,
  -- The user's customer ID in Stripe. User must not be able to update this.
  stripe_customer_id text
);

ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
-- No policies as this is a private table that the user must not have access to.

-- ===========================================
-- 2. CREATE PRODUCTS TABLE
-- ===========================================
CREATE TABLE IF NOT EXISTS products (
  -- Product ID from Stripe, e.g. prod_1234.
  id text primary key,
  -- Whether the product is currently available for purchase.
  active boolean,
  -- The product's name, meant to be displayable to the customer.
  name text,
  -- The product's description, meant to be displayable to the customer.
  description text,
  -- A URL of the product image in Stripe, meant to be displayable to the customer.
  image text,
  -- Set of key-value pairs, used to store additional information about the object in a structured format.
  metadata jsonb
);

ALTER TABLE products ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow public read-only access." ON products;
CREATE POLICY "Allow public read-only access." ON products FOR SELECT USING (true);

-- ===========================================
-- 3. CREATE PRICES TABLE
-- ===========================================
-- Create enums if they don't exist
DO $$ BEGIN
  CREATE TYPE pricing_type AS ENUM ('one_time', 'recurring');
EXCEPTION
  WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
  CREATE TYPE pricing_plan_interval AS ENUM ('day', 'week', 'month', 'year');
EXCEPTION
  WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS prices (
  -- Price ID from Stripe, e.g. price_1234.
  id text primary key,
  -- The ID of the product that this price belongs to.
  product_id text references products, 
  -- Whether the price can be used for new purchases.
  active boolean,
  -- A brief description of the price.
  description text,
  -- The unit amount as a positive integer in the smallest currency unit (e.g., 100 cents for US$1.00).
  unit_amount bigint,
  -- Three-letter ISO currency code, in lowercase.
  currency text check (char_length(currency) = 3),
  -- One of `one_time` or `recurring` depending on whether the price is for a one-time purchase or a recurring (subscription) purchase.
  type pricing_type,
  -- The frequency at which a subscription is billed. One of `day`, `week`, `month` or `year`.
  interval pricing_plan_interval,
  -- The number of intervals (specified in the `interval` attribute) between subscription billings.
  interval_count integer,
  -- Default number of trial days when subscribing a customer to this price.
  trial_period_days integer,
  -- Set of key-value pairs, used to store additional information about the object in a structured format.
  metadata jsonb
);

ALTER TABLE prices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow public read-only access." ON prices;
CREATE POLICY "Allow public read-only access." ON prices FOR SELECT USING (true);

-- ===========================================
-- 4. CREATE SUBSCRIPTION STATUS ENUM
-- ===========================================
DO $$ BEGIN
  CREATE TYPE subscription_status AS ENUM ('trialing', 'active', 'canceled', 'incomplete', 'incomplete_expired', 'past_due', 'unpaid', 'paused');
EXCEPTION
  WHEN duplicate_object THEN null;
END $$;

-- ===========================================
-- 5. CREATE NEW SUBSCRIPTIONS TABLE (Reference 2 pattern)
-- ===========================================
CREATE TABLE IF NOT EXISTS subscriptions (
  -- Subscription ID from Stripe, e.g. sub_1234. (PRIMARY KEY - following Reference 2)
  id text primary key,
  -- User ID (all subscriptions are individual)
  user_id uuid NOT NULL references auth.users,
  -- The status of the subscription object
  status subscription_status,
  -- Set of key-value pairs, used to store additional information about the object in a structured format.
  metadata jsonb,
  -- ID of the price that created this subscription (following Reference 2)
  price_id text references prices,
  -- Quantity multiplied by the unit amount of the price creates the amount of the subscription.
  quantity integer,
  -- If true the subscription has been canceled by the user and will be deleted at the end of the billing period.
  cancel_at_period_end boolean,
  -- Time at which the subscription was created.
  created timestamp with time zone default timezone('utc'::text, now()) not null,
  -- Start of the current period that the subscription has been invoiced for.
  current_period_start timestamp with time zone default timezone('utc'::text, now()) not null,
  -- End of the current period that the subscription has been invoiced for.
  current_period_end timestamp with time zone default timezone('utc'::text, now()) not null,
  -- If the subscription has ended, the timestamp of the date the subscription ended.
  ended_at timestamp with time zone,
  -- A date in the future at which the subscription will automatically get canceled.
  cancel_at timestamp with time zone,
  -- If the subscription has been canceled, the date of that cancellation.
  canceled_at timestamp with time zone,
  -- If the subscription has a trial, the beginning of that trial.
  trial_start timestamp with time zone,
  -- If the subscription has a trial, the end of that trial.
  trial_end timestamp with time zone,
  -- Usage limits (KEEP: our addition for plan enforcement)
  max_documents integer,
  max_questions integer,
  documents_uploaded integer default 0,
  questions_asked integer default 0
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_price_id ON subscriptions(price_id);

-- Enable RLS
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Users can view their own subscriptions
DROP POLICY IF EXISTS "Can only view own subs data." ON subscriptions;
CREATE POLICY "Can only view own subs data." ON subscriptions
  FOR SELECT USING (auth.uid() = user_id);

-- Insert policy
DROP POLICY IF EXISTS "Can insert own subs data." ON subscriptions;
CREATE POLICY "Can insert own subs data." ON subscriptions
  FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Update policy
DROP POLICY IF EXISTS "Can update own subs data." ON subscriptions;
CREATE POLICY "Can update own subs data." ON subscriptions
  FOR UPDATE USING (auth.uid() = user_id);

-- Admin policy
DROP POLICY IF EXISTS "Admins can manage all subscriptions." ON subscriptions;
CREATE POLICY "Admins can manage all subscriptions." ON subscriptions
  FOR ALL USING (
    EXISTS (SELECT 1 FROM profiles WHERE profiles.id = auth.uid() AND profiles.role = 'admin')
  );

-- ===========================================
-- 6. MIGRATE DATA FROM user_subscriptions TO subscriptions (if needed)
-- ===========================================
-- Only migrate subscriptions that have a stripe_subscription_id
-- We'll use the stripe_subscription_id as the primary key
INSERT INTO subscriptions (
  id,  -- Use stripe_subscription_id as primary key
  user_id,
  status,
  metadata,
  price_id,  -- Will be NULL initially, needs to be set from Stripe
  quantity,
  cancel_at_period_end,
  created,
  current_period_start,
  current_period_end,
  ended_at,
  cancel_at,
  canceled_at,
  trial_start,
  trial_end,
  max_documents,
  max_questions,
  documents_uploaded,
  questions_asked
)
SELECT 
  stripe_subscription_id as id,
  user_id,
  CASE 
    WHEN status = 'cancelled' THEN 'canceled'::subscription_status
    WHEN status = 'suspended' THEN 'canceled'::subscription_status
    ELSE status::subscription_status
  END as status,
  jsonb_build_object(
    'plan_name', plan_name,
    'plan_price', plan_price,
    'currency', currency,
    'subscription_type', subscription_type
  ) as metadata,
  NULL as price_id,  -- Will need to be populated from Stripe webhooks
  1 as quantity,
  COALESCE(cancel_at_period_end, false),
  COALESCE(created_at, timezone('utc'::text, now())) as created,
  -- Legacy user_subscriptions rows may have NULL periods; subscriptions requires NOT NULL
  COALESCE(current_period_start, created_at, timezone('utc'::text, now())) as current_period_start,
  COALESCE(
    current_period_end,
    created_at + INTERVAL '1 month',
    timezone('utc'::text, now()) + INTERVAL '1 month'
  ) as current_period_end,
  NULL as ended_at,
  NULL as cancel_at,
  cancelled_at as canceled_at,
  trial_start,
  trial_end,
  max_documents,
  max_questions,
  documents_uploaded,
  questions_asked
FROM user_subscriptions
WHERE stripe_subscription_id IS NOT NULL
  AND stripe_subscription_id NOT IN (SELECT id FROM subscriptions)
ON CONFLICT (id) DO NOTHING;

-- ===========================================
-- 7. MIGRATE CUSTOMER DATA
-- ===========================================
-- Populate customers table from profiles
INSERT INTO customers (id, stripe_customer_id)
SELECT id, stripe_customer_id
FROM profiles
WHERE stripe_customer_id IS NOT NULL
  AND id NOT IN (SELECT id FROM customers)
ON CONFLICT (id) DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id;

-- ===========================================
-- 8. REALTIME SUBSCRIPTIONS
-- ===========================================
DROP PUBLICATION IF EXISTS supabase_realtime;
CREATE PUBLICATION supabase_realtime FOR TABLE products, prices;


-- SUBSCRIPTION MANAGEMENT VIEWS
-- ===========================================
-- Creates views to easily view and manage user subscriptions
-- Unifies data from both subscriptions and user_subscriptions tables

-- ===========================================
-- 1. VIEW: All User Subscriptions (Unified)
-- ===========================================
-- This view combines data from both subscriptions and user_subscriptions tables
-- to show a complete picture of all user subscriptions

CREATE OR REPLACE VIEW subscription_overview AS
SELECT 
    -- Subscription identifiers
    COALESCE(s.id, us.stripe_subscription_id, us.id::text) as subscription_id,
    COALESCE(s.id, us.stripe_subscription_id) as stripe_subscription_id,
    us.id as database_id,
    
    -- User info
    COALESCE(s.user_id, us.user_id) as user_id,
    'individual'::text as subscription_type,
    
    -- Status
    COALESCE(s.status::text, us.status) as status,
    
    -- Plan details
    us.plan_name,
    us.plan_price,
    us.currency,
    s.price_id,
    p.name as product_name,
    p.description as product_description,
    pr.unit_amount as price_amount,
    pr.interval as billing_interval,
    
    -- Stripe customer
    COALESCE(
        (SELECT stripe_customer_id FROM customers WHERE id = COALESCE(s.user_id, us.user_id)),
        us.stripe_customer_id
    ) as stripe_customer_id,
    
    -- Billing dates
    COALESCE(s.current_period_start, us.current_period_start) as current_period_start,
    COALESCE(s.current_period_end, us.current_period_end) as current_period_end,
    COALESCE(s.cancel_at_period_end, us.cancel_at_period_end) as cancel_at_period_end,
    COALESCE(s.canceled_at, us.cancelled_at) as canceled_at,
    COALESCE(s.trial_start, us.trial_start) as trial_start,
    COALESCE(s.trial_end, us.trial_end) as trial_end,
    
    -- Usage limits
    COALESCE(s.max_documents, us.max_documents) as max_documents,
    COALESCE(s.max_questions, us.max_questions) as max_questions,
    COALESCE(s.documents_uploaded, us.documents_uploaded, 0) as documents_uploaded,
    COALESCE(s.questions_asked, us.questions_asked, 0) as questions_asked,
    
    -- Timestamps
    COALESCE(s.created, us.created_at) as created_at,
    GREATEST(
        COALESCE(s.current_period_start, us.updated_at),
        COALESCE(us.updated_at, s.current_period_start)
    ) as updated_at,
    
    -- Source table indicator
    CASE 
        WHEN s.id IS NOT NULL THEN 'subscriptions'
        WHEN us.id IS NOT NULL THEN 'user_subscriptions'
        ELSE 'unknown'
    END as source_table
    
FROM subscriptions s
FULL OUTER JOIN user_subscriptions us ON s.id = us.stripe_subscription_id
LEFT JOIN prices pr ON s.price_id = pr.id
LEFT JOIN products p ON pr.product_id = p.id;

-- Grant access to authenticated users (they can only see their own)
ALTER VIEW subscription_overview OWNER TO postgres;

-- ===========================================
-- 2. VIEW: Active Subscriptions Only
-- ===========================================
CREATE OR REPLACE VIEW active_subscriptions AS
SELECT *
FROM subscription_overview
WHERE status IN ('active', 'trialing', 'past_due');

-- ===========================================
-- 3. VIEW: Subscriptions with User Details
-- (profiles.email only — never join auth.users; avoids Security Advisor auth_users_exposed)
-- Must DROP first: CREATE OR REPLACE cannot change column types (e.g. varchar(255) → text).
-- ===========================================
DROP VIEW IF EXISTS public.subscription_details CASCADE;

CREATE VIEW public.subscription_details AS
SELECT
    so.*,
    p.email::text AS user_email,
    p.full_name::text AS user_name,
    p.role::text AS user_role
FROM public.subscription_overview so
LEFT JOIN public.profiles p ON so.user_id = p.id;

ALTER VIEW public.subscription_details OWNER TO postgres;

-- ===========================================
-- 4. FUNCTION: Get User's Active Subscription
-- ===========================================
CREATE OR REPLACE FUNCTION public.is_app_admin()
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = public
AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM profiles
        WHERE id = auth.uid()
          AND role IN ('admin', 'engineer', 'inspector')
    );
END;
$$;

CREATE OR REPLACE FUNCTION get_user_active_subscription(p_user_id UUID)
RETURNS TABLE (
    subscription_id TEXT,
    stripe_subscription_id TEXT,
    status TEXT,
    plan_name TEXT,
    product_name TEXT,
    current_period_end TIMESTAMP WITH TIME ZONE,
    max_documents INTEGER,
    max_questions INTEGER,
    documents_uploaded INTEGER,
    questions_asked INTEGER
) AS $$
DECLARE
    v_jwt_role TEXT;
BEGIN
    v_jwt_role := COALESCE(current_setting('request.jwt.claim.role', true), '');

    IF p_user_id IS DISTINCT FROM auth.uid()
       AND v_jwt_role != 'service_role'
       AND NOT is_app_admin() THEN
        RAISE EXCEPTION 'Access denied';
    END IF;

    RETURN QUERY
    SELECT 
        so.subscription_id,
        so.stripe_subscription_id,
        so.status,
        so.plan_name,
        so.product_name,
        so.current_period_end,
        so.max_documents,
        so.max_questions,
        so.documents_uploaded,
        so.questions_asked
    FROM subscription_overview so
    WHERE so.user_id = p_user_id
        AND so.status IN ('active', 'trialing', 'past_due')
    ORDER BY so.created_at DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- ===========================================
-- 5. FUNCTION: Get All Subscriptions for Admin
-- ===========================================
-- Drop old function if it exists (in case return type changed)
-- Must drop first because return type changed (removed company_name)
DO $$ 
BEGIN
    DROP FUNCTION IF EXISTS get_all_subscriptions_admin() CASCADE;
EXCEPTION WHEN OTHERS THEN
    -- Ignore if function doesn't exist
    NULL;
END $$;

CREATE OR REPLACE FUNCTION get_all_subscriptions_admin()
RETURNS TABLE (
    subscription_id TEXT,
    user_email TEXT,
    user_name TEXT,
    status TEXT,
    plan_name TEXT,
    product_name TEXT,
    current_period_end TIMESTAMP WITH TIME ZONE,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    created_at TIMESTAMP WITH TIME ZONE
) AS $$
DECLARE
    v_jwt_role TEXT;
BEGIN
    v_jwt_role := COALESCE(current_setting('request.jwt.claim.role', true), '');

    IF v_jwt_role != 'service_role' AND NOT is_app_admin() THEN
        RAISE EXCEPTION 'Admin access required';
    END IF;

    RETURN QUERY
    SELECT 
        sd.subscription_id,
        sd.user_email,
        sd.user_name,
        sd.status,
        sd.plan_name,
        sd.product_name,
        sd.current_period_end,
        sd.stripe_customer_id,
        sd.stripe_subscription_id,
        sd.created_at
    FROM subscription_details sd
    ORDER BY sd.created_at DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- ===========================================
-- 6. GRANT PERMISSIONS
-- ===========================================
-- Subscription views: backend service_role only (no direct client reads)
REVOKE ALL ON subscription_overview FROM PUBLIC;
REVOKE ALL ON active_subscriptions FROM PUBLIC;
REVOKE ALL ON public.subscription_details FROM PUBLIC;
REVOKE SELECT ON subscription_overview FROM authenticated;
REVOKE SELECT ON active_subscriptions FROM authenticated;
REVOKE SELECT ON public.subscription_details FROM anon;
REVOKE SELECT ON public.subscription_details FROM authenticated;
GRANT SELECT ON subscription_overview TO service_role;
GRANT SELECT ON active_subscriptions TO service_role;
GRANT SELECT ON public.subscription_details TO service_role;

-- Redeem RPCs: authenticated users (own uid) + backend service_role (explicit user id)
GRANT EXECUTE ON FUNCTION redeem_promo_passcode(TEXT, UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION redeem_promo_passcode(TEXT, UUID) TO service_role;
GRANT EXECUTE ON FUNCTION redeem_access_code(TEXT, UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION redeem_access_code(TEXT, UUID) TO service_role;

-- Subscription RPCs
GRANT EXECUTE ON FUNCTION is_app_admin() TO authenticated;
GRANT EXECUTE ON FUNCTION get_user_active_subscription(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION get_user_active_subscription(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION get_all_subscriptions_admin() TO authenticated;
GRANT EXECUTE ON FUNCTION get_all_subscriptions_admin() TO service_role;

-- ===========================================
-- 5b. COMPANY SEATS + MARKETING REFERRALS
-- login_email links partner → Augusta user (Partner tab). Also re-ensured in section 6b.
-- ===========================================
CREATE TABLE IF NOT EXISTS referral_partners (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    notes TEXT,
    commission_note TEXT,
    login_email TEXT,  -- Partner dashboard: must match profiles.email
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_referral_partners_code ON referral_partners(code);
CREATE INDEX IF NOT EXISTS idx_referral_partners_active ON referral_partners(is_active);
-- Existing DBs: CREATE TABLE IF NOT EXISTS will not add new columns — run ADD COLUMN
ALTER TABLE referral_partners ADD COLUMN IF NOT EXISTS login_email TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_partners_login_email
    ON referral_partners (lower(login_email))
    WHERE login_email IS NOT NULL AND btrim(login_email) <> '';

CREATE TABLE IF NOT EXISTS companies (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'Company',
    owner_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    join_code TEXT UNIQUE NOT NULL,
    max_seats INTEGER NOT NULL CHECK (max_seats > 0),
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'inactive')),
    referred_by_partner_id UUID REFERENCES referral_partners(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_companies_owner ON companies(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_companies_join_code ON companies(join_code);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);
CREATE INDEX IF NOT EXISTS idx_companies_referral ON companies(referred_by_partner_id);

CREATE TABLE IF NOT EXISTS company_memberships (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
    joined_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (company_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_company_memberships_user ON company_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_company_memberships_company ON company_memberships(company_id);
CREATE INDEX IF NOT EXISTS idx_company_memberships_active ON company_memberships(company_id, status);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_documents_company_id ON documents(company_id) WHERE company_id IS NOT NULL;

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id) ON DELETE SET NULL;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS welcome_email_sent_at TIMESTAMPTZ;
COMMENT ON COLUMN profiles.welcome_email_sent_at IS
  'When the automated customer welcome email was sent (idempotent stamp)';

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'profiles'
          AND con.contype = 'c'
          AND pg_get_constraintdef(con.oid) ILIKE '%subscription_source%'
    LOOP
        EXECUTE format('ALTER TABLE profiles DROP CONSTRAINT IF EXISTS %I', r.conname);
    END LOOP;
END $$;
ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_subscription_source_check;
ALTER TABLE profiles ADD CONSTRAINT profiles_subscription_source_check
    CHECK (subscription_source IS NULL OR subscription_source IN ('stripe', 'passcode', 'company'));

DROP POLICY IF EXISTS "Users can view company documents" ON documents;
CREATE POLICY "Users can view company documents" ON documents
    FOR SELECT USING (
        company_id IS NOT NULL
        AND EXISTS (
            SELECT 1 FROM company_memberships m
            WHERE m.company_id = documents.company_id
              AND m.user_id = auth.uid()
              AND m.status = 'active'
        )
    );

CREATE OR REPLACE FUNCTION public.join_company_by_code(
    p_code TEXT,
    p_user_id UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_user_id UUID;
    v_jwt_role TEXT;
    v_auth_role TEXT;
    v_company companies%ROWTYPE;
    v_seats INTEGER;
    v_existing UUID;
BEGIN
    p_code := NULLIF(UPPER(TRIM(p_code)), '');
    IF p_code IS NULL THEN
        RAISE EXCEPTION 'Company code is required.';
    END IF;

    -- Backend uses service_role key; claim may appear on jwt.claim.role or auth.role()
    v_jwt_role := COALESCE(current_setting('request.jwt.claim.role', true), '');
    BEGIN
        v_auth_role := COALESCE(auth.role(), '');
    EXCEPTION WHEN OTHERS THEN
        v_auth_role := '';
    END;

    IF (v_jwt_role = 'service_role' OR v_auth_role = 'service_role') AND p_user_id IS NOT NULL THEN
        v_user_id := p_user_id;
    ELSIF auth.uid() IS NOT NULL THEN
        v_user_id := auth.uid();
        IF p_user_id IS NOT NULL AND p_user_id IS DISTINCT FROM v_user_id THEN
            RAISE EXCEPTION 'Access denied';
        END IF;
    ELSIF p_user_id IS NOT NULL AND v_jwt_role = '' AND v_auth_role = '' THEN
        -- Some PostgREST/service-role setups omit role claims; allow explicit p_user_id
        -- only when SECURITY DEFINER is invoked without an end-user JWT.
        v_user_id := p_user_id;
    ELSE
        RAISE EXCEPTION 'Access denied';
    END IF;

    SELECT * INTO v_company
    FROM companies
    WHERE UPPER(join_code) = p_code
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Invalid company code.';
    END IF;
    IF v_company.status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'This company subscription is not active. Status: %.', v_company.status;
    END IF;

    -- Already a member of this company: always re-sync Professional access on the profile
    SELECT id INTO v_existing
    FROM company_memberships
    WHERE company_id = v_company.id AND user_id = v_user_id AND status = 'active';
    IF v_existing IS NOT NULL THEN
        UPDATE profiles
        SET account_type = 'professional',
            subscription_status = 'active',
            subscription_source = 'company',
            company_id = v_company.id,
            access_expires_at = NULL
        WHERE id = v_user_id;

        RETURN jsonb_build_object(
            'success', true,
            'company_id', v_company.id,
            'company_name', v_company.name,
            'message', 'Already a member of this company. Access refreshed.'
        );
    END IF;

    -- Stuck on another company that is pending/inactive: release seat so they can join this one
    UPDATE company_memberships m
    SET status = 'removed'
    FROM companies c
    WHERE m.user_id = v_user_id
      AND m.status = 'active'
      AND m.company_id = c.id
      AND c.id IS DISTINCT FROM v_company.id
      AND c.status IS DISTINCT FROM 'active';

    IF EXISTS (
        SELECT 1
        FROM company_memberships m
        JOIN companies c ON c.id = m.company_id
        WHERE m.user_id = v_user_id
          AND m.status = 'active'
          AND c.status = 'active'
          AND c.id IS DISTINCT FROM v_company.id
    ) THEN
        RAISE EXCEPTION 'You already belong to a company. Leave it before joining another.';
    END IF;

    SELECT COUNT(*)::INTEGER INTO v_seats
    FROM company_memberships
    WHERE company_id = v_company.id AND status = 'active';

    IF v_seats >= v_company.max_seats THEN
        RAISE EXCEPTION 'This company has no seats left.';
    END IF;

    INSERT INTO company_memberships (company_id, user_id, role, status)
    VALUES (v_company.id, v_user_id, 'member', 'active')
    ON CONFLICT (company_id, user_id) DO UPDATE
      SET status = 'active', role = 'member', joined_at = NOW();

    UPDATE profiles
    SET account_type = 'professional',
        subscription_status = 'active',
        subscription_source = 'company',
        company_id = v_company.id,
        access_expires_at = NULL
    WHERE id = v_user_id;

    RETURN jsonb_build_object(
        'success', true,
        'company_id', v_company.id,
        'company_name', v_company.name,
        'message', 'Joined company successfully.'
    );
END;
$$;

GRANT EXECUTE ON FUNCTION join_company_by_code(TEXT, UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION join_company_by_code(TEXT, UUID) TO service_role;

-- Repair: members of active companies who never got Professional on profiles
UPDATE profiles p
SET account_type = 'professional',
    subscription_status = 'active',
    subscription_source = 'company',
    company_id = c.id,
    access_expires_at = NULL,
    updated_at = NOW()
FROM company_memberships m
JOIN companies c ON c.id = m.company_id
WHERE m.user_id = p.id
  AND m.status = 'active'
  AND c.status = 'active'
  AND (
        p.account_type IS DISTINCT FROM 'professional'
     OR p.subscription_source IS DISTINCT FROM 'company'
     OR p.company_id IS DISTINCT FROM c.id
     OR p.subscription_status IS DISTINCT FROM 'active'
  );

-- Backfill: attach existing member uploads to their active company library
-- (Also re-run in §6d after attach helper + activation self-heal exist.)
UPDATE documents d
SET company_id = m.company_id,
    updated_at = NOW()
FROM company_memberships m
JOIN companies c ON c.id = m.company_id
WHERE m.user_id = d.user_id
  AND m.status = 'active'
  AND c.status = 'active'
  AND d.company_id IS NULL;

ALTER TABLE referral_partners ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_memberships ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Admins manage referral partners" ON referral_partners;
CREATE POLICY "Admins manage referral partners" ON referral_partners
    FOR ALL USING (
        EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role IN ('admin', 'engineer', 'inspector'))
    );

DROP POLICY IF EXISTS "Members read own company" ON companies;
CREATE POLICY "Members read own company" ON companies
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM company_memberships m
            WHERE m.company_id = companies.id AND m.user_id = auth.uid() AND m.status = 'active'
        )
    );

DROP POLICY IF EXISTS "Admins manage companies" ON companies;
CREATE POLICY "Admins manage companies" ON companies
    FOR ALL USING (
        EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role IN ('admin', 'engineer', 'inspector'))
    );

DROP POLICY IF EXISTS "Users read own memberships" ON company_memberships;
CREATE POLICY "Users read own memberships" ON company_memberships
    FOR SELECT USING (user_id = auth.uid());

DROP POLICY IF EXISTS "Admins manage memberships" ON company_memberships;
CREATE POLICY "Admins manage memberships" ON company_memberships
    FOR ALL USING (
        EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role IN ('admin', 'engineer', 'inspector'))
    );

-- Refresh search visibility for company-shared documents (after documents.company_id exists)
CREATE OR REPLACE FUNCTION search_chunks_vector(
    p_query_embedding vector(384),
    p_user_id UUID DEFAULT NULL,
    p_document_id UUID DEFAULT NULL,
    p_limit INTEGER DEFAULT 10,
    p_codebook TEXT DEFAULT NULL,
    p_jurisdiction TEXT DEFAULT NULL
) RETURNS TABLE (
    chunk_id UUID,
    document_id UUID,
    similarity FLOAT,
    text TEXT,
    clause_number TEXT,
    heading TEXT,
    page_number INTEGER,
    codebook TEXT,
    jurisdiction TEXT
) AS $$
BEGIN
    IF auth.uid() IS NOT NULL
       AND p_user_id IS NOT NULL
       AND p_user_id IS DISTINCT FROM auth.uid() THEN
        RAISE EXCEPTION 'search_chunks_vector: p_user_id must match auth.uid()'
            USING ERRCODE = '42501';
    END IF;

    RETURN QUERY
    SELECT
        c.id AS chunk_id,
        c.document_id,
        (1 - (e.embedding <=> p_query_embedding))::FLOAT AS similarity,
        c.text,
        c.clause_number,
        c.heading,
        c.page_number,
        c.codebook,
        c.jurisdiction
    FROM chunk_embeddings e
    JOIN chunks c ON e.chunk_id = c.id
    JOIN documents d ON c.document_id = d.id
    WHERE d.status = 'ready_for_search'
      AND (
          p_user_id IS NULL
          OR e.user_id = p_user_id
          OR d.is_shared_library = true
          OR (
              d.company_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM company_memberships m
                  WHERE m.company_id = d.company_id
                    AND m.user_id = p_user_id
                    AND m.status = 'active'
              )
          )
      )
      AND (p_document_id IS NULL OR e.document_id = p_document_id)
      AND (p_codebook IS NULL OR c.codebook = p_codebook)
      AND (
          p_jurisdiction IS NULL
          OR c.jurisdiction IS NULL
          OR c.jurisdiction = p_jurisdiction
          OR c.jurisdiction = 'BOTH'
      )
    ORDER BY e.embedding <=> p_query_embedding
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

CREATE OR REPLACE FUNCTION _hybrid_visible_chunks(
    p_user_id UUID,
    p_document_ids UUID[],
    p_codebook TEXT DEFAULT NULL
) RETURNS SETOF UUID
LANGUAGE sql
STABLE
SET search_path = public
AS $$
    SELECT c.id
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE d.status = 'ready_for_search'
      AND (
          d.user_id = p_user_id
          OR COALESCE(d.is_shared_library, false) = true
          OR (
              d.company_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM company_memberships m
                  WHERE m.company_id = d.company_id
                    AND m.user_id = p_user_id
                    AND m.status = 'active'
              )
          )
      )
      AND (p_document_ids IS NULL OR c.document_id = ANY(p_document_ids))
      AND (p_codebook IS NULL OR c.codebook = p_codebook);
$$;

-- ===========================================
-- 6b. ENSURE: referral_partners.login_email (Partner dashboard)
-- Idempotent — safe if 5b already applied. Fixes DBs created before login_email existed.
-- ===========================================
ALTER TABLE referral_partners ADD COLUMN IF NOT EXISTS login_email TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_partners_login_email
    ON referral_partners (lower(login_email))
    WHERE login_email IS NOT NULL AND btrim(login_email) <> '';
COMMENT ON COLUMN referral_partners.login_email IS
  'Augusta login email for this partner; unlocks Dashboard → Partner tab when it matches profiles.email';

-- ===========================================
-- 6c. SELF-HEAL: pending company → active when owner has paid subscription
-- Covers missed Stripe webhooks (company stays pending, stripe_subscription_id NULL).
-- Trigger runs on user_subscriptions insert/update; one-shot repair below for existing rows.
-- ===========================================

-- Company library helper (used by activate + §6d triggers): stamp member uploads with company_id
CREATE OR REPLACE FUNCTION public.attach_member_documents_to_company(p_company_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_count INTEGER := 0;
BEGIN
    IF p_company_id IS NULL THEN
        RETURN 0;
    END IF;

    UPDATE documents d
    SET company_id = p_company_id,
        updated_at = NOW()
    FROM company_memberships m
    WHERE m.company_id = p_company_id
      AND m.user_id = d.user_id
      AND m.status = 'active'
      AND d.company_id IS NULL;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.activate_pending_company_for_owner(
    p_user_id UUID,
    p_stripe_subscription_id TEXT,
    p_stripe_customer_id TEXT DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_company_id UUID;
BEGIN
    IF p_user_id IS NULL OR NULLIF(btrim(p_stripe_subscription_id), '') IS NULL THEN
        RETURN NULL;
    END IF;

    -- Latest pending company for this owner (checkout creates these before Stripe returns)
    SELECT c.id INTO v_company_id
    FROM companies c
    WHERE c.owner_user_id = p_user_id
      AND c.status = 'pending'
      AND (
          c.stripe_subscription_id IS NULL
          OR c.stripe_subscription_id = p_stripe_subscription_id
      )
      AND c.created_at > NOW() - INTERVAL '14 days'
    ORDER BY c.created_at DESC
    LIMIT 1;

    IF v_company_id IS NULL THEN
        -- Also backfill stripe id on already-active company missing it
        UPDATE companies c
        SET stripe_subscription_id = p_stripe_subscription_id,
            stripe_customer_id = COALESCE(c.stripe_customer_id, NULLIF(btrim(p_stripe_customer_id), '')),
            updated_at = NOW()
        WHERE c.owner_user_id = p_user_id
          AND c.status = 'active'
          AND (c.stripe_subscription_id IS NULL OR btrim(c.stripe_subscription_id) = '')
        RETURNING c.id INTO v_company_id;

        IF v_company_id IS NULL THEN
            RETURN NULL;
        END IF;
    ELSE
        UPDATE companies
        SET status = 'active',
            stripe_subscription_id = p_stripe_subscription_id,
            stripe_customer_id = COALESCE(stripe_customer_id, NULLIF(btrim(p_stripe_customer_id), '')),
            updated_at = NOW()
        WHERE id = v_company_id;
    END IF;

    -- Ensure owner seat
    INSERT INTO company_memberships (company_id, user_id, role, status)
    VALUES (v_company_id, p_user_id, 'owner', 'active')
    ON CONFLICT (company_id, user_id) DO UPDATE
      SET status = 'active', role = 'owner';

    -- Owner profile → company Professional
    UPDATE profiles
    SET account_type = 'professional',
        subscription_status = 'active',
        subscription_source = 'company',
        company_id = v_company_id,
        stripe_subscription_id = p_stripe_subscription_id,
        stripe_customer_id = COALESCE(stripe_customer_id, NULLIF(btrim(p_stripe_customer_id), '')),
        access_expires_at = NULL,
        updated_at = NOW()
    WHERE id = p_user_id;

    -- Attach any member uploads that missed company_id while company was pending
    PERFORM public.attach_member_documents_to_company(v_company_id);

    RETURN v_company_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.trg_user_subscriptions_sync_company()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.stripe_subscription_id IS NOT NULL THEN
            UPDATE companies
            SET status = 'inactive', updated_at = NOW()
            WHERE stripe_subscription_id = OLD.stripe_subscription_id
              AND status IS DISTINCT FROM 'inactive';
        END IF;
        RETURN OLD;
    END IF;

    IF NEW.status IN ('active', 'trialing', 'past_due')
       AND NULLIF(btrim(NEW.stripe_subscription_id), '') IS NOT NULL
       AND NEW.user_id IS NOT NULL THEN
        PERFORM public.activate_pending_company_for_owner(
            NEW.user_id,
            NEW.stripe_subscription_id,
            NEW.stripe_customer_id
        );
    ELSIF NEW.status IN ('cancelled', 'canceled', 'suspended')
          AND NULLIF(btrim(NEW.stripe_subscription_id), '') IS NOT NULL THEN
        UPDATE companies
        SET status = 'inactive', updated_at = NOW()
        WHERE stripe_subscription_id = NEW.stripe_subscription_id
          AND status IS DISTINCT FROM 'inactive';

        UPDATE company_memberships m
        SET status = 'removed'
        FROM companies c
        WHERE m.company_id = c.id
          AND c.stripe_subscription_id = NEW.stripe_subscription_id
          AND m.status = 'active';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trigger_user_subscriptions_sync_company ON user_subscriptions;
CREATE TRIGGER trigger_user_subscriptions_sync_company
    AFTER INSERT OR UPDATE OF status, stripe_subscription_id, user_id
    ON user_subscriptions
    FOR EACH ROW
    EXECUTE FUNCTION public.trg_user_subscriptions_sync_company();

GRANT EXECUTE ON FUNCTION public.activate_pending_company_for_owner(UUID, TEXT, TEXT) TO service_role;

-- One-shot repair: pending companies whose owner already has a paid subscription row
SELECT public.activate_pending_company_for_owner(
    us.user_id,
    us.stripe_subscription_id,
    us.stripe_customer_id
)
FROM user_subscriptions us
WHERE us.status IN ('active', 'trialing', 'past_due')
  AND NULLIF(btrim(us.stripe_subscription_id), '') IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM companies c
      WHERE c.owner_user_id = us.user_id
        AND c.status = 'pending'
        AND c.created_at > NOW() - INTERVAL '14 days'
  );

-- ===========================================
-- 6d. COMPANY DOCUMENT LIBRARY — root cause + durable fix
--
-- ROOT CAUSE:
--   Employee visibility requires documents.company_id = active company id.
--   Uploads only stamped company_id when company was already active (app path).
--   PDFs uploaded while company was pending (or any missed stamp) stay company_id NULL
--   forever — one-shot UPDATE only ran when someone re-executed this file.
--   Activating the company did not backfill member documents.
--
-- FIX:
--   1) Auto-stamp company_id on every document insert (pending or active seat).
--   2) When company becomes active, or a seat joins, backfill that member's NULLs.
--   3) activate_pending_company_for_owner also backfills (§6c).
--   4) Optional diagnose_company_document_sharing() for ops debugging (not required on setup).
-- ===========================================

CREATE OR REPLACE FUNCTION public.resolve_company_id_for_uploader(p_user_id UUID)
RETURNS UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT m.company_id
    FROM company_memberships m
    JOIN companies c ON c.id = m.company_id
    WHERE m.user_id = p_user_id
      AND m.status = 'active'
      AND c.status IN ('pending', 'active')
    ORDER BY
        CASE WHEN c.status = 'active' THEN 0 ELSE 1 END,
        m.joined_at DESC NULLS LAST
    LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION public.trg_documents_attach_company()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- Only fill missing stamp; never override an explicit company_id
    IF NEW.company_id IS NULL AND NEW.user_id IS NOT NULL THEN
        NEW.company_id := public.resolve_company_id_for_uploader(NEW.user_id);
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trigger_documents_attach_company ON documents;
CREATE TRIGGER trigger_documents_attach_company
    BEFORE INSERT ON documents
    FOR EACH ROW
    EXECUTE FUNCTION public.trg_documents_attach_company();

CREATE OR REPLACE FUNCTION public.trg_companies_backfill_documents()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF NEW.status = 'active'
       AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'active') THEN
        PERFORM public.attach_member_documents_to_company(NEW.id);
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trigger_companies_backfill_documents ON companies;
CREATE TRIGGER trigger_companies_backfill_documents
    AFTER INSERT OR UPDATE OF status ON companies
    FOR EACH ROW
    EXECUTE FUNCTION public.trg_companies_backfill_documents();

CREATE OR REPLACE FUNCTION public.trg_memberships_attach_documents()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_company_id UUID;
    v_user_id UUID;
    v_status TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;

    v_company_id := NEW.company_id;
    v_user_id := NEW.user_id;
    v_status := NEW.status;

    IF v_status = 'active'
       AND EXISTS (
           SELECT 1 FROM companies c
           WHERE c.id = v_company_id
             AND c.status IN ('pending', 'active')
       ) THEN
        UPDATE documents d
        SET company_id = v_company_id,
            updated_at = NOW()
        WHERE d.user_id = v_user_id
          AND d.company_id IS NULL;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trigger_memberships_attach_documents ON company_memberships;
CREATE TRIGGER trigger_memberships_attach_documents
    AFTER INSERT OR UPDATE OF status, company_id, user_id ON company_memberships
    FOR EACH ROW
    EXECUTE FUNCTION public.trg_memberships_attach_documents();

-- Diagnostic: find why company PDFs are invisible to seats (no data mutation)
CREATE OR REPLACE FUNCTION public.diagnose_company_document_sharing(
    p_owner_email TEXT DEFAULT NULL,
    p_member_email TEXT DEFAULT NULL
)
RETURNS TABLE (
    issue_code TEXT,
    severity TEXT,
    detail TEXT,
    company_id UUID,
    company_status TEXT,
    document_id UUID,
    document_filename TEXT,
    owner_email TEXT,
    member_email TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_is_service BOOLEAN;
    v_uid UUID := auth.uid();
BEGIN
    v_is_service := COALESCE(auth.role(), '') = 'service_role'
        OR COALESCE(current_setting('request.jwt.claim.role', true), '') = 'service_role';

    -- Companies still pending (employees cannot join / frontend skips library)
    RETURN QUERY
    SELECT
        'company_not_active'::TEXT,
        'high'::TEXT,
        format('Company "%s" is %s — seats cannot see shared library until active.', c.name, c.status),
        c.id,
        c.status,
        NULL::UUID,
        NULL::TEXT,
        po.email,
        NULL::TEXT
    FROM companies c
    LEFT JOIN profiles po ON po.id = c.owner_user_id
    WHERE c.status IS DISTINCT FROM 'active'
      AND (
          p_owner_email IS NULL
          OR lower(po.email) = lower(p_owner_email)
      )
      AND (
          v_is_service
          OR v_uid IS NULL
          OR c.owner_user_id = v_uid
          OR EXISTS (
              SELECT 1 FROM company_memberships mx
              WHERE mx.company_id = c.id AND mx.user_id = v_uid AND mx.status = 'active'
          )
      );

    -- Active seats whose profile.company_id is wrong/missing
    RETURN QUERY
    SELECT
        'member_profile_company_mismatch'::TEXT,
        'medium'::TEXT,
        format('Seat profile.company_id=%s but membership company=%s.', p.company_id::text, m.company_id::text),
        c.id,
        c.status,
        NULL::UUID,
        NULL::TEXT,
        po.email,
        p.email
    FROM company_memberships m
    JOIN companies c ON c.id = m.company_id
    JOIN profiles p ON p.id = m.user_id
    LEFT JOIN profiles po ON po.id = c.owner_user_id
    WHERE m.status = 'active'
      AND c.status = 'active'
      AND p.company_id IS DISTINCT FROM m.company_id
      AND (p_owner_email IS NULL OR lower(po.email) = lower(p_owner_email))
      AND (p_member_email IS NULL OR lower(p.email) = lower(p_member_email))
      AND (
          v_is_service
          OR v_uid IS NULL
          OR m.user_id = v_uid
          OR c.owner_user_id = v_uid
      );

    -- Uploads by active members with NULL company_id (invisible to other seats)
    RETURN QUERY
    SELECT
        'document_missing_company_id'::TEXT,
        'high'::TEXT,
        'Document has no company_id — other seats cannot list or search it.',
        c.id,
        c.status,
        d.id,
        COALESCE(d.original_filename, d.filename),
        po.email,
        p.email
    FROM documents d
    JOIN company_memberships m ON m.user_id = d.user_id AND m.status = 'active'
    JOIN companies c ON c.id = m.company_id AND c.status IN ('pending', 'active')
    JOIN profiles p ON p.id = d.user_id
    LEFT JOIN profiles po ON po.id = c.owner_user_id
    WHERE d.company_id IS NULL
      AND COALESCE(d.is_shared_library, false) = false
      AND (p_owner_email IS NULL OR lower(po.email) = lower(p_owner_email))
      AND (p_member_email IS NULL OR lower(p.email) = lower(p_member_email))
      AND (
          v_is_service
          OR v_uid IS NULL
          OR m.user_id = v_uid
          OR c.owner_user_id = v_uid
          OR EXISTS (
              SELECT 1 FROM company_memberships mx
              WHERE mx.company_id = c.id AND mx.user_id = v_uid AND mx.status = 'active'
          )
      );

    -- Member active but owner docs still unstamped
    RETURN QUERY
    SELECT
        'seat_cannot_see_owner_docs'::TEXT,
        'high'::TEXT,
        format(
            'Member has active seat; %s owner doc(s) lack company_id stamp.',
            COUNT(*)::TEXT
        ),
        c.id,
        c.status,
        NULL::UUID,
        NULL::TEXT,
        po.email,
        pm.email
    FROM company_memberships mm
    JOIN companies c ON c.id = mm.company_id
    JOIN profiles pm ON pm.id = mm.user_id
    JOIN profiles po ON po.id = c.owner_user_id
    JOIN documents d ON d.user_id = c.owner_user_id AND d.company_id IS NULL
        AND COALESCE(d.is_shared_library, false) = false
    WHERE mm.status = 'active'
      AND mm.role = 'member'
      AND c.status = 'active'
      AND (p_owner_email IS NULL OR lower(po.email) = lower(p_owner_email))
      AND (p_member_email IS NULL OR lower(pm.email) = lower(p_member_email))
      AND (
          v_is_service
          OR v_uid IS NULL
          OR mm.user_id = v_uid
          OR c.owner_user_id = v_uid
      )
    GROUP BY c.id, c.status, po.email, pm.email
    HAVING COUNT(*) > 0;

    RETURN;
END;
$$;

CREATE OR REPLACE FUNCTION public.repair_company_document_sharing()
RETURNS TABLE (
    company_id UUID,
    documents_attached INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    r RECORD;
    n INTEGER;
BEGIN
    -- Also stamp pending companies so activation immediately shares existing uploads
    FOR r IN
        SELECT id FROM companies WHERE status IN ('pending', 'active')
    LOOP
        n := public.attach_member_documents_to_company(r.id);
        company_id := r.id;
        documents_attached := n;
        RETURN NEXT;
    END LOOP;
END;
$$;

GRANT EXECUTE ON FUNCTION public.attach_member_documents_to_company(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.resolve_company_id_for_uploader(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.diagnose_company_document_sharing(TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.diagnose_company_document_sharing(TEXT, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION public.repair_company_document_sharing() TO service_role;

-- One-shot: stamp all existing gaps now that helpers/triggers exist
SELECT * FROM public.repair_company_document_sharing();

-- ===========================================
-- 7. COMMENTS
-- ===========================================
COMMENT ON VIEW subscription_overview IS 'Unified view of all subscriptions from both subscriptions and user_subscriptions tables';
COMMENT ON VIEW active_subscriptions IS 'View of only active subscriptions (active, trialing, past_due)';
COMMENT ON VIEW public.subscription_details IS
  'Subscription rows with profile name/email (no auth.users join — avoids auth_users_exposed)';
COMMENT ON FUNCTION get_user_active_subscription IS 'Get active subscription for a specific user';
COMMENT ON FUNCTION get_all_subscriptions_admin IS 'Get all subscriptions for admin management (requires admin role)';
COMMENT ON FUNCTION public.activate_pending_company_for_owner IS
  'Activates latest pending company for owner when Stripe subscription is paid (webhook backstop)';
COMMENT ON FUNCTION public.attach_member_documents_to_company IS
  'Stamps documents.company_id for all active members of a company (NULL → company library)';
COMMENT ON FUNCTION public.diagnose_company_document_sharing IS
  'Optional: reports company-library gaps (call with no args for all companies). Not required — repair runs on combined_setup.';
COMMENT ON FUNCTION public.repair_company_document_sharing IS
  'Backfills documents.company_id for all pending/active companies (invoked automatically at end of combined_setup)';

DO $$
BEGIN
    RAISE NOTICE '✅ combined_setup.sql complete';
    RAISE NOTICE '   Core: profiles, documents, chunks, chunk_embeddings, standard_tables, admin_queue, pdf_processing_jobs';
    RAISE NOTICE '   Backup-compat: conversations, conversation_messages, user_subscriptions, search_chunks_enhanced';
    RAISE NOTICE '   Shared library: documents.is_shared_library + RLS (NCC/SIR admin upload, all-user read)';
    RAISE NOTICE '   Billing: Stripe, passcodes, company seats, referral partners (+ login_email), subscription views';
    RAISE NOTICE '   Company self-heal: trigger user_subscriptions → activate pending companies';
    RAISE NOTICE '   Company library: auto-stamp documents.company_id + backfill all companies on this run';
    RAISE NOTICE '   RPC: search_chunks_vector, search_chunks_fts/trgm/heading, count_visible_chunks';
    RAISE NOTICE '   RPC: try_claim_pdf_processing_job, finish_pdf_processing_job, join_company_by_code';
    RAISE NOTICE '   Re-run this file anytime to upgrade an existing project (no separate patch files).';
END $$;
