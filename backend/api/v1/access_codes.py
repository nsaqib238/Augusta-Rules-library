"""
Access Code API Endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from services.access_code_service import access_code_service
from middleware.subscription_check import get_current_user, check_admin_access
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Request/Response Models
class RedeemCodeRequest(BaseModel):
    code: str

class RedeemCodeResponse(BaseModel):
    success: bool
    message: str
    access_expires_at: Optional[str]
    max_questions: Optional[int]
    max_documents: Optional[int]

class CreateAccessCodeRequest(BaseModel):
    access_type: str = 'trial'
    duration_days: int = 7
    max_questions: int = 20
    max_documents: int = 1
    max_uses: int = 1
    valid_until: Optional[str] = None
    description: Optional[str] = None

class AccessCodeResponse(BaseModel):
    id: str
    code: str
    access_type: str
    duration_days: int
    max_questions: int
    max_documents: int
    max_uses: int
    times_used: int
    is_active: bool
    created_at: str

class RedemptionResponse(BaseModel):
    id: str
    code: str
    redeemed_at: str
    access_granted_until: str
    questions_used: int
    documents_used: int
    is_active: bool
    expired: bool


@router.post("/redeem", response_model=RedeemCodeResponse)
async def redeem_access_code(
    request: RedeemCodeRequest,
    current_user: str = Depends(get_current_user)
):
    """
    Redeem an access code
    """
    try:
        result = await access_code_service.redeem_access_code(
            code=request.code,
            user_id=current_user
        )
        
        if not result['success']:
            raise HTTPException(status_code=400, detail=result['message'])
        
        return RedeemCodeResponse(
            success=True,
            message=result['message'],
            access_expires_at=result.get('access_expires_at'),
            max_questions=result.get('max_questions'),
            max_documents=result.get('max_documents')
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to redeem access code: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate")
async def validate_access_code(
    code: str,
    current_user: str = Depends(get_current_user)
):
    """
    Validate an access code without redeeming it
    """
    try:
        validation = await access_code_service.validate_access_code(code)
        
        return {
            "valid": validation['valid'],
            "reason": validation.get('reason'),
            "details": validation.get('code_data') if validation['valid'] else None
        }
        
    except Exception as e:
        logger.error(f"Failed to validate access code: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-redemptions", response_model=List[RedemptionResponse])
async def get_my_redemptions(current_user: str = Depends(get_current_user)):
    """
    Get all access code redemptions for current user
    """
    try:
        redemptions = await access_code_service.get_user_redemptions(current_user)
        
        return [
            RedemptionResponse(
                id=r['id'],
                code=r.get('access_codes', {}).get('code', 'N/A'),
                redeemed_at=r['redeemed_at'],
                access_granted_until=r['access_granted_until'],
                questions_used=r['questions_used'],
                documents_used=r['documents_used'],
                is_active=r['is_active'],
                expired=r['expired']
            )
            for r in redemptions
        ]
        
    except Exception as e:
        logger.error(f"Failed to get redemptions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# Admin endpoints
@router.post("/admin/generate", response_model=AccessCodeResponse)
async def generate_access_code(
    request: CreateAccessCodeRequest,
    admin_user: str = Depends(check_admin_access)
):
    """
    Generate a new access code (admin only)
    """
    try:
        valid_until = None
        if request.valid_until:
            valid_until = datetime.fromisoformat(request.valid_until)
        
        code = await access_code_service.create_access_code(
            access_type=request.access_type,
            duration_days=request.duration_days,
            max_questions=request.max_questions,
            max_documents=request.max_documents,
            max_uses=request.max_uses,
            valid_until=valid_until,
            description=request.description,
            created_by=admin_user
        )
        
        return AccessCodeResponse(
            id=code['id'],
            code=code['code'],
            access_type=code['access_type'],
            duration_days=code['duration_days'],
            max_questions=code['max_questions'],
            max_documents=code['max_documents'],
            max_uses=code['max_uses'],
            times_used=code['times_used'],
            is_active=code['is_active'],
            created_at=code['created_at']
        )
        
    except Exception as e:
        logger.error(f"Failed to generate access code: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/list", response_model=List[AccessCodeResponse])
async def list_access_codes(
    active_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin_user: str = Depends(check_admin_access)
):
    """
    List all access codes (admin only)
    """
    try:
        codes = await access_code_service.list_access_codes(
            active_only=active_only,
            limit=limit,
            offset=offset
        )
        
        return [
            AccessCodeResponse(
                id=code['id'],
                code=code['code'],
                access_type=code['access_type'],
                duration_days=code['duration_days'],
                max_questions=code['max_questions'],
                max_documents=code['max_documents'],
                max_uses=code['max_uses'],
                times_used=code['times_used'],
                is_active=code['is_active'],
                created_at=code['created_at']
            )
            for code in codes
        ]
        
    except Exception as e:
        logger.error(f"Failed to list access codes: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/admin/{code_id}/deactivate")
async def deactivate_access_code(
    code_id: str,
    admin_user: str = Depends(check_admin_access)
):
    """
    Deactivate an access code (admin only)
    """
    try:
        success = await access_code_service.deactivate_code(code_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Access code not found")
        
        return {"message": "Access code deactivated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to deactivate access code: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/{code_id}/stats")
async def get_code_stats(
    code_id: str,
    admin_user: str = Depends(check_admin_access)
):
    """
    Get statistics for an access code (admin only)
    """
    try:
        stats = await access_code_service.get_code_redemption_stats(code_id)
        
        if not stats:
            raise HTTPException(status_code=404, detail="Access code not found")
        
        return stats
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get code stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/expire-old-redemptions")
async def expire_old_redemptions(admin_user: str = Depends(check_admin_access)):
    """
    Manually trigger expiration of old redemptions (admin only)
    """
    try:
        count = await access_code_service.expire_old_redemptions()
        
        return {
            "message": f"Expired {count} old redemptions",
            "count": count
        }
        
    except Exception as e:
        logger.error(f"Failed to expire redemptions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

