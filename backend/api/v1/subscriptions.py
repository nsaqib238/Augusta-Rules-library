"""
Subscription API Endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from typing import Optional, Dict, List
from datetime import datetime
from services.stripe_service import (
    stripe_service,
    StripeCheckoutValidationError,
    CHECKOUT_CUSTOMER_INVALID,
)

try:
    from stripe.error import StripeError as StripeApiError
except ModuleNotFoundError:
    try:
        from stripe.errors import StripeError as StripeApiError
    except ModuleNotFoundError:
        from stripe._error import StripeError as StripeApiError
from services.subscription_service import subscription_service, PLAN_DISPLAY_NAMES
from services.subscription_service_v2 import SubscriptionServiceV2
from services.redirect_url import RedirectUrlError, validate_redirect_url
from middleware.subscription_check import get_current_user, get_current_user_with_email, check_admin_access
import os
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _validated_checkout_urls(success_url: str, cancel_url: str) -> tuple[str, str]:
    try:
        return (
            validate_redirect_url(success_url, field_name="success_url"),
            validate_redirect_url(cancel_url, field_name="cancel_url"),
        )
    except RedirectUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

# Request/Response Models (two plans: sole | professional; only professional is paid)
class CreateCheckoutSessionRequest(BaseModel):
    """Send either ``plan_name`` (legacy) or ``price_id`` (Stripe catalog), not both."""
    plan_name: Optional[str] = None  # professional | company_small | company_large
    price_id: Optional[str] = None  # Stripe Price id from GET /subscriptions/products
    success_url: str
    cancel_url: str
    company_id: Optional[str] = None  # set by server for company plans; optional for catalog
    referral_code: Optional[str] = None  # marketing partner code (company checkout only)
    company_name: Optional[str] = None

    @model_validator(mode="after")
    def _checkout_mode(self) -> "CreateCheckoutSessionRequest":
        has_plan = self.plan_name is not None and str(self.plan_name).strip() != ""
        has_price = self.price_id is not None and str(self.price_id).strip() != ""
        if has_plan == has_price:
            raise ValueError("Provide exactly one of plan_name or price_id")
        return self

class CheckoutSessionResponse(BaseModel):
    session_id: str
    session_url: str

class BillingPortalRequest(BaseModel):
    return_url: str

class BillingPortalResponse(BaseModel):
    portal_url: str

class SubscriptionResponse(BaseModel):
    id: str  # Database UUID (if exists) or Stripe subscription ID (if from Stripe only)
    subscription_type: str
    plan_name: str
    status: str
    current_period_end: Optional[str]  # Optional for free plans
    max_documents: Optional[int]
    max_questions: Optional[int]
    documents_uploaded: int
    questions_asked: int
    # Stripe status fields (for debugging)
    stripe_subscription_id: Optional[str] = None

class StripeSubscriptionResponse(BaseModel):
    """Complete Stripe subscription data as returned from Stripe API"""
    id: str
    object: str
    status: str
    customer: str
    items: Dict
    current_period_start: int
    current_period_end: int
    cancel_at_period_end: bool
    canceled_at: Optional[int]
    cancel_at: Optional[int]
    created: int
    ended_at: Optional[int]
    trial_start: Optional[int]
    trial_end: Optional[int]
    metadata: Dict
    default_payment_method: Optional[str]
    default_source: Optional[str]
    latest_invoice: Optional[str]
    next_pending_invoice_item_invoice: Optional[int]
    pending_invoice_item_interval: Optional[str]
    pending_setup_intent: Optional[str]
    pending_update: Optional[Dict]
    schedule: Optional[str]
    start_date: int
    application: Optional[str]
    application_fee_percent: Optional[float]
    billing_cycle_anchor: int
    billing_thresholds: Optional[Dict]
    collection_method: str
    currency: str
    days_until_due: Optional[int]
    default_tax_rates: List
    description: Optional[str]
    discount: Optional[Dict]
    livemode: bool
    pause_collection: Optional[Dict]
    payment_settings: Optional[Dict]
    quantity: int
    test_clock: Optional[str]
    transfer_data: Optional[Dict]
    trial_period_days: Optional[int]

class UsageStatsResponse(BaseModel):
    has_access: bool
    account_type: str  # sole | professional
    plan_name: Optional[str]
    plan_display_name: Optional[str]
    max_documents: Optional[int]
    max_questions: Optional[int]
    documents_uploaded: int
    questions_asked: int
    access_expires_at: Optional[str]
    subscription_status: Optional[str]
    subscription_source: Optional[str] = None  # 'stripe' | 'passcode' for display


class RedeemPasscodeRequest(BaseModel):
    code: str


class RedeemPasscodeResponse(BaseModel):
    success: bool
    access_expires_at: str
    message: str


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    request: CreateCheckoutSessionRequest,
    current_user: str = Depends(get_current_user)
):
    """
    Create a Stripe Checkout session for subscription.

    Use ``plan_name='professional'`` for the legacy single-plan flow, or ``price_id``
    from ``GET /api/v1/subscriptions/products`` for catalog-based pricing (same as
    the former ``/subscriptions-v2`` checkout).
    """
    try:
        from services.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        profile_result = supabase.table('profiles').select('*').eq('id', current_user).single().execute()

        if not profile_result.data:
            raise HTTPException(status_code=404, detail="User profile not found")

        profile = profile_result.data

        if request.price_id:
            user_email = profile.get('email')
            if not user_email:
                raise HTTPException(status_code=400, detail="User profile has no email")
            v2 = SubscriptionServiceV2()
            customer_id = await v2.get_or_create_customer(current_user, user_email)
            if not customer_id:
                raise HTTPException(status_code=500, detail="Failed to create Stripe customer")
            metadata: Dict = {'user_id': current_user}
            if request.company_id:
                metadata['company_id'] = request.company_id
            price_id_clean = str(request.price_id).strip()
            success_url, cancel_url = _validated_checkout_urls(request.success_url, request.cancel_url)
            for attempt in range(2):
                try:
                    session = await stripe_service.create_checkout_session(
                        customer_id=customer_id,
                        price_id=price_id_clean,
                        success_url=success_url,
                        cancel_url=cancel_url,
                        metadata=metadata,
                    )
                    return CheckoutSessionResponse(
                        session_id=session.id,
                        session_url=session.url,
                    )
                except StripeCheckoutValidationError as e:
                    # Prefer args[0] — some environments stringify Exception differently than str(e).
                    is_stale_customer = bool(
                        e.args and e.args[0] == CHECKOUT_CUSTOMER_INVALID
                    )
                    if attempt == 0 and is_stale_customer:
                        logger.warning(
                            "Checkout rejected stored customer %s; clearing mapping and retrying once",
                            customer_id,
                        )
                        await v2.clear_stripe_customer_mapping(current_user, customer_id)
                        customer_id = await v2.get_or_create_customer(current_user, user_email)
                        if not customer_id:
                            raise HTTPException(status_code=500, detail="Failed to create Stripe customer")
                        continue
                    if is_stale_customer:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "Your saved Stripe customer was removed in Stripe. "
                                "Click Subscribe again after refreshing the page; the server will create a new customer."
                            ),
                        )
                    raise HTTPException(status_code=400, detail=str(e))
                except StripeApiError as e:
                    msg = str(e)
                    code = getattr(e, "code", "") or ""
                    param = getattr(e, "param", "") or ""
                    if (
                        attempt == 0
                        and ("No such customer" in msg or code == "resource_missing")
                        and (param == "customer" or "customer" in msg.lower())
                    ):
                        logger.warning(
                            "Stripe checkout session rejected customer %s (%s); clearing mapping and retrying once",
                            customer_id,
                            msg,
                        )
                        await v2.clear_stripe_customer_mapping(current_user, customer_id)
                        customer_id = await v2.get_or_create_customer(current_user, user_email)
                        if not customer_id:
                            raise HTTPException(status_code=500, detail="Failed to create Stripe customer")
                        continue
                    logger.error(f"Failed to create checkout session: {msg}")
                    raise HTTPException(status_code=500, detail=msg)

        stripe_customer_id = profile.get('stripe_customer_id')
        user_email = profile.get('email')
        user_name = profile.get('full_name')

        if not stripe_customer_id:
            customer = await stripe_service.create_customer(
                email=user_email,
                name=user_name,
                metadata={'user_id': current_user}
            )
            stripe_customer_id = customer.id

            supabase.table('profiles').update({'stripe_customer_id': stripe_customer_id}).eq('id', current_user).execute()
            logger.debug(
                "Created Stripe customer %s for user %s with email %s",
                stripe_customer_id,
                current_user,
                user_email,
            )
            logger.info("Created Stripe customer for user %s with email %s", current_user, user_email)
        else:
            # Only sync email when we actually have an email on the profile; otherwise
            # update_customer would call Stripe modify() with no fields and can error oddly.
            if user_email:
                try:
                    customer = await stripe_service.get_customer(stripe_customer_id)
                    if customer.email != user_email:
                        logger.debug(
                            "Updating Stripe customer %s email from '%s' to '%s'",
                            stripe_customer_id,
                            customer.email,
                            user_email,
                        )
                        logger.info(
                            "Updating Stripe customer email from '%s' to '%s'",
                            customer.email,
                            user_email,
                        )
                        await stripe_service.update_customer(
                            customer_id=stripe_customer_id,
                            email=user_email,
                            name=user_name
                        )
                        logger.info("Updated Stripe customer email to match profile: %s", user_email)
                except Exception as email_update_error:
                    logger.warning("Failed to update customer email before checkout: %s", email_update_error)

        plan = (request.plan_name or "").strip().lower()
        company_plans = ("company_small", "company_large")
        if plan not in ("professional",) + company_plans:
            raise HTTPException(
                status_code=400,
                detail="Plan must be professional, company_small, or company_large",
            )

        resolved_price_id = stripe_service.get_price_id(plan)
        if not resolved_price_id:
            raise HTTPException(
                status_code=400,
                detail=f"Price not configured for plan '{plan}' (check STRIPE_PRICE_* in .env)",
            )

        from services.company_service import PLAN_SEATS, create_pending_company

        metadata: Dict = {"user_id": current_user, "plan_name": plan}
        if plan in company_plans:
            company = create_pending_company(
                owner_user_id=current_user,
                plan_name=plan,
                company_name=request.company_name,
                referral_code=request.referral_code,
            )
            metadata["company_id"] = company["id"]
            metadata["max_seats"] = str(PLAN_SEATS[plan])
            if request.referral_code and str(request.referral_code).strip():
                metadata["referral_code"] = str(request.referral_code).strip().upper()

        success_url, cancel_url = _validated_checkout_urls(request.success_url, request.cancel_url)
        session = await stripe_service.create_checkout_session(
            customer_id=stripe_customer_id,
            price_id=resolved_price_id,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata,
        )

        return CheckoutSessionResponse(
            session_id=session.id,
            session_url=session.url
        )

    except HTTPException:
        raise
    except StripeCheckoutValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/products")
async def get_subscription_products():
    """
    Active products and prices from Supabase (Stripe sync via webhooks).
    Prefer this path over ``/subscriptions-v2/products`` for new integrations.
    """
    try:
        from services.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        products_result = supabase.table('products').select('*').eq('active', True).execute()
        products = products_result.data or []

        products_with_prices = []
        for product in products:
            prices_result = (
                supabase.table('prices')
                .select('*')
                .eq('product_id', product['id'])
                .eq('active', True)
                .execute()
            )
            product = dict(product)
            product['prices'] = prices_result.data or []
            products_with_prices.append(product)

        logger.info("Returning %s catalog products with prices", len(products_with_prices))
        return products_with_prices

    except Exception as e:
        logger.error("Failed to get products: %s", e, exc_info=True)
        logger.warning("Products table may be missing or empty; returning [].")
        return []


@router.post("/create-portal-session", response_model=BillingPortalResponse)
async def create_portal_session(
    request: BillingPortalRequest,
    current_user: str = Depends(get_current_user)
):
    """
    Create a Stripe Customer Portal session for subscription management
    """
    try:
        # Get user's Stripe customer ID
        from services.supabase_client import get_supabase_client
        supabase = get_supabase_client()
        
        # Get full profile with email
        profile_result = supabase.table('profiles').select('stripe_customer_id, email, full_name').eq('id', current_user).single().execute()
        
        if not profile_result.data:
            raise HTTPException(status_code=404, detail="User profile not found")
        
        profile = profile_result.data
        stripe_customer_id = profile.get('stripe_customer_id')
        user_email = profile.get('email')
        user_name = profile.get('full_name')
        
        # If no customer ID in database, try to find it in Stripe by email
        if not stripe_customer_id:
            logger.info(f"No stripe_customer_id in DB for user {current_user}, searching Stripe by email: {user_email}")
            
            if not user_email:
                raise HTTPException(
                    status_code=400, 
                    detail="User email not found. Cannot access billing portal."
                )
            
            # Search for customer in Stripe by email
            try:
                import stripe
                customers = stripe.Customer.list(email=user_email, limit=1)
                
                if customers.data and len(customers.data) > 0:
                    # Found existing customer in Stripe
                    stripe_customer_id = customers.data[0].id
                    logger.info(f"Found Stripe customer {stripe_customer_id} for email {user_email}")
                    
                    # Save to database for future use
                    supabase.table('profiles').update({
                        'stripe_customer_id': stripe_customer_id
                    }).eq('id', current_user).execute()
                    logger.info(f"Saved stripe_customer_id {stripe_customer_id} to profile for user {current_user}")
                    
                else:
                    # No customer found - create one
                    logger.info(f"No Stripe customer found for {user_email}, creating new customer")
                    customer = await stripe_service.create_customer(
                        email=user_email,
                        name=user_name,
                        metadata={'user_id': current_user}
                    )
                    stripe_customer_id = customer.id
                    logger.info(f"Created new Stripe customer {stripe_customer_id} for user {current_user}")
                    
                    # Save to database
                    supabase.table('profiles').update({
                        'stripe_customer_id': stripe_customer_id
                    }).eq('id', current_user).execute()
                    logger.info(f"Saved new stripe_customer_id {stripe_customer_id} to profile")
                    
            except Exception as stripe_error:
                logger.error(f"Error searching/creating Stripe customer: {str(stripe_error)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to access Stripe customer. Please contact support. Error: {str(stripe_error)}"
                )
        
        # Verify customer exists in Stripe and sync email if needed
        try:
            customer = await stripe_service.get_customer(stripe_customer_id)
            logger.info(f"Verified Stripe customer {stripe_customer_id} exists for user {current_user}")
            
            # CRITICAL: Ensure customer email in Stripe matches user's email
            # This ensures subscription emails go to the correct address
            if customer.email != user_email:
                logger.warning(f"Stripe customer {stripe_customer_id} email mismatch: Stripe='{customer.email}' vs Profile='{user_email}'")
                logger.info(f"Updating Stripe customer email to match profile: {user_email}")
                customer = await stripe_service.update_customer(
                    customer_id=stripe_customer_id,
                    email=user_email,
                    name=user_name
                )
                logger.info(f"✅ Updated Stripe customer email to: {user_email}")
        except Exception as e:
            logger.error(f"Stripe customer {stripe_customer_id} not found or invalid: {str(e)}")
            raise HTTPException(
                status_code=404,
                detail=f"Stripe customer not found. Please contact support."
            )
        
        # Create portal session
        try:
            return_url = validate_redirect_url(request.return_url, field_name="return_url")
            session = await stripe_service.create_billing_portal_session(
                customer_id=stripe_customer_id,
                return_url=return_url
            )
            logger.info(f"Created billing portal session for user {current_user} (customer: {stripe_customer_id})")
            return BillingPortalResponse(portal_url=session.url)
        except HTTPException:
            raise
        except RedirectUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as portal_error:
            logger.error(f"Failed to create portal session: {str(portal_error)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create billing portal session. Error: {str(portal_error)}"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create portal session: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get("/my-subscription", response_model=Optional[SubscriptionResponse])
async def get_my_subscription(current_user: str = Depends(get_current_user)):
    """
    Get current user's subscription with real-time Stripe status
    """
    try:
        # Try to get subscription, but don't let Stripe API slowness break the request
        # Add overall timeout protection
        import asyncio
        try:
            # Only request Stripe status for the /my-subscription endpoint (user viewing their subscription)
            # Other endpoints should use DB-only to reduce Stripe API calls
            subscription = await asyncio.wait_for(
                subscription_service.get_user_subscription(current_user, include_stripe_status=True),
                timeout=10.0  # Reduced timeout since we trust DB first
            )
        except asyncio.TimeoutError:
            logger.error(f"Timeout getting subscription for user {current_user} (15s)")
            # Return None - frontend can handle this
            return None
        except Exception as service_error:
            logger.error(f"Error in subscription service: {str(service_error)}", exc_info=True)
            # If service fails, return None instead of crashing
            subscription = None
        
        if not subscription:
            logger.info(f"No subscription found for user {current_user} (checked both database and Stripe)")
            # Return None - frontend will handle this gracefully
            return None
        
        # Handle subscription that came from Stripe (no database ID yet)
        subscription_id = subscription.get('id')  # Database UUID
        stripe_subscription_id = subscription.get('stripe_subscription_id')  # Stripe ID (sub_xxxxx)
        from_stripe_only = subscription.get('from_stripe', False)
        
        # If no database ID but we have Stripe ID, use Stripe ID as the primary identifier
        if not subscription_id and stripe_subscription_id:
            subscription_id = stripe_subscription_id
            logger.info(f"Subscription from Stripe only (not in database yet): Stripe ID = {stripe_subscription_id}")
        elif subscription_id and stripe_subscription_id:
            logger.info(f"Subscription found: Database ID = {subscription_id}, Stripe ID = {stripe_subscription_id}")
        
        # Map database fields to response model
        return SubscriptionResponse(
            id=subscription_id or 'unknown',  # Database UUID or Stripe ID if not in DB
            subscription_type=subscription.get('subscription_type', 'individual'),
            plan_name=subscription.get('plan_name', subscription.get('plan_id', 'unknown')),
            status=subscription.get('status', subscription.get('stripe_status', 'unknown')),
            current_period_end=subscription.get('current_period_end') or subscription.get('stripe_current_period_end'),
            max_documents=subscription.get('max_documents'),
            max_questions=subscription.get('max_questions'),
            documents_uploaded=subscription.get('documents_uploaded', 0),
            questions_asked=subscription.get('questions_asked', 0),
            # Stripe ID and status fields
            stripe_subscription_id=stripe_subscription_id,  # Always show Stripe ID separately
            stripe_status=subscription.get('stripe_status') or subscription.get('status'),
            stripe_current_period_start=subscription.get('stripe_current_period_start'),
            stripe_current_period_end=subscription.get('stripe_current_period_end') or subscription.get('current_period_end'),
            stripe_cancel_at_period_end=subscription.get('stripe_cancel_at_period_end'),
            stripe_canceled_at=subscription.get('stripe_canceled_at'),
            stripe_trial_end=subscription.get('stripe_trial_end'),
            stripe_error=subscription.get('stripe_error'),
            from_stripe_only=from_stripe_only
        )
        
    except Exception as e:
        logger.error(f"Failed to get subscription: {str(e)}", exc_info=True)
        # Return None instead of raising exception - frontend can handle this
        # This prevents the endpoint from crashing and causing "Failed to fetch" errors
        return None


@router.get("/usage-stats", response_model=UsageStatsResponse)
async def get_usage_stats(current_user: str = Depends(get_current_user)):
    """
    Get user's usage statistics and access level
    """
    try:
        access = await subscription_service.check_user_access(current_user)
        
        return UsageStatsResponse(
            has_access=access.get('has_access', False),
            account_type=access.get('account_type', 'sole'),
            plan_name=access.get('plan_name'),
            plan_display_name=access.get('plan_display_name'),
            max_documents=access.get('max_documents'),
            max_questions=access.get('max_questions'),
            documents_uploaded=access.get('documents_uploaded', 0),
            questions_asked=access.get('questions_asked', 0),
            access_expires_at=access.get('access_expires_at'),
            subscription_status=access.get('subscription_status'),
            subscription_source=access.get('subscription_source'),
        )
        
    except Exception as e:
        logger.error(f"Failed to get usage stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/redeem-passcode", response_model=RedeemPasscodeResponse)
async def redeem_passcode(
    request: RedeemPasscodeRequest,
    current_user: str = Depends(get_current_user),
):
    """
    Redeem a passcode for professional trial. Grants professional access until access_expires_at; then user falls back to sole.
    """
    try:
        from services.passcode_service import redeem_passcode as do_redeem
        result = do_redeem(current_user, request.code)
        return RedeemPasscodeResponse(
            success=result["success"],
            access_expires_at=result["access_expires_at"],
            message=result["message"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Redeem passcode failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Redemption failed. Please try again.")


@router.get("/stripe-status", response_model=List[StripeSubscriptionResponse])
async def get_stripe_subscription_status(current_user: str = Depends(get_current_user)):
    """
    Get current subscription status directly from Stripe API
    Returns all subscription information that Stripe provides via webhooks
    """
    try:
        from services.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        profile = subscription_service.ensure_profile(current_user)
        if not profile:
            return []

        stripe_customer_id = profile.get('stripe_customer_id')
        
        if not stripe_customer_id:
            # Try to get from customers table (0 rows is normal — do not use .single()).
            customer_result = supabase.table('customers')\
                .select('stripe_customer_id')\
                .eq('id', current_user)\
                .maybe_single()\
                .execute()

            row = subscription_service._result_data(customer_result)
            if row and row.get('stripe_customer_id'):
                stripe_customer_id = row['stripe_customer_id']
            else:
                # Normal state for a new user: no Stripe customer/subscription yet.
                return []
        
        # Fetch all subscriptions from Stripe (including canceled)
        subscriptions = await stripe_service.list_subscriptions(
            customer_id=stripe_customer_id,
            limit=100,  # Get all subscriptions
            status='all'  # Include all statuses (active, canceled, etc.)
        )
        
        if not subscriptions:
            return []
        
        price_to_plan = getattr(stripe_service, 'price_to_plan', {}) or {}
        
        # Convert Stripe subscription objects to response format
        result = []
        for sub in subscriptions:
            # Convert Stripe object to dict
            sub_dict = sub if isinstance(sub, dict) else sub.to_dict()
            
            # Annotate each subscription item with plan metadata
            items = sub_dict.get('items', {})
            if isinstance(items, dict) and 'data' in items:
                for item in items.get('data', []):
                    price = item.get('price', {})
                    price_id = price.get('id')
                    if price_id and price_id in price_to_plan:
                        plan_key = price_to_plan[price_id]
                        display_name = PLAN_DISPLAY_NAMES.get(plan_key, plan_key.replace('_', ' ').title())
                        item['plan_name'] = plan_key
                        item['plan_display_name'] = display_name
            
            result.append(StripeSubscriptionResponse(
                id=sub_dict.get('id', ''),
                object=sub_dict.get('object', 'subscription'),
                status=sub_dict.get('status', ''),
                customer=sub_dict.get('customer', ''),
                items=sub_dict.get('items', {}),
                current_period_start=sub_dict.get('current_period_start', 0),
                current_period_end=sub_dict.get('current_period_end', 0),
                cancel_at_period_end=sub_dict.get('cancel_at_period_end', False),
                canceled_at=sub_dict.get('canceled_at'),
                cancel_at=sub_dict.get('cancel_at'),
                created=sub_dict.get('created', 0),
                ended_at=sub_dict.get('ended_at'),
                trial_start=sub_dict.get('trial_start'),
                trial_end=sub_dict.get('trial_end'),
                metadata=sub_dict.get('metadata', {}),
                default_payment_method=sub_dict.get('default_payment_method'),
                default_source=sub_dict.get('default_source'),
                latest_invoice=sub_dict.get('latest_invoice'),
                next_pending_invoice_item_invoice=sub_dict.get('next_pending_invoice_item_invoice'),
                pending_invoice_item_interval=sub_dict.get('pending_invoice_item_interval'),
                pending_setup_intent=sub_dict.get('pending_setup_intent'),
                pending_update=sub_dict.get('pending_update'),
                schedule=sub_dict.get('schedule'),
                start_date=sub_dict.get('start_date', 0),
                application=sub_dict.get('application'),
                application_fee_percent=sub_dict.get('application_fee_percent'),
                billing_cycle_anchor=sub_dict.get('billing_cycle_anchor', 0),
                billing_thresholds=sub_dict.get('billing_thresholds'),
                collection_method=sub_dict.get('collection_method', 'charge_automatically'),
                currency=sub_dict.get('currency', 'aud'),
                days_until_due=sub_dict.get('days_until_due'),
                default_tax_rates=sub_dict.get('default_tax_rates', []),
                description=sub_dict.get('description'),
                discount=sub_dict.get('discount'),
                livemode=sub_dict.get('livemode', False),
                pause_collection=sub_dict.get('pause_collection'),
                payment_settings=sub_dict.get('payment_settings'),
                quantity=sub_dict.get('quantity', 1),
                test_clock=sub_dict.get('test_clock'),
                transfer_data=sub_dict.get('transfer_data'),
                trial_period_days=sub_dict.get('trial_period_days')
            ))
        
        logger.info(f"Retrieved {len(result)} subscriptions from Stripe for user {current_user}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get Stripe subscription status: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch Stripe subscription: {str(e)}")


class SyncResponse(BaseModel):
    success: bool
    message: str
    subscriptions_synced: int
    errors: Optional[List[str]] = None


@router.post("/sync-to-supabase", response_model=SyncResponse)
async def sync_stripe_subscriptions_to_supabase(current_user: str = Depends(get_current_user)):
    """
    Sync all Stripe subscriptions for the current user to Supabase
    This ensures the database has the latest subscription data from Stripe
    """
    try:
        from services.supabase_client import get_supabase_client
        from datetime import datetime
        supabase = get_supabase_client()
        subscription_service_v2 = SubscriptionServiceV2()
        
        # Get user's Stripe customer ID
        # 0 rows is possible (new user). Do not use .single() which raises PGRST116.
        profile_result = supabase.table('profiles')\
            .select('stripe_customer_id, email')\
            .eq('id', current_user)\
            .maybe_single()\
            .execute()
        
        if not profile_result.data:
            raise HTTPException(status_code=404, detail="User profile not found")
        
        profile = profile_result.data
        stripe_customer_id = profile.get('stripe_customer_id')
        
        if not stripe_customer_id:
            # Try to get from customers table (0 rows is normal — do not use .single()).
            # Some Supabase client errors can return None; treat that as "no row" here
            # so we return a clean 404 instead of a 500.
            customer_result = supabase.table('customers')\
                .select('stripe_customer_id')\
                .eq('id', current_user)\
                .maybe_single()\
                .execute()

            row = getattr(customer_result, "data", None) if customer_result is not None else None
            if row and row.get('stripe_customer_id'):
                stripe_customer_id = row['stripe_customer_id']
            else:
                raise HTTPException(
                    status_code=404,
                    detail="No Stripe customer ID found. Please create a subscription first."
                )
        
        # Ensure customer record exists in customers table
        try:
            customer_upsert_result = supabase.table('customers')\
                .upsert({
                    'id': current_user,
                    'stripe_customer_id': stripe_customer_id
                })\
                .execute()
            logger.info(f"Ensured customer record exists: {current_user} -> {stripe_customer_id}")
        except Exception as customer_err:
            logger.warning(f"Failed to upsert customer record: {customer_err}")
        
        # Fetch all subscriptions from Stripe
        subscriptions = await stripe_service.list_subscriptions(
            customer_id=stripe_customer_id,
            limit=100
        )
        
        if not subscriptions:
            return SyncResponse(
                success=True,
                message="No subscriptions found in Stripe to sync",
                subscriptions_synced=0
            )
        
        # Sync each subscription to Supabase
        synced_count = 0
        errors = []
        
        for sub in subscriptions:
            try:
                # Convert Stripe object to dict if needed
                sub_dict = sub if isinstance(sub, dict) else sub.to_dict()
                subscription_id = sub_dict.get('id')
                customer_id = sub_dict.get('customer')
                
                if not subscription_id or not customer_id:
                    errors.append(f"Invalid subscription data: missing id or customer")
                    continue
                
                # Use subscription_service_v2 to upsert
                try:
                    success = await subscription_service_v2.upsert_subscription(
                        subscription_id=subscription_id,
                        customer_id=customer_id,
                        is_create_action=False  # This is a sync, not a create action
                    )
                    
                    if success:
                        synced_count += 1
                        logger.info(f"Successfully synced subscription {subscription_id} to Supabase")

                        # Pre-launch: also activate pending company if Stripe sub is paid
                        sub_status = (sub_dict.get("status") or "").strip().lower()
                        if sub_status in ("active", "trialing", "past_due"):
                            from services.company_service import (
                                activate_pending_company_for_paid_subscription,
                            )

                            meta = sub_dict.get("metadata") or {}
                            if not isinstance(meta, dict):
                                try:
                                    meta = dict(meta)
                                except Exception:
                                    meta = {}
                            activated = activate_pending_company_for_paid_subscription(
                                owner_user_id=current_user,
                                stripe_subscription_id=subscription_id,
                                stripe_customer_id=customer_id,
                                metadata=meta,
                            )
                            if activated:
                                logger.info(
                                    "Sync activated company %s for user %s",
                                    activated,
                                    current_user,
                                )
                                from services.subscription_service import subscription_service

                                subscription_service.invalidate_cache(current_user)
                    else:
                        errors.append(f"Failed to sync subscription {subscription_id} (returned False)")
                except Exception as upsert_err:
                    error_msg = f"Failed to sync subscription {subscription_id}: {str(upsert_err)}"
                    errors.append(error_msg)
                    logger.error(error_msg, exc_info=True)
                    
            except Exception as sub_err:
                error_msg = f"Error processing subscription {sub_dict.get('id', 'unknown')}: {str(sub_err)}"
                errors.append(error_msg)
                logger.error(error_msg, exc_info=True)
        
        if synced_count > 0:
            message = f"Successfully synced {synced_count} subscription(s) to database"
        else:
            message = "No subscriptions were synced"
            if errors:
                message += f". Errors: {', '.join(errors)}"
        
        return SyncResponse(
            success=synced_count > 0,
            message=message,
            subscriptions_synced=synced_count,
            errors=errors if errors else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to sync subscriptions to database: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to sync subscriptions: {str(e)}")


@router.get("/debug", response_model=Dict)
async def debug_subscription(current_user: str = Depends(get_current_user)):
    """
    Debug endpoint to check subscription status and Stripe connection
    """
    try:
        from services.supabase_client import get_supabase_client
        supabase = get_supabase_client()
        
        # Get profile
        # 0 rows is possible (new user). Do not use .single() which raises PGRST116.
        profile_result = supabase.table('profiles')\
            .select('stripe_customer_id, email')\
            .eq('id', current_user)\
            .maybe_single()\
            .execute()
        
        profile = profile_result.data if profile_result.data else {}
        stripe_customer_id = profile.get('stripe_customer_id')
        user_email = profile.get('email')
        
        # Check database subscriptions
        db_subscriptions = supabase.table('user_subscriptions')\
            .select('*')\
            .eq('user_id', current_user)\
            .execute()
        
        # Try to get from Stripe if customer ID exists
        stripe_info = None
        if stripe_customer_id:
            try:
                import stripe
                customer = stripe.Customer.retrieve(stripe_customer_id)
                subscriptions = stripe.Subscription.list(customer=stripe_customer_id, limit=5)
                stripe_info = {
                    'customer_id': stripe_customer_id,
                    'customer_exists': True,
                    'subscriptions_count': len(subscriptions.data),
                    'subscriptions': [
                        {
                            'id': sub.id,
                            'status': sub.status,
                            'current_period_end': datetime.fromtimestamp(sub.current_period_end).isoformat() if sub.current_period_end else None
                        }
                        for sub in subscriptions.data
                    ]
                }
            except Exception as e:
                stripe_info = {
                    'customer_id': stripe_customer_id,
                    'customer_exists': False,
                    'error': str(e)
                }
        elif user_email:
            # Try to find by email
            try:
                import stripe
                customers = stripe.Customer.list(email=user_email, limit=1)
                if customers.data:
                    stripe_info = {
                        'customer_id': None,
                        'customer_found_by_email': customers.data[0].id,
                        'customer_exists': True,
                        'message': 'Customer found by email but not in profile'
                    }
                else:
                    stripe_info = {
                        'customer_id': None,
                        'customer_found_by_email': None,
                        'message': 'No customer found in Stripe with this email'
                    }
            except Exception as e:
                stripe_info = {
                    'error': f"Failed to search by email: {str(e)}"
                }
        else:
            stripe_info = {
                'message': 'No Stripe customer ID or email found'
            }
        
        return {
            'user_id': current_user,
            'user_email': user_email,
            'profile_stripe_customer_id': stripe_customer_id,
            'database_subscriptions': db_subscriptions.data if db_subscriptions.data else [],
            'database_subscriptions_count': len(db_subscriptions.data) if db_subscriptions.data else 0,
            'stripe_info': stripe_info
        }
        
    except Exception as e:
        logger.error(f"Failed to debug subscription: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cancel")
async def cancel_subscription(
    immediately: bool = False,
    current_user: str = Depends(get_current_user)
):
    """
    DEPRECATED: Subscription cancellation is now handled via Stripe Customer Portal.
    Users should use the "Manage Billing" button which opens Stripe Customer Portal.
    
    This endpoint is kept for backward compatibility but returns a 410 Gone error.
    All subscription management (cancel, update, change plan) should be done via Stripe Customer Portal.
    """
    raise HTTPException(
        status_code=410,
        detail="Subscription cancellation is now handled via Stripe Customer Portal. Please use 'Manage Billing' button to access the Stripe Customer Portal where you can cancel, update, or change your subscription."
    )


# Admin endpoints
@router.post("/admin/extend/{subscription_id}")
async def extend_subscription(
    subscription_id: str,
    days: int,
    admin_user: str = Depends(check_admin_access)
):
    """
    Extend a subscription by specified days (admin only)
    """
    try:
        success = await subscription_service.extend_subscription(
            subscription_id,
            days,
            admin_user_id=admin_user
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Subscription not found")
        
        return {"message": f"Subscription extended by {days} days"}
        
    except Exception as e:
        logger.error(f"Failed to extend subscription: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/stats")
async def get_subscription_stats(admin_user: str = Depends(check_admin_access)):
    """
    Get overall subscription statistics (admin only)
    """
    try:
        stats = await subscription_service.get_subscription_stats()
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get subscription stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/last-payment")
async def get_last_payment(current_user: str = Depends(get_current_user)):
    """
    Get the last payment information for the current user
    """
    try:
        from services.supabase_client import get_supabase_client
        supabase = get_supabase_client()
        
        # Get last payment for this user
        result = supabase.table('payment_history')\
            .select('*')\
            .eq('user_id', current_user)\
            .order('payment_date', desc=True)\
            .limit(1)\
            .execute()
        
        if result.data and len(result.data) > 0:
            payment = result.data[0]
            return {
                "payment_id": payment.get('id'),
                "stripe_invoice_id": payment.get('stripe_invoice_id'),
                "stripe_payment_intent_id": payment.get('stripe_payment_intent_id'),
                "amount": payment.get('amount'),
                "currency": payment.get('currency'),
                "status": payment.get('status'),
                "description": payment.get('description'),
                "receipt_url": payment.get('receipt_url'),
                "payment_date": payment.get('payment_date'),
                "user_id": payment.get('user_id'),
                "subscription_id": payment.get('subscription_id')
            }
        else:
            return None
        
    except Exception as e:
        logger.error(f"Failed to get last payment: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/payment-history")
async def get_payment_history(
    limit: int = 10,
    current_user: str = Depends(get_current_user)
):
    """
    Get payment history for the current user
    """
    try:
        from services.supabase_client import get_supabase_client
        supabase = get_supabase_client()
        
        # Get payment history for this user
        result = supabase.table('payment_history')\
            .select('*')\
            .eq('user_id', current_user)\
            .order('payment_date', desc=True)\
            .limit(limit)\
            .execute()
        
        return result.data if result.data else []
        
    except Exception as e:
        logger.error(f"Failed to get payment history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Passcode (promo) admin ----------
class CreatePasscodesRequest(BaseModel):
    count: int = 20
    duration_months: int = 6  # 3 or 6
    notes: Optional[str] = None
    prefix: str = "PILOT-"


@router.post("/admin/passcodes/create")
async def admin_create_passcodes(
    request: CreatePasscodesRequest,
    admin_user: str = Depends(check_admin_access),
):
    """Create a batch of passcodes. Returns list of created codes (code, id, duration_months, ...)."""
    try:
        from services.passcode_service import create_passcodes
        created = create_passcodes(
            count=request.count,
            duration_months=request.duration_months,
            notes=request.notes,
            created_by=admin_user,
            prefix=request.prefix or "PILOT-",
        )
        return {"created": len(created), "passcodes": created}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Create passcodes failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/passcodes")
async def admin_list_passcodes(
    unused_only: bool = False,
    admin_user: str = Depends(check_admin_access),
):
    """List all passcodes; optional filter to unused only."""
    try:
        from services.passcode_service import list_passcodes
        return list_passcodes(unused_only=unused_only)
    except Exception as e:
        logger.error(f"List passcodes failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/passcodes/redemptions")
async def admin_list_passcode_redemptions(admin_user: str = Depends(check_admin_access)):
    """List passcode redemptions with code and user email."""
    try:
        from services.passcode_service import list_redemptions
        return list_redemptions()
    except Exception as e:
        logger.error(f"List passcode redemptions failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Company seats + referral partners ----------
class JoinCompanyRequest(BaseModel):
    code: str


class CreateReferralPartnerRequest(BaseModel):
    name: str
    code: str
    notes: Optional[str] = None
    commission_note: Optional[str] = None
    login_email: Optional[str] = None


@router.post("/join-company")
async def join_company_endpoint(
    request: JoinCompanyRequest,
    current_user: str = Depends(get_current_user),
):
    try:
        from services.company_service import join_company

        return join_company(current_user, request.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Join company failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-company")
async def get_my_company(current_user: str = Depends(get_current_user)):
    """Never 500 on stale membership data — return empty company so UI can re-join."""
    try:
        from services.company_service import get_user_company, list_company_members

        company = get_user_company(current_user)
        if not company:
            return {"company": None, "members": []}
        payload = {
            "company": {
                "id": company["id"],
                "name": company.get("name"),
                "join_code": company.get("join_code") if company.get("role") == "owner" else None,
                "max_seats": int(company["max_seats"]) if company.get("max_seats") is not None else None,
                "seats_used": int(company["seats_used"]) if company.get("seats_used") is not None else 0,
                "status": company.get("status"),
                "role": company.get("role"),
            },
            "members": [],
        }
        if company.get("role") == "owner":
            try:
                payload["members"] = list_company_members(current_user)
            except ValueError:
                payload["members"] = []
            except Exception as mem_err:
                logger.warning("list_company_members failed for %s: %s", current_user, mem_err)
                payload["members"] = []
        return payload
    except Exception as e:
        logger.error("get_my_company failed: %s", e, exc_info=True)
        return {"company": None, "members": [], "error": str(e)}


class RemoveCompanyMemberRequest(BaseModel):
    user_id: str


@router.post("/my-company/remove-member")
async def remove_my_company_member(
    request: RemoveCompanyMemberRequest,
    current_user: str = Depends(get_current_user),
):
    try:
        from services.company_service import remove_company_member

        return remove_company_member(current_user, request.user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("remove_company_member failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-partner")
async def get_my_partner(
    current_user: dict = Depends(get_current_user_with_email),
):
    """Marketing partner dashboard — only companies attributed to this partner."""
    try:
        from services.company_service import get_my_partner_dashboard

        return get_my_partner_dashboard(current_user.get("email"))
    except Exception as e:
        logger.error("get_my_partner failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/referral-partners")
async def admin_list_referral_partners(admin_user: str = Depends(check_admin_access)):
    from services.company_service import list_referral_partners

    return list_referral_partners()


@router.post("/admin/referral-partners")
async def admin_create_referral_partner(
    request: CreateReferralPartnerRequest,
    admin_user: str = Depends(check_admin_access),
):
    try:
        from services.company_service import create_referral_partner

        return create_referral_partner(
            name=request.name,
            code=request.code,
            notes=request.notes,
            commission_note=request.commission_note,
            login_email=request.login_email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Create referral partner failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/companies")
async def admin_list_companies(admin_user: str = Depends(check_admin_access)):
    from services.company_service import list_companies_admin

    return list_companies_admin()

