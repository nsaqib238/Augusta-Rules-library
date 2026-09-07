"""
Stripe Service - Handles all Stripe API interactions
"""
import stripe

try:  # Stripe <=10.x
    from stripe.error import StripeError, InvalidRequestError, SignatureVerificationError
except ModuleNotFoundError:
    try:  # Stripe 11.x reorganized errors module
        from stripe.errors import StripeError, InvalidRequestError, SignatureVerificationError
    except ModuleNotFoundError:
        try:  # Stripe 13.x hides errors under _error
            from stripe._error import StripeError, InvalidRequestError, SignatureVerificationError
        except ModuleNotFoundError:  # Fallback for unexpected layouts
            stripe_error = getattr(stripe, "error", None)
            if stripe_error is None:
                raise
            StripeError = stripe_error.StripeError
            InvalidRequestError = stripe_error.InvalidRequestError
            SignatureVerificationError = stripe_error.SignatureVerificationError
import os
from typing import Dict, Optional, List
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)

# Configure Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')


class StripeCheckoutValidationError(Exception):
    """Raised for user-fixable checkout issues (wrong/mismatched Stripe objects), not transient API errors."""


def stripe_object_is_deleted(obj: object) -> bool:
    """Stripe returns HTTP 200 for deleted Customers with ``deleted: true``; Checkout still rejects them."""
    if obj is None:
        return False
    try:
        if getattr(obj, "deleted", None) is True:
            return True
    except Exception:
        pass
    try:
        getter = getattr(obj, "get", None)
        if callable(getter) and getter("deleted") is True:
            return True
    except Exception:
        pass
    return False


# Raised when a stored customer id must be discarded and checkout retried with a fresh customer.
CHECKOUT_CUSTOMER_INVALID = "checkout_customer_invalid"


