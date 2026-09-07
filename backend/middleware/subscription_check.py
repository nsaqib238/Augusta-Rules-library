"""
Subscription Check Middleware - Validates user subscriptions and usage limits
"""
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from services.supabase_client import get_supabase_client
from services.subscription_service import subscription_service
from services.admin_allowlist import get_admin_email_allowlist
from services.async_utils import run_blocking
import logging

logger = logging.getLogger(__name__)

security = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """
    Get current user from JWT token. Enforces max concurrent sessions per account (see session_limit_service).
    
    Returns:
        User ID
        
    Raises:
        HTTPException if authentication fails or too many concurrent sessions
    """
    try:
        supabase = get_supabase_client()
        token = credentials.credentials

        # supabase-py is sync — offload the network call so the event loop
        # can serve other requests while we wait for Supabase Auth.
        user_response = await run_blocking(supabase.auth.get_user, token)

        if not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid token")

        user_id = user_response.user.id
        user_email = (user_response.user.email or "").strip().lower()

        # Enforce concurrent session limit (skip for admin allowlist).
        # check_and_track_session does 2–3 Supabase calls; keep them off-thread.
        try:
            allowlist = get_admin_email_allowlist()
            if not (user_email and user_email in allowlist):
                from services.session_limit_service import check_and_track_session
                await run_blocking(check_and_track_session, user_id, token)
        except ValueError as limit_err:
            raise HTTPException(status_code=403, detail=str(limit_err))
        except HTTPException:
            raise
        except Exception as limit_err:
            logger.warning("Session limit check failed (non-fatal): %s", limit_err)

        return user_id

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Authentication failed: {str(e)}")
        raise HTTPException(status_code=401, detail="Authentication failed")


async def get_current_user_with_email(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Get current user info (id + email) from JWT token.
    """
    try:
        supabase = get_supabase_client()
        token = credentials.credentials
        user_response = await run_blocking(supabase.auth.get_user, token)

        if not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid token")

        return {
            "id": user_response.user.id,
            "email": user_response.user.email,
        }

    except Exception as e:
        logger.error(f"Authentication failed: {str(e)}")
        raise HTTPException(status_code=401, detail="Authentication failed")


async def check_subscription_access(
    user_id: str = Depends(get_current_user),
    required_level: str = 'any'
) -> str:
    """
    Middleware to check if user has active subscription
    
    Args:
        user_id: User ID from get_current_user
        required_level: Required subscription level ('any', 'individual', 'company')
        
    Returns:
        User ID if access granted
        
    Raises:
        HTTPException if access denied
    """
    try:
        access = await subscription_service.check_user_access(user_id)
        
        if not access.get('has_access'):
            raise HTTPException(
                status_code=403,
                detail="Active subscription required. Please subscribe to continue."
            )
        
        account_type = access.get('account_type', 'free')
        
        # Check if user meets required level (all subscriptions are now individual)
        if required_level == 'individual':
            if account_type not in ['individual', 'professional']:
                raise HTTPException(
                    status_code=403,
                    detail="Paid subscription required for this feature"
                )
        
        # Note: 'company' level checks are deprecated - all subscriptions are now individual
        
        return user_id
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Subscription check failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to verify subscription")


async def check_upload_limit(user_id: str = Depends(get_current_user)) -> str:
    """
    Check if user can upload documents
    
    Returns:
        User ID if upload allowed
        
    Raises:
        HTTPException if limit reached
    """
    try:
        can_upload = await subscription_service.enforce_usage_limits(user_id, 'upload_document')
        
        if not can_upload:
            access = await subscription_service.check_user_access(user_id)
            max_docs = access.get('max_documents', 0)
            current_docs = access.get('documents_uploaded', 0)
            
            raise HTTPException(
                status_code=403,
                detail=f"Document upload limit reached ({current_docs}/{max_docs}). Please upgrade your subscription."
            )
        
        return user_id
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload limit check failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to check upload limit")


async def check_question_limit(user_id: str = Depends(get_current_user)) -> str:
    """
    Check if user can ask questions
    
    Returns:
        User ID if questions allowed
        
    Raises:
        HTTPException if limit reached
    """
    try:
        can_ask = await subscription_service.enforce_usage_limits(user_id, 'ask_question')
        
        if not can_ask:
            access = await subscription_service.check_user_access(user_id)
            max_questions = access.get('max_questions', 0)
            current_questions = access.get('questions_asked', 0)
            
            raise HTTPException(
                status_code=403,
                detail=f"Question limit reached ({current_questions}/{max_questions}). Please upgrade your subscription."
            )
        
        return user_id
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Question limit check failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to check question limit")


async def check_admin_access(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """
    Check if user is an admin (profile.role in admin/engineer/inspector, or email in allowlist).
    
    Returns:
        User ID if admin
        
    Raises:
        HTTPException if not admin
    """
    try:
        supabase = get_supabase_client()
        token = credentials.credentials

        # Verify token and get user (offloaded to thread pool — sync HTTP under the hood)
        user_response = await run_blocking(supabase.auth.get_user, token)

        if not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid token")

        user_id = user_response.user.id
        user_email = (user_response.user.email or "").strip().lower()

        # Allowlist: same emails the frontend treats as admin (REACT_APP_ADMIN_EMAIL_ALLOWLIST)
        allowlist = get_admin_email_allowlist()
        if user_email and user_email in allowlist:
            return user_id

        profile_response = await run_blocking(
            lambda: supabase.table('profiles')
            .select('role')
            .eq('id', user_id)
            .single()
            .execute()
        )

        if not profile_response.data or profile_response.data.get('role') not in ['admin', 'engineer', 'inspector']:
            raise HTTPException(status_code=403, detail="Admin access required")

        return user_id

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin check failed: {str(e)}")
        raise HTTPException(status_code=403, detail="Admin access required")

# check_company_admin function removed - company features no longer supported

