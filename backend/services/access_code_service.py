"""
Access Code Service - Manages access codes for free trials and promotions
"""
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import secrets
import string
from services.supabase_client import get_supabase_client
import logging

logger = logging.getLogger(__name__)

class AccessCodeService:
    """Service for managing access codes"""
    
    def __init__(self):
        self.supabase = get_supabase_client()
    
    def generate_code(self, length: int = 12) -> str:
        """Generate a random access code"""
        characters = string.ascii_uppercase + string.digits
        code = ''.join(secrets.choice(characters) for _ in range(length))
        return code
    
    async def create_access_code(
        self,
        access_type: str = 'trial',
        duration_days: int = 7,
        max_questions: int = 20,
        max_documents: int = 1,
        max_uses: int = 1,
        valid_until: Optional[datetime] = None,
        description: Optional[str] = None,
        created_by: Optional[str] = None
    ) -> Dict:
        """
        Create a new access code
        
        Args:
            access_type: trial, promotional, educational, or partner
            duration_days: How long access is granted
            max_questions: Maximum questions allowed
            max_documents: Maximum documents allowed
            max_uses: How many times the code can be used
            valid_until: Optional expiration date
            description: Optional description
            created_by: User ID who created the code
            
        Returns:
            Created access code record
        """
        try:
            # Generate unique code
            code = self.generate_code()
            
            # Ensure code is unique
            while await self.get_access_code_by_code(code):
                code = self.generate_code()
            
            code_data = {
                'code': code,
                'access_type': access_type,
                'duration_days': duration_days,
                'max_questions': max_questions,
                'max_documents': max_documents,
                'max_uses': max_uses,
                'times_used': 0,
                'valid_from': datetime.utcnow().isoformat(),
                'valid_until': valid_until.isoformat() if valid_until else None,
                'is_active': True,
                'description': description,
                'created_by': created_by,
            }
            
            result = self.supabase.table('access_codes').insert(code_data).execute()
            
            if not result.data:
                raise Exception("Failed to create access code")
            
            logger.info(f"Created access code: {code}")
            return result.data[0]
            
        except Exception as e:
            logger.error(f"Failed to create access code: {str(e)}")
            raise
    
    async def get_access_code(self, code_id: str) -> Optional[Dict]:
        """Get access code by ID"""
        try:
            result = self.supabase.table('access_codes')\
                .select('*')\
                .eq('id', code_id)\
                .single()\
                .execute()
            
            return result.data if result.data else None
            
        except Exception as e:
            logger.error(f"Failed to get access code: {str(e)}")
            return None
    
    async def get_access_code_by_code(self, code: str) -> Optional[Dict]:
        """Get access code by code string"""
        try:
            result = self.supabase.table('access_codes')\
                .select('*')\
                .eq('code', code)\
                .single()\
                .execute()
            
            return result.data if result.data else None
            
        except Exception as e:
            logger.error(f"Failed to get access code by string: {str(e)}")
            return None
    
    async def validate_access_code(self, code: str) -> Dict:
        """
        Validate if an access code can be used
        
        Returns:
            Dict with validation result:
            {
                'valid': bool,
                'reason': str (if invalid),
                'code_data': dict (if valid)
            }
        """
        try:
            access_code = await self.get_access_code_by_code(code)
            
            if not access_code:
                return {'valid': False, 'reason': 'Code not found'}
            
            if not access_code.get('is_active'):
                return {'valid': False, 'reason': 'Code is inactive'}
            
            # Check if code has expired
            if access_code.get('valid_until'):
                valid_until = datetime.fromisoformat(access_code['valid_until'])
                if datetime.utcnow() > valid_until:
                    return {'valid': False, 'reason': 'Code has expired'}
            
            # Check if code has reached max uses
            if access_code.get('times_used', 0) >= access_code.get('max_uses', 1):
                return {'valid': False, 'reason': 'Code has reached maximum uses'}
            
            return {'valid': True, 'code_data': access_code}
            
        except Exception as e:
            logger.error(f"Failed to validate access code: {str(e)}")
            return {'valid': False, 'reason': 'Validation error'}
    
    async def redeem_access_code(
        self,
        code: str,
        user_id: str
    ) -> Dict:
        """
        Redeem an access code for a user via SECURITY DEFINER RPC (row-locked, no table enumeration).
        """
        try:
            code = (code or "").strip()
            if not code:
                return {'success': False, 'message': 'Code is required'}

            try:
                result = self.supabase.rpc(
                    'redeem_access_code',
                    {'p_code': code, 'p_user_id': user_id},
                ).execute()
            except Exception as exc:
                msg = str(exc)
                if hasattr(exc, 'message') and exc.message:
                    msg = str(exc.message)
                elif getattr(exc, 'args', None):
                    msg = str(exc.args[0])
                logger.warning("Access code redeem failed for user %s: %s", user_id, msg)
                return {'success': False, 'message': msg.split('\n')[-1].strip() or 'Failed to redeem code'}

            data = result.data
            if data is None:
                return {'success': False, 'message': 'Failed to redeem code'}
            if isinstance(data, list):
                data = data[0] if data else None
            if not isinstance(data, dict) or not data.get('success'):
                return {'success': False, 'message': 'Failed to redeem code'}

            from services.subscription_service import subscription_service
            subscription_service.invalidate_cache(user_id)

            logger.info("User %s redeemed access code", user_id)

            return {
                'success': True,
                'message': data.get('message', 'Access granted'),
                'redemption': {'id': data.get('redemption_id')},
                'access_expires_at': data.get('access_expires_at'),
                'max_questions': data.get('max_questions'),
                'max_documents': data.get('max_documents'),
            }

        except Exception as e:
            logger.error(f"Failed to redeem access code: {str(e)}")
            return {'success': False, 'message': 'Failed to redeem code'}
    
    async def get_user_redemptions(self, user_id: str) -> List[Dict]:
        """Get all access code redemptions for a user"""
        try:
            result = self.supabase.table('access_code_redemptions')\
                .select('*, access_codes!inner(*)')\
                .eq('user_id', user_id)\
                .order('redeemed_at', desc=True)\
                .execute()
            
            return result.data if result.data else []
            
        except Exception as e:
            logger.error(f"Failed to get user redemptions: {str(e)}")
            return []
    
    async def get_active_redemption(self, user_id: str) -> Optional[Dict]:
        """Get active redemption for a user"""
        try:
            result = self.supabase.table('access_code_redemptions')\
                .select('*, access_codes!inner(*)')\
                .eq('user_id', user_id)\
                .eq('is_active', True)\
                .eq('expired', False)\
                .gte('access_granted_until', datetime.utcnow().isoformat())\
                .single()\
                .execute()
            
            return result.data if result.data else None
            
        except Exception as e:
            logger.error(f"Failed to get active redemption: {str(e)}")
            return None
    
    async def deactivate_code(self, code_id: str) -> bool:
        """Deactivate an access code"""
        try:
            self.supabase.table('access_codes')\
                .update({'is_active': False})\
                .eq('id', code_id)\
                .execute()
            
            logger.info(f"Deactivated access code: {code_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to deactivate access code: {str(e)}")
            return False
    
    async def expire_old_redemptions(self) -> int:
        """
        Expire old redemptions that have passed their access_granted_until date
        
        Returns:
            Number of redemptions expired
        """
        try:
            # Use the database function
            result = self.supabase.rpc('expire_old_access_code_redemptions').execute()
            
            if result.data:
                expired_count = result.data
                logger.info(f"Expired {expired_count} old redemptions")
                return expired_count
            
            return 0
            
        except Exception as e:
            logger.error(f"Failed to expire old redemptions: {str(e)}")
            return 0
    
    async def list_access_codes(
        self,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict]:
        """List all access codes (admin function)"""
        try:
            query = self.supabase.table('access_codes')\
                .select('*')
            
            if active_only:
                query = query.eq('is_active', True)
            
            result = query\
                .order('created_at', desc=True)\
                .range(offset, offset + limit - 1)\
                .execute()
            
            return result.data if result.data else []
            
        except Exception as e:
            logger.error(f"Failed to list access codes: {str(e)}")
            return []
    
    async def get_code_redemption_stats(self, code_id: str) -> Dict:
        """Get statistics for an access code"""
        try:
            # Get code info
            code = await self.get_access_code(code_id)
            if not code:
                return {}
            
            # Get all redemptions
            redemptions = self.supabase.table('access_code_redemptions')\
                .select('*')\
                .eq('access_code_id', code_id)\
                .execute()
            
            total_redemptions = len(redemptions.data) if redemptions.data else 0
            active_redemptions = sum(1 for r in redemptions.data if r.get('is_active', False)) if redemptions.data else 0
            expired_redemptions = sum(1 for r in redemptions.data if r.get('expired', False)) if redemptions.data else 0
            
            return {
                'code': code['code'],
                'total_redemptions': total_redemptions,
                'active_redemptions': active_redemptions,
                'expired_redemptions': expired_redemptions,
                'times_used': code.get('times_used', 0),
                'max_uses': code.get('max_uses', 1),
                'is_active': code.get('is_active', False),
            }
            
        except Exception as e:
            logger.error(f"Failed to get code redemption stats: {str(e)}")
            return {}


# Global instance
access_code_service = AccessCodeService()