class StripeService:
    """Service for managing Stripe operations"""
    
    def __init__(self):
        self.webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
        
        # Load price IDs from environment variables (NO hardcoded fallbacks!)
        # These MUST be set in your backend .env file
        individual_price = os.getenv('STRIPE_PRICE_INDIVIDUAL_MONTHLY')
        professional_price = os.getenv('STRIPE_PRICE_PROFESSIONAL')
        sole_trader_price = os.getenv('STRIPE_PRICE_SOLE_TRADER')  # Optional, only if you create $0 product
        company_small_price = os.getenv('STRIPE_PRICE_COMPANY_SMALL')
        company_large_price = os.getenv('STRIPE_PRICE_COMPANY_LARGE')
        
        # Log what we loaded from environment
        logger.info(f"Loading Stripe price IDs from environment:")
        logger.info(f"  STRIPE_PRICE_INDIVIDUAL_MONTHLY: {individual_price}")
        logger.info(f"  STRIPE_PRICE_PROFESSIONAL: {professional_price}")
        logger.info(f"  STRIPE_PRICE_SOLE_TRADER: {sole_trader_price}")
        logger.info(f"  STRIPE_PRICE_COMPANY_SMALL: {company_small_price}")
        logger.info(f"  STRIPE_PRICE_COMPANY_LARGE: {company_large_price}")
        
        # Warn if required price IDs are missing
        if not individual_price:
            logger.warning("⚠️ STRIPE_PRICE_INDIVIDUAL_MONTHLY not set in .env! Checkout will fail for Individual plan.")
        if not professional_price:
            logger.warning("⚠️ STRIPE_PRICE_PROFESSIONAL not set in .env! Checkout will fail for Professional plan.")
        
        self.price_ids = {
            'sole_trader_free': sole_trader_price,  # Optional - only needed if you create $0 product
            'individual_monthly': individual_price,  # REQUIRED
            'professional': professional_price,  # REQUIRED
            'company_small': company_small_price,
            'company_large': company_large_price,
        }
        
        # Build reverse mapping: price_id -> plan_name
        self.price_to_plan = {
            price_id: plan_name
            for plan_name, price_id in self.price_ids.items()
            if price_id  # Only include non-None price IDs
        }
        
        logger.info(f"✅ Initialized Stripe service with {len(self.price_to_plan)} price mappings:")
        for price_id, plan_name in self.price_to_plan.items():
            logger.info(f"  {plan_name}: {price_id}")
    
    async def create_customer(
        self,
        email: str,
        name: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> stripe.Customer:
        """
        Create a new Stripe customer
        
        Args:
            email: Customer email
            name: Customer name
            metadata: Additional metadata
            
        Returns:
            Stripe Customer object
        """
        try:
            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata=metadata or {}
            )
            logger.info(f"Created Stripe customer: {customer.id}")
            return customer
        except StripeError as e:
            logger.error(f"Failed to create customer: {str(e)}")
            raise
    
    async def get_customer(self, customer_id: str) -> stripe.Customer:
        """Get Stripe customer by ID"""
        try:
            return stripe.Customer.retrieve(customer_id)
        except StripeError as e:
            logger.error(f"Failed to retrieve customer {customer_id}: {str(e)}")
            raise
    
    async def update_customer(
        self,
        customer_id: str,
        email: Optional[str] = None,
        name: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> stripe.Customer:
        """Update Stripe customer"""
        try:
            update_data = {}
            if email:
                update_data['email'] = email
            if name:
                update_data['name'] = name
            if metadata:
                update_data['metadata'] = metadata

            # Stripe rejects modify() with no fields; callers sometimes pass all-None.
            if not update_data:
                return stripe.Customer.retrieve(customer_id)

            return stripe.Customer.modify(customer_id, **update_data)
        except StripeError as e:
            logger.error(f"Failed to update customer {customer_id}: {str(e)}")
            raise
    
    async def create_checkout_session(
        self,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        metadata: Optional[Dict] = None,
        trial_period_days: Optional[int] = None
    ) -> stripe.checkout.Session:
        """
        Create a Stripe Checkout session
        
        Args:
            customer_id: Stripe customer ID
            price_id: Stripe price ID
            success_url: URL to redirect after successful payment
            cancel_url: URL to redirect if payment cancelled
            metadata: Additional metadata
            trial_period_days: Optional trial period
            
        Returns:
            Stripe Checkout Session
        """
        try:
            cid = str(customer_id or "").strip()
            pid = str(price_id or "").strip()
            if not cid or not pid:
                raise StripeCheckoutValidationError("customer_id and price_id are required for checkout")

            # Validate objects exist and belong to the same Stripe mode (live vs test).
            # Mismatched catalogs (common after key changes) produce confusing Stripe errors.
            cust = stripe.Customer.retrieve(cid)
            if stripe_object_is_deleted(cust):
                logger.warning("Checkout preflight: customer %s is deleted in Stripe (retrieve returned deleted object)", cid)
                raise StripeCheckoutValidationError(CHECKOUT_CUSTOMER_INVALID)
            pri = stripe.Price.retrieve(pid)
            # Some Stripe objects omit `livemode` on certain API/SDK combinations; treating
            # missing as False caused false positives (400) even when customer+price were valid.
            cust_lm = getattr(cust, "livemode", None)
            pri_lm = getattr(pri, "livemode", None)
            if cust_lm is not None and pri_lm is not None and bool(cust_lm) != bool(pri_lm):
                raise StripeCheckoutValidationError(
                    "Stripe mode mismatch: the saved customer and the selected price are not both "
                    "live or both test. Fix STRIPE_SECRET_KEY and/or re-sync products/prices from Stripe."
                )
            if cust_lm is None or pri_lm is None:
                logger.info(
                    "Checkout preflight: livemode compare skipped (missing on object). "
                    "customer livemode=%r price livemode=%r",
                    cust_lm,
                    pri_lm,
                )
            canonical_customer_id = getattr(cust, "id", None) or cid
            canonical_price_id = getattr(pri, "id", None) or pid

            session_params = {
                'customer': canonical_customer_id,
                'payment_method_types': ['card'],
                'line_items': [{
                    'price': canonical_price_id,
                    'quantity': 1,
                }],
                'mode': 'subscription',
                'success_url': success_url,
                'cancel_url': cancel_url,
                'metadata': metadata or {},
                'allow_promotion_codes': True,
                'subscription_data': {
                    'metadata': metadata or {},
                },
            }
            
            if trial_period_days:
                session_params['subscription_data']['trial_period_days'] = trial_period_days
            
            session = stripe.checkout.Session.create(**session_params)
            logger.info(f"Created checkout session: {session.id}")
            return session
        except StripeError as e:
            logger.error(f"Failed to create checkout session: {str(e)}")
            raise
        except StripeCheckoutValidationError:
            raise
    
    async def create_subscription(
        self,
        customer_id: str,
        price_id: str,
        metadata: Optional[Dict] = None,
        trial_period_days: Optional[int] = None
    ) -> stripe.Subscription:
        """
        Create a subscription directly (without checkout)
        
        Args:
            customer_id: Stripe customer ID
            price_id: Stripe price ID
            metadata: Additional metadata
            trial_period_days: Optional trial period
            
        Returns:
            Stripe Subscription
        """
        try:
            subscription_params = {
                'customer': customer_id,
                'items': [{'price': price_id}],
                'metadata': metadata or {},
            }
            
            if trial_period_days:
                subscription_params['trial_period_days'] = trial_period_days
            
            subscription = stripe.Subscription.create(**subscription_params)
            logger.info(f"Created subscription: {subscription.id}")
            return subscription
        except StripeError as e:
            logger.error(f"Failed to create subscription: {str(e)}")
            raise
    
    async def get_subscription(self, subscription_id: str) -> stripe.Subscription:
        """Get subscription by ID"""
        try:
            return stripe.Subscription.retrieve(subscription_id)
        except StripeError as e:
            logger.error(f"Failed to retrieve subscription {subscription_id}: {str(e)}")
            raise
    
    async def update_subscription(
        self,
        subscription_id: str,
        price_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
        cancel_at_period_end: Optional[bool] = None
    ) -> stripe.Subscription:
        """
        Update an existing subscription
        
        Args:
            subscription_id: Stripe subscription ID
            price_id: New price ID (for upgrades/downgrades)
            metadata: Additional metadata
            cancel_at_period_end: Whether to cancel at period end
            
        Returns:
            Updated Stripe Subscription
        """
        try:
            update_data = {}
            
            if price_id:
                subscription = await self.get_subscription(subscription_id)
                update_data['items'] = [{
                    'id': subscription['items']['data'][0].id,
                    'price': price_id,
                }]
            
            if metadata:
                update_data['metadata'] = metadata
            
            if cancel_at_period_end is not None:
                update_data['cancel_at_period_end'] = cancel_at_period_end
            
            subscription = stripe.Subscription.modify(subscription_id, **update_data)
            logger.info(f"Updated subscription: {subscription_id}")
            return subscription
        except StripeError as e:
            logger.error(f"Failed to update subscription {subscription_id}: {str(e)}")
            raise
    
    async def cancel_subscription(
        self,
        subscription_id: str,
        immediately: bool = False
    ) -> stripe.Subscription:
        """
        Cancel a subscription
        
        Args:
            subscription_id: Stripe subscription ID
            immediately: If True, cancel immediately. If False, cancel at period end
            
        Returns:
            Cancelled Stripe Subscription
        """
        try:
            if immediately:
                subscription = stripe.Subscription.delete(subscription_id)
                logger.info(f"Immediately cancelled subscription: {subscription_id}")
            else:
                subscription = stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True
                )
                logger.info(f"Scheduled subscription cancellation: {subscription_id}")
            
            return subscription
        except StripeError as e:
            logger.error(f"Failed to cancel subscription {subscription_id}: {str(e)}")
            raise
    
    async def create_billing_portal_session(
        self,
        customer_id: str,
        return_url: str
    ) -> stripe.billing_portal.Session:
        """
        Create a billing portal session for customer self-service
        
        Args:
            customer_id: Stripe customer ID
            return_url: URL to return to after portal session
            
        Returns:
            Billing portal session
        """
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )
            logger.info(f"Created billing portal session for customer: {customer_id}")
            return session
        except StripeError as e:
            logger.error(f"Failed to create billing portal session: {str(e)}")
            raise
    
    async def list_subscriptions(
        self,
        customer_id: str,
        limit: int = 10,
        status: Optional[str] = None
    ) -> List[stripe.Subscription]:
        """
        List all subscriptions for a customer
        
        Args:
            customer_id: Stripe customer ID
            limit: Maximum number of subscriptions to return
            status: Optional filter by status (active, canceled, etc.)
            
        Returns:
            List of Stripe Subscription objects
        """
        try:
            params = {
                'customer': customer_id, 
                'limit': limit
            }
            if status:
                params['status'] = status
            
            subscriptions = stripe.Subscription.list(**params)
            
            # Expand product information for each subscription item
            # Stripe's list API doesn't support nested expands, so we need to expand individually
            expanded_subscriptions = []
            for sub in subscriptions.data:
                # Retrieve each subscription with expanded product info
                expanded_sub = stripe.Subscription.retrieve(
                    sub.id,
                    expand=['items.data.price.product']
                )
                expanded_subscriptions.append(expanded_sub)
            
            logger.info(f"Retrieved {len(expanded_subscriptions)} subscriptions for customer {customer_id}")
            return expanded_subscriptions
        except StripeError as e:
            logger.error(f"Failed to list subscriptions for customer {customer_id}: {str(e)}")
            raise
    
    async def list_invoices(
        self,
        customer_id: str,
        limit: int = 10
    ) -> List[stripe.Invoice]:
        """List invoices for a customer"""
        try:
            invoices = stripe.Invoice.list(
                customer=customer_id,
                limit=limit
            )
            return invoices.data
        except StripeError as e:
            logger.error(f"Failed to list invoices for {customer_id}: {str(e)}")
            raise
    
    async def get_invoice(self, invoice_id: str) -> stripe.Invoice:
        """Get invoice by ID"""
        try:
            return stripe.Invoice.retrieve(invoice_id)
        except StripeError as e:
            logger.error(f"Failed to retrieve invoice {invoice_id}: {str(e)}")
            raise
    
    async def create_payment_intent(
        self,
        amount: int,
        currency: str = 'aud',
        customer_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> stripe.PaymentIntent:
        """
        Create a payment intent for one-time payments
        
        Args:
            amount: Amount in cents
            currency: Currency code
            customer_id: Optional customer ID
            metadata: Additional metadata
            
        Returns:
            Payment Intent
        """
        try:
            payment_intent_params = {
                'amount': amount,
                'currency': currency,
                'metadata': metadata or {},
            }
            
            if customer_id:
                payment_intent_params['customer'] = customer_id
            
            payment_intent = stripe.PaymentIntent.create(**payment_intent_params)
            logger.info(f"Created payment intent: {payment_intent.id}")
            return payment_intent
        except StripeError as e:
            logger.error(f"Failed to create payment intent: {str(e)}")
            raise
    
    def construct_webhook_event(
        self,
        payload: bytes,
        signature: str
    ) -> stripe.Event:
        """
        Verify and construct webhook event
        
        Args:
            payload: Raw request body
            signature: Stripe signature header
            
        Returns:
            Verified Stripe Event
            
        Raises:
            ValueError: If signature verification fails
        """
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                self.webhook_secret
            )
            logger.info(f"Verified webhook event: {event.type}")
            return event
        except ValueError as e:
            logger.error(f"Invalid webhook signature: {str(e)}")
            raise
        except SignatureVerificationError as e:
            logger.error(f"Webhook signature verification failed: {str(e)}")
            raise ValueError("Invalid signature")
    
    def get_price_id(self, plan_name: str) -> Optional[str]:
        """Get Stripe price ID for a plan name"""
        return self.price_ids.get(plan_name)
    
    def get_plan_name_for_price(self, price_id: str) -> Optional[str]:
        """Get internal plan name for a Stripe price ID"""
        if not price_id:
            return None
        
        # Normalize price_id: remove "Price " prefix if present and ensure it starts with "price_"
        normalized_id = price_id.replace('Price ', '').strip()
        if not normalized_id.startswith('price_'):
            normalized_id = f"price_{normalized_id}"
        
        # Try exact match first
        plan_name = self.price_to_plan.get(normalized_id)
        if plan_name:
            return plan_name
        
        # Try case-insensitive lookup as fallback
        normalized_id_lower = normalized_id.lower()
        for mapped_price_id, mapped_plan in self.price_to_plan.items():
            if mapped_price_id.lower() == normalized_id_lower:
                logger.debug(f"Matched price ID {price_id} (normalized: {normalized_id}) to plan {mapped_plan} via case-insensitive lookup")
                return mapped_plan
        
        logger.warning(f"Price ID {price_id} (normalized: {normalized_id}) not found in mapping. Available price IDs: {list(self.price_to_plan.keys())}")
        return None
    
    async def get_upcoming_invoice(self, customer_id: str) -> Optional[stripe.Invoice]:
        """Get upcoming invoice for a customer"""
        try:
            invoice = stripe.Invoice.upcoming(customer=customer_id)
            return invoice
        except InvalidRequestError:
            # No upcoming invoice
            return None
        except StripeError as e:
            logger.error(f"Failed to get upcoming invoice for {customer_id}: {str(e)}")
            raise


# Global instance
stripe_service = StripeService()

