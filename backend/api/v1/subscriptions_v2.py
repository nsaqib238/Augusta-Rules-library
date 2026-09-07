"""
Subscription API V2 — **legacy duplicate surface**.

Catalog listing and checkout-by-price are implemented on the main router:

- ``GET /api/v1/subscriptions/products``
- ``POST /api/v1/subscriptions/create-checkout-session`` with ``price_id`` in the body

These ``/subscriptions-v2/*`` routes remain for backward compatibility (e.g. old bookmarks
or Stripe return URLs). Prefer ``/api/v1/subscriptions/*`` for new integrations.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from services.subscription_service_v2 import SubscriptionServiceV2
from services.stripe_service import stripe_service, StripeCheckoutValidationError
from services.redirect_url import RedirectUrlError, validate_redirect_url
from middleware.subscription_check import get_current_user
import logging

logger = logging.getLogger(__name__)

router = APIRouter()
subscription_service = SubscriptionServiceV2()


class SubscriptionResponse(BaseModel):
    """Subscription response matching Reference 2 pattern"""
    id: str  # Stripe subscription ID
    user_id: Optional[str]
    company_id: Optional[str]
    status: str
    price_id: Optional[str]
    quantity: Optional[int]
    cancel_at_period_end: bool
    current_period_start: str
    current_period_end: str
    created: str
    # Price and product info (from join)
    price: Optional[dict] = None
    product: Optional[dict] = None


class CreateCheckoutRequest(BaseModel):
    """Create checkout session request"""
    price_id: str  # Use price_id instead of plan_name (Reference 2 pattern)
    success_url: str
    cancel_url: str
    company_id: Optional[str] = None  # Optional: for company subscriptions


@router.get("/my-subscription", response_model=Optional[SubscriptionResponse])
async def get_my_subscription(current_user: str = Depends(get_current_user)):
    """
    Get current user's subscription - SIMPLE VERSION (Reference 2 pattern)
    """
    try:
        subscription = await subscription_service.get_user_subscription(current_user)
        
        if not subscription:
            logger.info(f"No subscription found for user {current_user}")
            return None
        
        # Map to response model
        return SubscriptionResponse(
            id=subscription['id'],
            user_id=subscription.get('user_id'),
            company_id=subscription.get('company_id'),
            status=subscription['status'],
            price_id=subscription.get('price_id'),
            quantity=subscription.get('quantity', 1),
            cancel_at_period_end=subscription.get('cancel_at_period_end', False),
            current_period_start=subscription['current_period_start'],
            current_period_end=subscription['current_period_end'],
            created=subscription['created'],
            price=subscription.get('prices'),
            product=subscription.get('prices', {}).get('products') if subscription.get('prices') else None
        )
        
    except Exception as e:
        logger.error(f"Failed to get subscription: {str(e)}", exc_info=True)
        return None


@router.post("/create-checkout-session")
async def create_checkout_session(
    request: CreateCheckoutRequest,
    current_user: str = Depends(get_current_user)
):
    """
    Create Stripe checkout session - SIMPLE VERSION (Reference 2 pattern)
    """
    try:
        from services.supabase_client import get_supabase_client
        supabase = get_supabase_client()
        
        # Get user profile
        profile_result = supabase.table('profiles')\
            .select('email, full_name')\
            .eq('id', current_user)\
            .single()\
            .execute()
        
        if not profile_result.data:
            raise HTTPException(status_code=404, detail="User profile not found")
        
        profile = profile_result.data
        
        # Get or create Stripe customer (Reference 2 pattern)
        customer_id = await subscription_service.get_or_create_customer(
            user_id=current_user,
            email=profile['email']
        )
        
        if not customer_id:
            raise HTTPException(status_code=500, detail="Failed to create Stripe customer")

        try:
            success_url = validate_redirect_url(request.success_url, field_name="success_url")
            cancel_url = validate_redirect_url(request.cancel_url, field_name="cancel_url")
        except RedirectUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Create checkout session
        metadata = {'user_id': current_user}
        if request.company_id:
            metadata['company_id'] = request.company_id

        session = await stripe_service.create_checkout_session(
            customer_id=customer_id,
            price_id=request.price_id,  # Use price_id directly (Reference 2 pattern)
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata
        )
        
        return {
            "session_id": session['id'],
            "session_url": session['url']
        }
        
    except HTTPException:
        raise
    except StripeCheckoutValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create checkout session: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cancel")
async def cancel_subscription(
    immediately: bool = False,
    current_user: str = Depends(get_current_user)
):
    """
    DEPRECATED: Subscription cancellation is now handled via Stripe Customer Portal.
    Users should use the "Manage Billing" button which opens Stripe Customer Portal.
    
    This endpoint is kept for admin use only and will be removed in future versions.
    """
    raise HTTPException(
        status_code=410,
        detail="Subscription cancellation is now handled via Stripe Customer Portal. Please use 'Manage Billing' button to access the Stripe Customer Portal."
    )


@router.get("/products")
async def get_products():
    """
    Get all active products and prices - SIMPLE VERSION (Reference 2 pattern)
    """
    try:
        from services.supabase_client import get_supabase_client
        supabase = get_supabase_client()
        
        # Get all active products
        products_result = supabase.table('products')\
            .select('*')\
            .eq('active', True)\
            .execute()
        
        products = products_result.data or []
        
        # For each product, get its prices
        products_with_prices = []
        for product in products:
            prices_result = supabase.table('prices')\
                .select('*')\
                .eq('product_id', product['id'])\
                .eq('active', True)\
                .execute()
            
            product['prices'] = prices_result.data or []
            products_with_prices.append(product)
        
        logger.info(f"Returning {len(products_with_prices)} products with prices")
        return products_with_prices
        
    except Exception as e:
        logger.error(f"Failed to get products: {str(e)}", exc_info=True)
        # Return empty array instead of error if products table doesn't exist yet
        logger.warning("Products table may not exist yet or may be empty. Returning empty array.")
        return []

