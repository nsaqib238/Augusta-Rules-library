"""
Subscription Service - Manages subscription logic and access control
"""
from typing import Optional, Dict, List
from datetime import datetime, timedelta, timezone
from services.supabase_client import get_supabase_client
from services.stripe_service import stripe_service
from services.async_utils import run_blocking
import logging
import time

logger = logging.getLogger(__name__)

# Two plans only: sole (NCC + SIR) | professional (all)
DEFAULT_PLAN = {
    'plan_name': 'sole',
    'display_name': 'Sole',
    'max_documents': 0,
    'max_questions': None,
    'subscription_status': 'active',
}

PLAN_DISPLAY_NAMES = {
    'sole': 'Sole',
    'professional': 'Professional',
}

PLAN_LIMITS = {
    'sole': {
        'max_documents': 0,
        'max_questions': None,
        'unlimited_documents': False,
        'unlimited_questions': True,
    },
    'professional': {
        'max_documents': None,
        'max_questions': None,
        'unlimited_documents': True,
        'unlimited_questions': True,
    },
}

class SubscriptionService:
    """Service for managing subscriptions and access control"""
    
    def __init__(self):
        self.supabase = get_supabase_client()
        # Cache for subscription lookups to avoid hitting Stripe repeatedly
        # Format: {user_id: (subscription_data, timestamp)}
        self._subscription_cache: Dict[str, tuple] = {}
        self._cache_ttl = 180  # 3 minutes TTL (configurable: 60-300 seconds)

    def _get_cached_subscription(self, user_id: str) -> Optional[Dict]:
        """Get subscription from cache if still valid."""
        if user_id in self._subscription_cache:
            subscription_data, cached_time = self._subscription_cache[user_id]
            age = time.time() - cached_time
            if age < self._cache_ttl:
                logger.debug(f"Using cached subscription for user {user_id} (age: {age:.1f}s)")
                return subscription_data
            else:
                # Cache expired, remove it
                del self._subscription_cache[user_id]
                logger.debug(f"Cache expired for user {user_id} (age: {age:.1f}s > TTL: {self._cache_ttl}s)")
        return None
    
    def _cache_subscription(self, user_id: str, subscription: Optional[Dict]):
        """Cache subscription data with current timestamp."""
        self._subscription_cache[user_id] = (subscription, time.time())
        logger.debug(f"Cached subscription for user {user_id} (TTL: {self._cache_ttl}s)")
    
    def invalidate_cache(self, user_id: Optional[str] = None):
        """Invalidate subscription cache for a specific user or all users."""
        if user_id:
            if user_id in self._subscription_cache:
                del self._subscription_cache[user_id]
                logger.debug(f"Invalidated cache for user {user_id}")
        else:
            self._subscription_cache.clear()
            logger.debug("Invalidated all subscription caches")

    @staticmethod
    def _result_data(result) -> Optional[Dict]:
        """Safely read .data from a Supabase response (execute() may return None)."""
        if result is None:
            return None
        data = getattr(result, 'data', None)
        if isinstance(data, list):
            return data[0] if data else None
        return data

    def _fetch_profile_row(self, user_id: str) -> Optional[Dict]:
        """Load profile; tolerate DBs that have not yet added optional billing columns."""
        columns_full = (
            'id, email, full_name, account_type, subscription_status, documents_remaining, '
            'questions_remaining, access_expires_at, stripe_customer_id, subscription_source'
        )
        columns_min = (
            'id, email, full_name, account_type, subscription_status, documents_remaining, '
            'access_expires_at, stripe_customer_id, subscription_source'
        )
        last_exc: Optional[Exception] = None
        for cols in (columns_full, columns_min):
            try:
                return self._result_data(
                    self.supabase.table('profiles')
                    .select(cols)
                    .eq('id', user_id)
                    .maybe_single()
                    .execute()
                )
            except Exception as exc:
                last_exc = exc
                msg = str(exc)
                if '42703' in msg or 'does not exist' in msg.lower():
                    continue
                raise
        if last_exc:
            logger.warning('Profile fetch failed for %s: %s', user_id, last_exc)
        return None

    def ensure_profile(self, user_id: str) -> Optional[Dict]:
        """Return profiles row, creating one from auth.users if the signup trigger missed."""
        row = self._fetch_profile_row(user_id)
        if row:
            return row

        email = None
        full_name = None
        try:
            auth_resp = self.supabase.auth.admin.get_user_by_id(user_id)
            auth_user = getattr(auth_resp, 'user', None)
            if auth_user is None and isinstance(auth_resp, dict):
                auth_user = auth_resp.get('user')
            if auth_user is not None:
                email = getattr(auth_user, 'email', None) or (
                    auth_user.get('email') if isinstance(auth_user, dict) else None
                )
                meta = getattr(auth_user, 'user_metadata', None) or (
                    auth_user.get('user_metadata') if isinstance(auth_user, dict) else None
                ) or {}
                if isinstance(meta, dict):
                    full_name = meta.get('full_name') or meta.get('name')
        except Exception as exc:
            logger.warning('Could not load auth user for profile bootstrap %s: %s', user_id, exc)

        payload = {
            'id': user_id,
            'email': email,
            'full_name': full_name or (email.split('@')[0] if email else 'User'),
            'account_type': 'sole',
            'subscription_status': 'inactive',
            'documents_remaining': 0,
        }
        try:
            self.supabase.table('profiles').upsert(payload, on_conflict='id').execute()
            logger.info('Created missing profile row for user %s', user_id)
            return payload
        except Exception as exc:
            logger.error('Failed to create profile for user %s: %s', user_id, exc)
            return None

    def _resolve_plan_display_name(self, plan_name: Optional[str]) -> str:
        if not plan_name:
            return 'Unknown Plan'
        return PLAN_DISPLAY_NAMES.get(plan_name, plan_name.replace('_', ' ').title())

    def _activate_sole_plan(self, user_id: str, profile: Optional[Dict] = None) -> Dict:
        """Ensure the user has the Sole plan (NCC + SIR only)."""
        try:
            profile_data = profile or self.ensure_profile(user_id) or {}
            if not profile_data:
                profile_data = {}

            max_docs = DEFAULT_PLAN['max_documents']
            max_questions = DEFAULT_PLAN['max_questions']
            documents_remaining = profile_data.get('documents_remaining')
            if documents_remaining is None or documents_remaining < 0 or (max_docs is not None and documents_remaining > max_docs):
                documents_remaining = max_docs
            questions_remaining = None if max_questions is None else (profile_data.get('questions_remaining') or 0)
            if max_questions is not None and (questions_remaining is None or questions_remaining < 0):
                questions_remaining = max_questions

            updates = {}

            if profile_data.get('documents_remaining') != documents_remaining:
                updates['documents_remaining'] = documents_remaining

            if 'questions_remaining' in profile_data and profile_data.get('questions_remaining') != questions_remaining:
                updates['questions_remaining'] = questions_remaining

            if profile_data.get('account_type') != 'sole':
                updates['account_type'] = 'sole'

            if profile_data.get('subscription_status') != DEFAULT_PLAN['subscription_status']:
                updates['subscription_status'] = DEFAULT_PLAN['subscription_status']

            if updates:
                try:
                    self.supabase.table('profiles').update(updates).eq('id', user_id).execute()
                except Exception as update_exc:
                    logger.warning(f"Could not persist sole plan for user {user_id}: {str(update_exc)}")

            documents_used = max(0, (max_docs or 0) - (documents_remaining or 0)) if max_docs is not None else 0
            questions_used = 0 if max_questions is None else max(0, (max_questions or 0) - (questions_remaining or 0))

            return {
                'has_access': True,
                'account_type': 'sole',
                'plan_name': 'sole',
                'plan_display_name': DEFAULT_PLAN['display_name'],
                'subscription_status': DEFAULT_PLAN['subscription_status'],
                'max_documents': max_docs,
                'max_questions': max_questions,
                'documents_uploaded': documents_used,
                'questions_asked': questions_used,
                'access_expires_at': None,
                'subscription_source': None,
            }

        except Exception as exc:
            logger.error(f"Failed to activate sole plan for user {user_id}: {str(exc)}")
            return {
                'has_access': True,
                'account_type': 'sole',
                'plan_name': 'sole',
                'plan_display_name': DEFAULT_PLAN['display_name'],
                'subscription_status': DEFAULT_PLAN['subscription_status'],
                'max_documents': DEFAULT_PLAN['max_documents'],
                'max_questions': DEFAULT_PLAN['max_questions'],
                'documents_uploaded': 0,
                'questions_asked': 0,
                'access_expires_at': None,
                'subscription_source': None,
            }

    def _decrement_free_plan_allowance(self, user_id: str, action_type: str):
        """Reduce remaining allowances for the free plan when usage occurs"""
        try:
            result = None
            for cols in ('documents_remaining, questions_remaining', 'documents_remaining'):
                try:
                    result = self.supabase.table('profiles')\
                        .select(cols)\
                        .eq('id', user_id)\
                        .maybe_single()\
                        .execute()
                    break
                except Exception as exc:
                    if '42703' in str(exc) or 'does not exist' in str(exc).lower():
                        continue
                    raise

            profile = self._result_data(result)
            if not profile:
                return

            updates = {}
            if action_type == 'documents':
                remaining = profile.get('documents_remaining', DEFAULT_PLAN['max_documents'])
                remaining = max(0, (remaining if remaining is not None else DEFAULT_PLAN['max_documents']) - 1)
                updates['documents_remaining'] = remaining
            elif action_type == 'questions' and 'questions_remaining' in profile:
                remaining = profile.get('questions_remaining', DEFAULT_PLAN['max_questions'])
                remaining = max(0, (remaining if remaining is not None else DEFAULT_PLAN['max_questions']) - 1)
                updates['questions_remaining'] = remaining

            if updates:
                self.supabase.table('profiles')\
                    .update(updates)\
                    .eq('id', user_id)\
                    .execute()

        except Exception as exc:
            logger.error(f"Failed to decrement free plan allowance for user {user_id}: {str(exc)}")
    
    async def create_individual_subscription(
        self,
        user_id: str,
        plan_name: str,
        stripe_customer_id: str,
        stripe_subscription_id: str,
        plan_price: float,
        current_period_start: datetime,
        current_period_end: datetime
    ) -> Dict:
        """
        Create an individual subscription record (all subscriptions are now individual)
        
        Args:
            user_id: User UUID
            plan_name: Plan name ('individual_monthly' or 'professional')
            stripe_customer_id: Stripe customer ID
            stripe_subscription_id: Stripe subscription ID
            plan_price: Price in AUD
            current_period_start: Billing period start
            current_period_end: Billing period end
            
        Returns:
            Created subscription record
        """
        try:
            # Determine usage limits based on plan
            plan_limits = PLAN_LIMITS.get(plan_name, {})
            max_documents = plan_limits.get('max_documents')
            max_questions = plan_limits.get('max_questions')
            
            # All subscriptions are now 'individual' type (including Professional)
            subscription_data = {
                'user_id': user_id,
                'subscription_type': 'individual',  # Always individual now
                'plan_name': plan_name,  # 'individual_monthly' or 'professional'
                'plan_price': plan_price,
                'currency': 'AUD',
                'stripe_customer_id': stripe_customer_id,
                'stripe_subscription_id': stripe_subscription_id,
                'status': 'active',
                'current_period_start': current_period_start.isoformat(),
                'current_period_end': current_period_end.isoformat(),
                'max_documents': max_documents,
                'max_questions': max_questions,
                'documents_uploaded': 0,
                'questions_asked': 0,
            }
            
            existing = self.supabase.table('user_subscriptions')\
                .select('id')\
                .eq('stripe_subscription_id', stripe_subscription_id)\
                .limit(1)\
                .execute()

            if existing.data:
                subscription_id = existing.data[0]['id']
                self.supabase.table('user_subscriptions')\
                    .update(subscription_data)\
                    .eq('id', subscription_id)\
                    .execute()
                result = self.supabase.table('user_subscriptions')\
                    .select('*')\
                    .eq('id', subscription_id)\
                    .single()\
                    .execute()
            else:
                result = self.supabase.table('user_subscriptions')\
                    .insert(subscription_data)\
                    .execute()
            
            # Ensure stripe_customer_id is saved to profile
            profile_check = self.supabase.table('profiles')\
                .select('stripe_customer_id')\
                .eq('id', user_id)\
                .single()\
                .execute()
            
            if profile_check.data:
                existing_customer_id = profile_check.data.get('stripe_customer_id')
                if not existing_customer_id or existing_customer_id != stripe_customer_id:
                    self.supabase.table('profiles')\
                        .update({'stripe_customer_id': stripe_customer_id})\
                        .eq('id', user_id)\
                        .execute()
                    # SECURITY: Don't log Stripe customer IDs in production
                    logger.debug(f"Updated stripe_customer_id in profile for user {user_id} to {stripe_customer_id}")
                    logger.info(f"Updated stripe_customer_id in profile for user {user_id}")
            
            # Only professional is paid; update profile so check_user_access uses it
            account_type = 'professional'
            await self.update_user_profile_subscription(
                user_id,
                account_type=account_type,
                subscription_status='active',
                access_expires_at=current_period_end,
                stripe_subscription_id=stripe_subscription_id,
                subscription_source='stripe',
            )
            
            logger.info(f"Created subscription for user {user_id}: plan={plan_name}")
            return result.data[0] if result.data else None
            
        except Exception as e:
            logger.error(f"Failed to create subscription: {str(e)}")
            raise
    
    # DEPRECATED: Company subscriptions removed. All subscriptions are now individual.
    # Professional plan is treated as an individual subscription with unlimited documents.
    async def create_company_subscription(
        self,
        company_id: str,
        plan_name: str,
        stripe_customer_id: str,
        stripe_subscription_id: str,
        plan_price: float,
        current_period_start: datetime,
        current_period_end: datetime
    ) -> Dict:
        """
        DEPRECATED: Company subscriptions are no longer supported.
        This method is kept for backwards compatibility but redirects to individual subscription.
        All subscriptions (including Professional) are now individual subscriptions.
        """
        logger.warning(f"create_company_subscription called - redirecting to individual subscription. plan_name={plan_name}")
        # Treat all subscriptions as individual now (including Professional)
        # The plan_name determines the limits, not subscription_type
        return await self.create_individual_subscription(
            user_id=company_id,  # In the simplified model, company_id would be the user_id
            plan_name=plan_name,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            plan_price=plan_price,
            current_period_start=current_period_start,
            current_period_end=current_period_end
        )
    
    def _get_cached_subscription(self, user_id: str) -> Optional[Dict]:
        """Get subscription from cache if still valid."""
        if user_id in self._subscription_cache:
            subscription_data, cached_time = self._subscription_cache[user_id]
            age = time.time() - cached_time
            if age < self._cache_ttl:
                logger.debug(f"Using cached subscription for user {user_id} (age: {age:.1f}s)")
                return subscription_data
            else:
                # Cache expired, remove it
                del self._subscription_cache[user_id]
                logger.debug(f"Cache expired for user {user_id} (age: {age:.1f}s > TTL: {self._cache_ttl}s)")
        return None
    
    def _cache_subscription(self, user_id: str, subscription: Optional[Dict]):
        """Cache subscription data with current timestamp."""
        self._subscription_cache[user_id] = (subscription, time.time())
        logger.debug(f"Cached subscription for user {user_id} (TTL: {self._cache_ttl}s)")
    
    def invalidate_cache(self, user_id: Optional[str] = None):
        """Invalidate subscription cache for a specific user or all users."""
        if user_id:
            if user_id in self._subscription_cache:
                del self._subscription_cache[user_id]
                logger.debug(f"Invalidated cache for user {user_id}")
        else:
            self._subscription_cache.clear()
            logger.debug("Invalidated all subscription caches")
    
    async def get_user_subscription(self, user_id: str, include_stripe_status: bool = False) -> Optional[Dict]:
        """
        Get active subscription for a user.
        
        PERFORMANCE: Trusts database first, only falls back to Stripe if:
        - Subscription not in DB AND
        - Not in cache (to avoid repeated Stripe calls)
        - include_stripe_status=True (explicitly requested)
        
        Args:
            user_id: User UUID
            include_stripe_status: If True, enhances DB subscription with Stripe status or falls back to Stripe if not in DB.
                                  Default False to prioritize DB and reduce Stripe API calls.
        
        Returns:
            Subscription dict or None
        """
        try:
            # Check cache first (fastest path)
            cached_subscription = self._get_cached_subscription(user_id)
            if cached_subscription is not None:
                logger.debug(f"Returning cached subscription for user {user_id}")
                return cached_subscription
            
            # Valid subscription statuses (not cancelled/expired)
            valid_statuses = ['active', 'trialing', 'past_due', 'incomplete', 'incomplete_expired', 'unpaid']
            
            # PRIMARY: Check database first (trust DB - webhooks should populate it).
            # supabase-py is sync — offload to a thread so we don't freeze the loop.
            result = await run_blocking(
                lambda: self.supabase.table('user_subscriptions')
                .select('*')
                .eq('user_id', user_id)
                .in_('status', valid_statuses)
                .order('created_at', desc=True)
                .limit(1)
                .execute()
            )
            
            subscription = None
            if result.data:
                subscription = result.data[0]
                logger.info(f"Found subscription in database for user {user_id}: status={subscription.get('status')}, stripe_id={subscription.get('stripe_subscription_id')}")
                # Cache DB result immediately (trust DB first)
                self._cache_subscription(user_id, subscription)
                
                # If explicitly requested, enhance with Stripe status (non-blocking, short timeout)
                if include_stripe_status:
                    subscription = await self._enhance_with_stripe_status(subscription, user_id)
                
                return subscription
            
            # FALLBACK: Only check Stripe if:
            # 1. Not in DB
            # 2. Not in cache (already checked Stripe recently)
            # 3. Explicitly requested (include_stripe_status=True)
            if include_stripe_status:
                logger.warning(
                    f"⚠️  No subscription in database for user {user_id}, checking Stripe as fallback... "
                    f"(This indicates webhooks may not be writing to database - check webhook configuration)"
                )
                try:
                    import asyncio
                    # Add timeout for Stripe fetch (5 seconds max)
                    subscription = await asyncio.wait_for(
                        self._get_subscription_from_stripe_by_user(user_id),
                        timeout=5.0
                    )
                    if subscription:
                        logger.info(f"Fetched subscription from Stripe for user {user_id} (webhook should have written this to DB)")
                        # Cache the Stripe result to avoid repeated calls
                        self._cache_subscription(user_id, subscription)
                        return subscription
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout fetching subscription from Stripe for user {user_id} (5s)")
                    # Cache None with shorter TTL to allow retry sooner
                    self._cache_subscription(user_id, None)
                    return None
                except Exception as stripe_fetch_error:
                    logger.error(f"Failed to fetch subscription from Stripe (non-fatal): {str(stripe_fetch_error)}", exc_info=True)
                    # Cache None to avoid repeated failed attempts
                    self._cache_subscription(user_id, None)
                    return None
            
            # No subscription found in DB and Stripe fallback not requested
            logger.debug(f"No subscription found for user {user_id} (DB only, Stripe not checked)")
            # Cache None to avoid repeated DB queries
            self._cache_subscription(user_id, None)
            return None
            
        except Exception as e:
            logger.error(f"Failed to get user subscription: {str(e)}", exc_info=True)
            return None
    
    async def _enhance_with_stripe_status(self, subscription: Dict, user_id: str) -> Dict:
        """Enhance DB subscription with real-time Stripe status (non-blocking, short timeout)."""
        stripe_subscription_id = subscription.get('stripe_subscription_id')
        if not stripe_subscription_id:
            return subscription
        
        try:
            import asyncio
            # Non-blocking enhancement with short timeout (3 seconds)
            stripe_subscription = await asyncio.wait_for(
                stripe_service.get_subscription(stripe_subscription_id),
                timeout=3.0
            )
            if stripe_subscription:
                # Update subscription with latest Stripe status (StripeObject or dict)
                try:
                    subscription['stripe_status'] = stripe_subscription['status']
                    subscription['stripe_current_period_end'] = stripe_subscription.get(
                        'current_period_end'
                    ) if isinstance(stripe_subscription, dict) else stripe_subscription['current_period_end']
                except (KeyError, TypeError):
                    subscription['stripe_status'] = getattr(stripe_subscription, 'status', None)
                    subscription['stripe_current_period_end'] = getattr(
                        stripe_subscription, 'current_period_end', None
                    )
                # Prefer live Stripe status for access decisions
                if subscription.get('stripe_status'):
                    subscription['status'] = subscription['stripe_status']
                logger.debug(f"Enhanced subscription with Stripe status for user {user_id}")
        except asyncio.TimeoutError:
            logger.debug(f"Timeout enhancing subscription with Stripe status for user {user_id} (non-fatal)")
        except Exception as e:
            logger.debug(f"Failed to enhance subscription with Stripe status (non-fatal): {str(e)}")
        
        return subscription
    async def get_user_subscription_legacy(self, user_id: str, include_stripe_status: bool = True) -> Optional[Dict]:
        """Legacy method - kept for backward compatibility. Use get_user_subscription() instead."""
        return await self.get_user_subscription(user_id, include_stripe_status=include_stripe_status)
    
    async def _get_subscription_from_stripe_by_user(self, user_id: str) -> Optional[Dict]:
        """Fetch subscription directly from Stripe using user's Stripe customer ID"""
        try:
            profile = self.ensure_profile(user_id)
            if not profile:
                logger.warning(f"Profile not found for user {user_id}")
                return None

            stripe_customer_id = profile.get('stripe_customer_id')
            user_email = profile.get('email')
            
            if not stripe_customer_id:
                logger.warning(f"No Stripe customer ID found for user {user_id} (email: {user_email})")
                # Try to find customer by email as fallback
                if user_email:
                    logger.info(f"Attempting to find Stripe customer by email: {user_email}")
                    try:
                        import stripe
                        customers = stripe.Customer.list(email=user_email, limit=1)
                        if customers.data:
                            stripe_customer_id = customers.data[0].id
                            # SECURITY: Don't log Stripe customer IDs in production
                            logger.debug(f"Found Stripe customer by email: {stripe_customer_id}")
                            logger.info(f"Found Stripe customer by email")
                            # Update profile with customer ID
                            self.supabase.table('profiles')\
                                .update({'stripe_customer_id': stripe_customer_id})\
                                .eq('id', user_id)\
                                .execute()
                        else:
                            logger.info(f"No Stripe customer found with email {user_email}")
                            return None
                    except Exception as e:
                        logger.error(f"Failed to search for customer by email: {str(e)}")
                        return None
                else:
                    return None
            
            # SECURITY: Don't log Stripe customer IDs in production
            logger.debug(f"Found Stripe customer ID {stripe_customer_id} for user {user_id}, fetching subscriptions from Stripe...")
            logger.info(f"Found Stripe customer for user {user_id}, fetching subscriptions from Stripe...")
            
            # Get customer from Stripe to verify it exists
            try:
                customer = await stripe_service.get_customer(stripe_customer_id)
                logger.debug(f"Verified Stripe customer exists: {customer.id}")
                logger.info(f"Verified Stripe customer exists")
            except Exception as e:
                logger.error(f"Failed to retrieve Stripe customer: {str(e)}")
                logger.debug(f"Failed to retrieve Stripe customer {stripe_customer_id}: {str(e)}")
                return None
            
            # Get active subscriptions for this customer
            import stripe
            try:
                subscriptions = stripe.Subscription.list(
                    customer=stripe_customer_id,
                    status='all',  # Get all statuses
                    limit=10
                )
                logger.info(f"Found {len(subscriptions.data)} subscriptions in Stripe for customer {stripe_customer_id}")
                
                # Log all subscription statuses for debugging
                for sub in subscriptions.data:
                    logger.info(f"  - Subscription {sub.id}: status={sub.status}")
            except Exception as e:
                logger.error(f"Failed to list subscriptions from Stripe: {str(e)}")
                return None
            
            if subscriptions.data:
                # Get the most recent active/trialing subscription
                active_sub = None
                for sub in subscriptions.data:
                    if sub.status in ['active', 'trialing', 'past_due', 'incomplete']:
                        active_sub = sub
                        logger.info(f"Selected active subscription: {sub.id}, status={sub.status}")
                        break
                
                if not active_sub:
                    logger.info(
                        "No active/trialing subscription in Stripe for customer %s (canceled/expired subs ignored for access)",
                        stripe_customer_id,
                    )
                    return None

                if active_sub:
                    sub_id = active_sub.id
                    sub_status = active_sub.status
                    logger.info(f"Found subscription in Stripe: {sub_id}, status={sub_status}")
                    
                    # Retrieve full subscription object to get items (list doesn't expand items by default)
                    # Stripe objects support dict-style access
                    # Add timeout protection - if Stripe is slow, don't block the entire request
                    full_subscription = None
                    items = None
                    try:
                        import asyncio
                        # Set a timeout for Stripe API call (10 seconds)
                        full_subscription = await asyncio.wait_for(
                            stripe_service.get_subscription(sub_id),
                            timeout=10.0
                        )
                        logger.info(f"Retrieved full subscription: {sub_id}")
                        
                        # Access items using dict-style access (Stripe objects support this)
                        # Try dict access first (most reliable)
                        try:
                            items = full_subscription['items']['data']
                        except (KeyError, TypeError):
                            # Fallback to attribute access
                            try:
                                if hasattr(full_subscription, 'items'):
                                    items_obj = full_subscription.items
                                    if hasattr(items_obj, 'data'):
                                        items = items_obj.data
                                    elif hasattr(items_obj, '__getitem__'):
                                        items = items_obj['data']
                            except Exception as e:
                                logger.warning(f"Failed to access items via attribute: {str(e)}")
                        
                        if not items:
                            logger.warning(f"Could not access items from subscription {sub_id}")
                    except asyncio.TimeoutError:
                        logger.warning(f"Timeout retrieving full subscription {sub_id} from Stripe (10s)")
                        # Continue without full subscription - we can still return basic subscription info
                        items = None
                    except Exception as e:
                        logger.error(f"Failed to retrieve full subscription details: {str(e)}", exc_info=True)
                        items = None
                    
                    # Get plan name from price ID
                    price_id = None
                    plan_name = 'unknown'
                    if items and len(items) > 0:
                        try:
                            first_item = items[0]
                            # Access price using dict-style access
                            if isinstance(first_item, dict):
                                price_id = first_item.get('price', {}).get('id')
                            else:
                                # Try dict-style access on object
                                try:
                                    price_id = first_item['price']['id']
                                except (KeyError, TypeError):
                                    # Fallback to attribute access
                                    if hasattr(first_item, 'price'):
                                        price_obj = first_item.price
                                        if isinstance(price_obj, dict):
                                            price_id = price_obj.get('id')
                                        elif hasattr(price_obj, 'id'):
                                            price_id = price_obj.id
                                        else:
                                            try:
                                                price_id = price_obj['id']
                                            except (KeyError, TypeError):
                                                pass
                            
                            if price_id:
                                # Map price ID to plan name
                                plan_name = stripe_service.get_plan_name_for_price(price_id) or price_id
                                logger.info(f"Plan: {plan_name} (price_id: {price_id})")
                            else:
                                logger.warning(f"Could not extract price_id from subscription items")
                        except Exception as e:
                            logger.error(f"Error extracting price from items: {str(e)}", exc_info=True)
                    else:
                        logger.warning(f"No items found in subscription {sub_id}")
                    
                    # Get period dates from active_sub (list results have these fields)
                    period_start = None
                    period_end = None
                    try:
                        # Try dict-style access first (Stripe objects support this)
                        try:
                            period_start = active_sub['current_period_start']
                            period_end = active_sub['current_period_end']
                        except (KeyError, TypeError):
                            # Fallback to attribute access
                            period_start = getattr(active_sub, 'current_period_start', None)
                            period_end = getattr(active_sub, 'current_period_end', None)
                    except Exception as e:
                        logger.warning(f"Could not access period dates from subscription: {str(e)}")
                    
                    # Convert timestamps to ISO strings
                    period_start_iso = datetime.fromtimestamp(period_start).isoformat() if period_start else None
                    period_end_iso = datetime.fromtimestamp(period_end).isoformat() if period_end else None
                    
                    # Return a dict that looks like our database subscription
                    return {
                        'id': None,  # Not in database yet
                        'stripe_subscription_id': sub_id,
                        'stripe_customer_id': stripe_customer_id,
                        'user_id': user_id,
                        'subscription_type': 'individual',
                        'status': sub_status,
                        'plan_name': plan_name,
                        'current_period_start': period_start_iso,
                        'current_period_end': period_end_iso,
                        'stripe_status': sub_status,  # Will be set again below
                        'from_stripe': True  # Flag to indicate this came from Stripe, not database
                    }
            
            logger.info(f"No active subscriptions found in Stripe for customer {stripe_customer_id}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to get subscription from Stripe: {str(e)}", exc_info=True)
            return None
    
    async def _company_stripe_still_paid(self, company: Dict) -> bool:
        """
        True only if the company's Stripe subscription is still billable.
        Fail closed when there is no stripe_subscription_id (orphan active row).
        """
        import asyncio

        sub_id = (company.get("stripe_subscription_id") or "").strip()
        if not sub_id:
            logger.warning(
                "Company %s active but missing stripe_subscription_id — treating as unpaid",
                company.get("id"),
            )
            return False

        paid = {"active", "trialing", "past_due"}

        # Prefer Stripe (billing UI may show canceled while DB/company row lag)
        try:
            from services.stripe_service import stripe_service

            stripe_sub = await asyncio.wait_for(
                stripe_service.get_subscription(sub_id),
                timeout=4.0,
            )
            status = ""
            try:
                status = (stripe_sub["status"] or "").strip().lower()
            except (KeyError, TypeError):
                status = (getattr(stripe_sub, "status", None) or "").strip().lower()
            logger.info(
                "Company %s stripe sub %s status=%s",
                company.get("id"),
                sub_id,
                status,
            )
            return status in paid
        except Exception as stripe_err:
            logger.warning(
                "Stripe check failed for company sub %s (%s); falling back to DB",
                sub_id,
                stripe_err,
            )

        try:
            result = await run_blocking(
                lambda: self.supabase.table("user_subscriptions")
                .select("status")
                .eq("stripe_subscription_id", sub_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                db_status = (result.data[0].get("status") or "").strip().lower()
                return db_status in paid
        except Exception as db_err:
            logger.warning("DB subscription check failed for %s: %s", sub_id, db_err)

        return False

    async def check_user_access(self, user_id: str) -> Dict:
        """
        Two plans only: sole (NCC + SIR) | professional (all). Read from profile;
        if profile says sole but user has an active subscription in DB, sync profile to professional.
        """
        try:
            profile = self.ensure_profile(user_id)
            if not profile:
                return self._activate_sole_plan(user_id)

            account_type = (profile.get('account_type') or 'sole').strip().lower()
            if account_type not in ('sole', 'professional'):
                account_type = 'sole'

            # Passcode trial expired: fall back to sole (effective tier only; optionally persist)
            subscription_source = (profile.get('subscription_source') or '').strip().lower()
            access_expires_at = profile.get('access_expires_at')
            if account_type == 'professional' and subscription_source == 'passcode' and access_expires_at:
                try:
                    if isinstance(access_expires_at, str):
                        exp = datetime.fromisoformat(access_expires_at.replace('Z', '+00:00'))
                    else:
                        exp = access_expires_at
                except Exception:
                    exp = None
                now_utc = datetime.now(timezone.utc)
                if exp and not getattr(exp, 'tzinfo', None):
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp and now_utc >= exp:
                    account_type = 'sole'
                    try:
                        await run_blocking(
                            lambda: self.supabase.table('profiles')
                            .update({
                                'account_type': 'sole',
                                'subscription_status': 'inactive',
                                'access_expires_at': None,
                                'subscription_source': None,
                            })
                            .eq('id', user_id)
                            .execute()
                        )
                        logger.info("Passcode trial expired for user %s; downgraded to sole", user_id)
                    except Exception as e:
                        logger.warning("Failed to persist sole after passcode expiry: %s", e)
                    profile['account_type'] = 'sole'
                    profile['subscription_status'] = 'inactive'
                    profile['access_expires_at'] = None
                    profile['subscription_source'] = None

            # Company seat: membership + company active + Stripe still paid
            # (If cancel webhook missed, companies.status can stay active — verify Stripe.)
            from services.company_service import (
                get_user_company,
                set_company_status_by_subscription,
            )

            company = await run_blocking(lambda: get_user_company(user_id))
            if company:
                company_active = (company.get("status") or "").strip().lower() == "active"
                company_paid = False
                if company_active:
                    company_paid = await self._company_stripe_still_paid(company)

                if company_active and company_paid:
                    account_type = "professional"
                    profile["account_type"] = "professional"
                    profile["subscription_status"] = "active"
                    profile["subscription_source"] = "company"
                    profile["company_id"] = company.get("id")
                    profile["access_expires_at"] = None
                    try:
                        await run_blocking(
                            lambda: self.supabase.table("profiles")
                            .update(
                                {
                                    "account_type": "professional",
                                    "subscription_status": "active",
                                    "subscription_source": "company",
                                    "company_id": company.get("id"),
                                    "access_expires_at": None,
                                }
                            )
                            .eq("id", user_id)
                            .execute()
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to sync company Professional on profile for %s: %s",
                            user_id,
                            e,
                        )
                else:
                    # Inactive/pending company, or Stripe canceled but company row still "active".
                    # Never wipe an active passcode (or other non-company) Professional grant.
                    if company_active and not company_paid:
                        stripe_sub_id = (company.get("stripe_subscription_id") or "").strip()
                        try:
                            await run_blocking(
                                lambda: set_company_status_by_subscription(
                                    stripe_sub_id,
                                    "canceled",
                                    company_id=company.get("id"),
                                    owner_user_id=company.get("owner_user_id") or user_id,
                                )
                            )
                            logger.info(
                                "Deactivated company %s after Stripe cancel (self-heal)",
                                company.get("id"),
                            )
                        except Exception as heal_err:
                            logger.warning(
                                "Failed to deactivate unpaid company %s: %s",
                                company.get("id"),
                                heal_err,
                            )
                        # Prevent self-heal upgrade from stale user_subscriptions.status=active
                        if stripe_sub_id:
                            try:
                                await run_blocking(
                                    lambda sid=stripe_sub_id: self.supabase.table(
                                        "user_subscriptions"
                                    )
                                    .update({"status": "cancelled"})
                                    .eq("stripe_subscription_id", sid)
                                    .execute()
                                )
                                self.invalidate_cache(user_id)
                            except Exception as us_err:
                                logger.warning(
                                    "Failed to mark user_subscriptions cancelled for %s: %s",
                                    stripe_sub_id,
                                    us_err,
                                )

                    preserve_source = (
                        profile.get("subscription_source") or subscription_source or ""
                    ).strip().lower()
                    preserve_other = (
                        account_type == "professional"
                        and preserve_source in ("passcode", "stripe")
                    )

                    if preserve_other:
                        # Detach company link only; keep passcode/stripe Professional
                        try:
                            await run_blocking(
                                lambda: self.supabase.table("profiles")
                                .update({"company_id": None})
                                .eq("id", user_id)
                                .execute()
                            )
                        except Exception as e:
                            logger.warning(
                                "Failed to clear company_id while preserving %s access: %s",
                                preserve_source,
                                e,
                            )
                        profile["company_id"] = None
                        logger.info(
                            "Preserved %s Professional for user %s while detaching company %s",
                            preserve_source,
                            user_id,
                            company.get("id"),
                        )
                    else:
                        account_type = "sole"
                        try:
                            await run_blocking(
                                lambda: self.supabase.table("profiles")
                                .update(
                                    {
                                        "account_type": "sole",
                                        "subscription_status": "inactive",
                                        "subscription_source": None,
                                        "company_id": None,
                                        "stripe_subscription_id": None,
                                        "access_expires_at": None,
                                    }
                                )
                                .eq("id", user_id)
                                .execute()
                            )
                        except Exception as e:
                            logger.warning(
                                "Failed to clear inactive company membership profile: %s", e
                            )
                        profile["account_type"] = "sole"
                        profile["subscription_status"] = "inactive"
                        profile["subscription_source"] = None
                        profile["company_id"] = None
                        profile["stripe_subscription_id"] = None
                        profile["access_expires_at"] = None
            elif subscription_source == "company" or profile.get("company_id"):
                # Stale profile flags with no usable company membership
                preserve_source = (subscription_source or "").strip().lower()
                if account_type == "professional" and preserve_source in ("passcode", "stripe"):
                    try:
                        await run_blocking(
                            lambda: self.supabase.table("profiles")
                            .update({"company_id": None})
                            .eq("id", user_id)
                            .execute()
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to clear stale company_id for %s user %s: %s",
                            preserve_source,
                            user_id,
                            e,
                        )
                    profile["company_id"] = None
                else:
                    account_type = "sole"
                    try:
                        await run_blocking(
                            lambda: self.supabase.table("profiles")
                            .update(
                                {
                                    "account_type": "sole",
                                    "subscription_status": "inactive",
                                    "subscription_source": None,
                                    "company_id": None,
                                    "stripe_subscription_id": None,
                                    "access_expires_at": None,
                                }
                            )
                            .eq("id", user_id)
                            .execute()
                        )
                    except Exception as e:
                        logger.warning("Failed to clear stale company profile flags: %s", e)
                    profile["account_type"] = "sole"
                    profile["subscription_status"] = "inactive"
                    profile["subscription_source"] = None
                    profile["company_id"] = None
                    profile["stripe_subscription_id"] = None
                    profile["access_expires_at"] = None

            # Self-heal: if profile says sole but they have an active subscription in Stripe (e.g. webhook missed).
            # Runs automatically on any request that checks access (dashboard load, usage-stats, upload, etc.):
            # no button or user action required — first time they open the app after subscribing is enough.
            if account_type == 'sole' and (profile.get('subscription_source') or '').strip().lower() != 'company':
                sub = await self.get_user_subscription(user_id)
                from_stripe = False
                if sub and sub.get('stripe_subscription_id'):
                    # Never upgrade from stale DB "active" after Stripe cancel
                    sub = await self._enhance_with_stripe_status(sub, user_id)
                    live = (sub.get('stripe_status') or sub.get('status') or '').strip().lower()
                    if live not in ('active', 'trialing', 'past_due'):
                        try:
                            await run_blocking(
                                lambda sid=sub.get('stripe_subscription_id'): self.supabase.table(
                                    'user_subscriptions'
                                )
                                .update({'status': 'cancelled'})
                                .eq('stripe_subscription_id', sid)
                                .execute()
                            )
                            self.invalidate_cache(user_id)
                        except Exception:
                            pass
                        sub = None
                if not sub and profile.get('stripe_customer_id'):
                    sub = await self.get_user_subscription(user_id, include_stripe_status=True)
                    from_stripe = sub and sub.get('from_stripe', False)
                if sub and sub.get('status') in ('active', 'trialing', 'past_due'):
                    live_status = (sub.get('stripe_status') or sub.get('status') or '').strip().lower()
                    if live_status not in ('active', 'trialing', 'past_due'):
                        sub = None
                if sub and sub.get('status') in ('active', 'trialing', 'past_due'):
                    plan_name = (sub.get('plan_name') or '').lower()
                    if 'professional' in plan_name or sub.get('max_documents') is None:
                        account_type = 'professional'
                        try:
                            period_end = sub.get('current_period_end')
                            period_start = sub.get('current_period_start')
                            if period_end:
                                if isinstance(period_end, str):
                                    period_end = datetime.fromisoformat(period_end.replace('Z', '+00:00'))
                                elif isinstance(period_end, (int, float)):
                                    period_end = datetime.fromtimestamp(period_end)
                            else:
                                period_end = None
                            if period_start:
                                if isinstance(period_start, str):
                                    period_start = datetime.fromisoformat(period_start.replace('Z', '+00:00'))
                                elif isinstance(period_start, (int, float)):
                                    period_start = datetime.fromtimestamp(period_start)
                            else:
                                period_start = None

                            stripe_sub_id = sub.get('stripe_subscription_id')
                            stripe_cust_id = profile.get('stripe_customer_id') or sub.get('stripe_customer_id')

                            # If subscription came from Stripe (not in DB), create the user_subscriptions row
                            if from_stripe and stripe_sub_id:
                                try:
                                    plan_limits = PLAN_LIMITS.get('professional', {})
                                    subscription_data = {
                                        'user_id': user_id,
                                        'subscription_type': 'individual',
                                        'plan_name': 'professional',
                                        'plan_price': 49.0,  # Default for professional (AUD / month)
                                        'currency': 'AUD',
                                        'stripe_customer_id': stripe_cust_id,
                                        'stripe_subscription_id': stripe_sub_id,
                                        'status': sub.get('status', 'active'),
                                        'current_period_start': period_start.isoformat() if period_start else datetime.utcnow().isoformat(),
                                        'current_period_end': period_end.isoformat() if period_end else None,
                                        'max_documents': plan_limits.get('max_documents'),
                                        'max_questions': plan_limits.get('max_questions'),
                                        'documents_uploaded': 0,
                                        'questions_asked': 0,
                                    }
                                    await run_blocking(
                                        lambda: self.supabase.table('user_subscriptions')
                                        .insert(subscription_data)
                                        .execute()
                                    )
                                    logger.info("Created user_subscriptions row for user %s from Stripe subscription", user_id)
                                except Exception as create_err:
                                    logger.warning("Failed to create user_subscriptions row: %s", create_err)

                            await self.update_user_profile_subscription(
                                user_id,
                                account_type='professional',
                                subscription_status='active',
                                access_expires_at=period_end,
                                stripe_subscription_id=stripe_sub_id,
                                subscription_source='stripe',
                            )
                            logger.info("Synced profile to professional for user %s (had active subscription)", user_id)
                            if period_end:
                                profile['access_expires_at'] = period_end.isoformat() if hasattr(period_end, 'isoformat') else period_end
                        except Exception as sync_err:
                            logger.warning("Failed to sync profile to professional: %s", sync_err)

            # Self-heal downgrade: Stripe professional but subscription canceled/ended (webhook may have been missed).
            if account_type == 'professional' and subscription_source not in ('passcode', 'company'):
                stripe_sub = await self.get_user_subscription(user_id)
                if not stripe_sub and profile.get('stripe_customer_id'):
                    stripe_sub = await self.get_user_subscription(user_id, include_stripe_status=True)
                active_statuses = ('active', 'trialing', 'past_due')
                if not stripe_sub or stripe_sub.get('status') not in active_statuses:
                    account_type = 'sole'
                    try:
                        await self.update_user_profile_subscription(
                            user_id,
                            account_type='sole',
                            subscription_status='inactive',
                            stripe_subscription_id=None,
                            subscription_source=None,
                        )
                        stripe_sub_id = profile.get('stripe_subscription_id')
                        if stripe_sub_id:
                            await run_blocking(
                                lambda sid=stripe_sub_id: self.supabase.table('user_subscriptions')
                                .update({'status': 'cancelled'})
                                .eq('stripe_subscription_id', sid)
                                .execute()
                            )
                        profile['account_type'] = 'sole'
                        profile['subscription_status'] = 'inactive'
                        profile['subscription_source'] = None
                        profile['access_expires_at'] = None
                        logger.info(
                            "Downgraded user %s to sole (Stripe subscription no longer active)",
                            user_id,
                        )
                    except Exception as down_err:
                        logger.warning("Failed to downgrade profile after cancel: %s", down_err)

            limits = PLAN_LIMITS.get(account_type, PLAN_LIMITS['sole'])
            return {
                'has_access': True,
                'account_type': account_type,
                'plan_name': account_type,
                'plan_display_name': PLAN_DISPLAY_NAMES.get(account_type, 'Sole'),
                'subscription_status': 'active' if account_type == 'professional' else (profile.get('subscription_status') or 'inactive'),
                'max_documents': limits.get('max_documents'),
                'max_questions': limits.get('max_questions'),
                'documents_uploaded': profile.get('documents_uploaded') or 0,
                'questions_asked': 0,
                'access_expires_at': profile.get('access_expires_at'),
                'subscription_source': profile.get('subscription_source'),
            }
        except Exception as e:
            logger.error(f"Failed to check user access: {str(e)}")
            return self._activate_sole_plan(user_id)

    async def enforce_sole_codebook_access(self, user_id: str, codebook_id: str) -> None:
        """
        This product only allows NCC/SIR shared-library codebooks (all plan types).
        Raises ValueError when the codebook is not permitted.
        """
        from services.shared_library_service import is_shared_library_codebook

        cid = (codebook_id or "").strip().upper()
        if not is_shared_library_codebook(cid):
            raise ValueError(
                "This codebook is not in the shared compliance library."
            )

    async def enforce_usage_limits(self, user_id: str, action_type: str) -> bool:
        """
        Check if user can perform an action based on usage limits
        
        Args:
            user_id: User UUID
            action_type: 'upload_document' or 'ask_question'
            
        Returns:
            True if action is allowed, False otherwise
        """
        try:
            access = await self.check_user_access(user_id)
            
            if not access.get('has_access'):
                return False
            
            if action_type == 'upload_document':
                max_docs = access.get('max_documents')
                if max_docs is None:
                    return True  # Unlimited
                docs_uploaded = access.get('documents_uploaded', 0)
                return docs_uploaded < max_docs
            
            elif action_type == 'ask_question':
                max_questions = access.get('max_questions')
                if max_questions is None:
                    return True  # Unlimited
                questions_asked = access.get('questions_asked', 0)
                return questions_asked < max_questions
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to enforce usage limits: {str(e)}")
            return False
    
    async def increment_usage(self, user_id: str, action_type: str) -> bool:
        """
        Increment usage counter after successful action
        
        Args:
            user_id: User UUID
            action_type: 'documents' or 'questions'
            
        Returns:
            True if successful
        """
        try:
            # Get subscription
            subscription = await self.get_user_subscription(user_id)
            
            if subscription and 'subscription_id' in subscription:
                # Update subscription counter
                if action_type == 'documents':
                    self.supabase.table('user_subscriptions')\
                        .update({'documents_uploaded': subscription['documents_uploaded'] + 1})\
                        .eq('id', subscription['id'])\
                        .execute()
                elif action_type == 'questions':
                    self.supabase.table('user_subscriptions')\
                        .update({'questions_asked': subscription['questions_asked'] + 1})\
                        .eq('id', subscription['id'])\
                        .execute()
            else:
                # Check if trial/access code
                redemption_result = self.supabase.table('access_code_redemptions')\
                    .select('*')\
                    .eq('user_id', user_id)\
                    .eq('is_active', True)\
                    .single()\
                    .execute()
                
                if redemption_result.data:
                    redemption = redemption_result.data
                    if action_type == 'documents':
                        self.supabase.table('access_code_redemptions')\
                            .update({'documents_used': redemption['documents_used'] + 1})\
                            .eq('id', redemption['id'])\
                            .execute()
                    elif action_type == 'questions':
                        self.supabase.table('access_code_redemptions')\
                            .update({'questions_used': redemption['questions_used'] + 1})\
                            .eq('id', redemption['id'])\
                            .execute()
                else:
                    # Default to the free plan counters
                    self._decrement_free_plan_allowance(user_id, action_type)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to increment usage: {str(e)}")
            return False

    async def update_user_profile_subscription(
        self,
        user_id: str,
        account_type: str,
        subscription_status: str,
        access_expires_at: Optional[datetime] = None,
        stripe_subscription_id: Optional[str] = None,
        subscription_source: Optional[str] = None,
    ):
        """Update user profile with subscription info (two plans: sole | professional). subscription_source: 'stripe' | 'passcode' | None."""
        try:
            update_data = {
                'account_type': account_type,
                'subscription_status': subscription_status,
            }
            if access_expires_at:
                update_data['access_expires_at'] = access_expires_at.isoformat()
            if stripe_subscription_id is not None:
                update_data['stripe_subscription_id'] = stripe_subscription_id
            if subscription_source is not None:
                update_data['subscription_source'] = subscription_source

            await run_blocking(
                lambda: self.supabase.table('profiles').update(update_data).eq('id', user_id).execute()
            )

            logger.info(f"Updated profile for user {user_id}")

        except Exception as e:
            logger.error(f"Failed to update profile: {str(e)}")
            raise

    async def cancel_subscription(
        self,
        subscription_id: str,
        immediately: bool = False
    ) -> bool:
        """Cancel a subscription by database ID (legacy method - kept for compatibility)"""
        try:
            # Get subscription
            result = self.supabase.table('user_subscriptions')\
                .select('*')\
                .eq('id', subscription_id)\
                .single()\
                .execute()
            
            if not result.data:
                logger.warning(f"Subscription not found in database: {subscription_id}")
                return False
            
            subscription = result.data
            stripe_subscription_id = subscription.get('stripe_subscription_id')
            user_id = subscription.get('user_id')
            
            if not stripe_subscription_id:
                logger.error(f"No Stripe subscription ID found for subscription {subscription_id}")
                return False
            
            # Cancel using the new method that works with Stripe ID
            return await self.cancel_subscription_by_stripe_id(
                stripe_subscription_id=stripe_subscription_id,
                database_id=subscription_id,
                immediately=immediately,
                user_id=user_id
            )
            
        except Exception as e:
            logger.error(f"Failed to cancel subscription: {str(e)}", exc_info=True)
            return False
    
    async def cancel_subscription_by_stripe_id(
        self,
        stripe_subscription_id: str,
        database_id: Optional[str] = None,
        immediately: bool = False,
        user_id: Optional[str] = None
    ) -> bool:
        """Cancel a subscription by Stripe subscription ID (works even if not in database)"""
        try:
            logger.info(f"Cancelling Stripe subscription: {stripe_subscription_id}, immediately={immediately}")
            
            # Cancel in Stripe first (this is the source of truth)
            try:
                cancelled_subscription = await stripe_service.cancel_subscription(
                    stripe_subscription_id,
                    immediately=immediately
                )
                logger.info(f"Successfully cancelled subscription in Stripe: {stripe_subscription_id}")
            except Exception as stripe_error:
                logger.error(f"Failed to cancel subscription in Stripe: {str(stripe_error)}", exc_info=True)
                raise
            
            # Update database if subscription exists there
            # Try to find by database_id first, then by stripe_subscription_id
            db_subscription_id = None
            if database_id:
                db_subscription_id = database_id
                logger.info(f"Using database_id to update: {database_id}")
            else:
                # Try to find subscription in database by stripe_subscription_id
                try:
                    result = self.supabase.table('user_subscriptions')\
                        .select('id, user_id')\
                        .eq('stripe_subscription_id', stripe_subscription_id)\
                        .limit(1)\
                        .execute()
                    if result.data and len(result.data) > 0:
                        db_subscription_id = result.data[0]['id']
                        # If we don't have user_id, get it from database
                        if not user_id:
                            user_id = result.data[0].get('user_id')
                        logger.info(f"Found subscription in database by stripe_subscription_id: {db_subscription_id}")
                except Exception as e:
                    logger.warning(f"Could not find subscription in database by stripe_subscription_id: {str(e)}")
            
            if db_subscription_id:
                try:
                    if immediately:
                        self.supabase.table('user_subscriptions')\
                            .update({
                                'status': 'cancelled',
                                'cancelled_at': datetime.utcnow().isoformat(),
                                'updated_at': datetime.utcnow().isoformat()
                            })\
                            .eq('id', db_subscription_id)\
                            .execute()
                    else:
                        self.supabase.table('user_subscriptions')\
                            .update({
                                'cancel_at_period_end': True,
                                'updated_at': datetime.utcnow().isoformat()
                            })\
                            .eq('id', db_subscription_id)\
                            .execute()
                    
                    logger.info(f"Updated subscription in database: {db_subscription_id}")
                    
                    # Log event if subscription exists in database
                    try:
                        self.supabase.table('subscription_events').insert({
                            'subscription_id': db_subscription_id,
                            'event_type': 'cancelled',
                            'triggered_by': 'user',
                            'event_data': {'immediately': immediately, 'stripe_subscription_id': stripe_subscription_id}
                        }).execute()
                    except Exception as event_error:
                        logger.warning(f"Failed to log subscription event: {str(event_error)}")
                        
                except Exception as db_error:
                    logger.warning(f"Failed to update database (subscription may not exist yet): {str(db_error)}")
                    # Don't fail if database update fails - Stripe cancellation is what matters
            else:
                logger.info(f"Subscription not in database yet, only cancelled in Stripe: {stripe_subscription_id}")
            
            # Activate free plan for user if we have user_id
            if user_id:
                try:
                    self._activate_sole_plan(user_id)
                    logger.info(f"Activated free plan for user: {user_id}")
                except Exception as free_plan_error:
                    logger.warning(f"Failed to activate free plan: {str(free_plan_error)}")
            else:
                logger.warning(f"No user_id available to activate free plan for cancelled subscription: {stripe_subscription_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to cancel subscription by Stripe ID: {str(e)}", exc_info=True)
            return False
    
    async def extend_subscription(
        self,
        subscription_id: str,
        days: int,
        admin_user_id: Optional[str] = None
    ) -> bool:
        """Extend subscription by specified days (admin function)"""
        try:
            result = self.supabase.table('user_subscriptions')\
                .select('*')\
                .eq('id', subscription_id)\
                .single()\
                .execute()
            
            if not result.data:
                return False
            
            subscription = result.data
            current_end = datetime.fromisoformat(subscription['current_period_end'])
            new_end = current_end + timedelta(days=days)
            
            self.supabase.table('user_subscriptions')\
                .update({'current_period_end': new_end.isoformat()})\
                .eq('id', subscription_id)\
                .execute()
            
            # Log event
            self.supabase.table('subscription_events').insert({
                'subscription_id': subscription_id,
                'event_type': 'extended',
                'triggered_by': 'admin',
                'triggered_by_user_id': admin_user_id,
                'event_data': {'days_extended': days, 'new_end_date': new_end.isoformat()}
            }).execute()
            
            logger.info(f"Extended subscription {subscription_id} by {days} days")
            return True
            
        except Exception as e:
            logger.error(f"Failed to extend subscription: {str(e)}")
            return False
    
    async def get_subscription_stats(self) -> Dict:
        """Get overall subscription statistics"""
        try:
            # Use the database function
            result = self.supabase.rpc('get_subscription_stats').execute()
            
            if result.data:
                return result.data[0]
            
            return {}
            
        except Exception as e:
            logger.error(f"Failed to get subscription stats: {str(e)}")
            return {}


# Global instance
subscription_service = SubscriptionService()

