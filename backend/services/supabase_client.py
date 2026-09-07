import os
from supabase import create_client, Client
from dotenv import load_dotenv

# Load .env file, but don't override if already loaded by main.py
# This prevents multiple loads but ensures it's available if called directly
load_dotenv(override=False)

def get_supabase_client() -> Client:
    """Get Supabase client instance"""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not supabase_key:
        raise ValueError("Missing database connection environment variables (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)")
    
    return create_client(supabase_url, supabase_key)

def get_supabase_client_with_anon_key() -> Client:
    """Get Supabase client instance with anon key for storage operations"""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
    
    if not supabase_url or not supabase_anon_key:
        raise ValueError("Missing database connection environment variables (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)")
    
    return create_client(supabase_url, supabase_anon_key) 