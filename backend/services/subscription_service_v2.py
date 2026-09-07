"""
Subscription Service V2 - Following Reference 2 pattern (simple and reliable)
"""
from typing import Optional, Dict
from datetime import datetime, timedelta
from services.supabase_client import get_supabase_client
from services.stripe_service import stripe_service, stripe_object_is_deleted
import logging

try:
    from stripe.error import StripeError
except ModuleNotFoundError:
    try:
        from stripe.errors import StripeError
    except ModuleNotFoundError:
        from stripe._error import StripeError

logger = logging.getLogger(__name__)

class SubscriptionServiceV2:
    """Simplified subscription service following Reference 2 pattern"""
    
    def __init__(self):
        self.supabase = get_supabase_client()

    async def get_user_subscription(self, user_id: str) -> Optional[Dict]:
        """
        Get active subscription for a user - SIMPLE VERSION (Reference 2 pattern)
        Only checks individual subscriptions (company subscriptions removed)
        """
        try:
            # Check individual subscription (simple query like Reference 2)
            result = self.supabase.table('subscriptions')\
                .select('*, prices(*, products(*))')\
                .eq('user_id', user_id)\
                .in_('status', ['trialing', 'active'])\
                .limit(1)\
                .execute()
            
            if result.data and len(result.data) > 0:
                logger.info(f"Found individual subscription for user {user_id}: {result.data[0].get('id')}")
                return result.data[0]
            
            logger.info(f"No active subscription found for user {user_id}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to get user subscription: {str(e)}", exc_info=True)
            return None

    async def upsert_subscription(
        self,
        subscription_id: str,
        customer_id: str,
        is_create_action: bool = False
    ) -> bool:
        """
        Upsert subscription from Stripe webhook (Reference 2 pattern)
        """
        try:
            # Get customer's userId from mapping table
            customer_result = self.supabase.table('customers')\
                .select('id')\
                .eq('stripe_customer_id', customer_id)\
                .limit(1)\
                .execute()
            
            if not customer_result.data or len(customer_result.data) == 0:
                logger.error(f"Customer not found for stripe_customer_id: {customer_id}")
                return False
            
            user_id = customer_result.data[0]['id']
            
            # Retrieve full subscription from Stripe
            subscription = await stripe_service.get_subscription(subscription_id)
            if not subscription:
                logger.error(f"Failed to retrieve subscription {subscription_id} from Stripe")
                return False
            
            # Extract price_id from subscription items
            items = subscription.get('items', {}).get('data', [])
            if not items:
                logger.error(f"Subscription {subscription_id} has no items")
                return False
            
            price_id = items[0].get('price', {}).get('id')
            if not price_id:
                logger.error(f"Subscription {subscription_id} has no price_id")
                return False
            
            # Prepare subscription data (Reference 2 pattern)
            # Handle missing period dates gracefully - use created date as fallback
            current_period_start = subscription.get('current_period_start')
            current_period_end = subscription.get('current_period_end')
            
            # If period dates are missing, use created date or current time as fallback
            if not current_period_start:
                logger.warning(f"Subscription {subscription_id} missing current_period_start, using created date")
                current_period_start = subscription.get('created', int(datetime.utcnow().timestamp()))
            
            if not current_period_end:
                logger.warning(f"Subscription {subscription_id} missing current_period_end, using created date + 1 month")
                # Default to 1 month from start if missing
                period_start = current_period_start
                if isinstance(period_start, (int, float)):
                    period_end_dt = datetime.fromtimestamp(period_start) + timedelta(days=30)
                    current_period_end = int(period_end_dt.timestamp())
                else:
                    current_period_end = current_period_start
            
            # Prepare subscription data
            subscription_data = {
                'id': subscription['id'],
                'status': subscription['status'],
                'metadata': subscription.get('metadata', {}),
                'price_id': price_id,
                'quantity': subscription.get('quantity', 1),
                'cancel_at_period_end': subscription.get('cancel_at_period_end', False),
                'created': datetime.fromtimestamp(subscription['created']).isoformat() if subscription.get('created') else datetime.utcnow().isoformat(),
                'current_period_start': datetime.fromtimestamp(current_period_start).isoformat() if current_period_start else datetime.utcnow().isoformat(),
                'current_period_end': datetime.fromtimestamp(current_period_end).isoformat() if current_period_end else datetime.utcnow().isoformat(),
                'ended_at': datetime.fromtimestamp(subscription['ended_at']).isoformat() if subscription.get('ended_at') else None,
                'cancel_at': datetime.fromtimestamp(subscription['cancel_at']).isoformat() if subscription.get('cancel_at') else None,
                'canceled_at': datetime.fromtimestamp(subscription['canceled_at']).isoformat() if subscription.get('canceled_at') else None,
                'trial_start': datetime.fromtimestamp(subscription['trial_start']).isoformat() if subscription.get('trial_start') else None,
                'trial_end': datetime.fromtimestamp(subscription['trial_end']).isoformat() if subscription.get('trial_end') else None,
            }
            
            # Check if price exists in prices table (foreign key constraint)
            price_check = self.supabase.table('prices')\
                .select('id')\
                .eq('id', price_id)\
                .limit(1)\
                .execute()
            
            if not price_check.data or len(price_check.data) == 0:
                logger.warning(f"Price {price_id} not found in prices table. Creating placeholder...")
                # Try to create a minimal price record if it doesn't exist
                try:
                    # Get product info from Stripe if available
                    product_id = items[0].get('price', {}).get('product', '')
                    if isinstance(product_id, dict):
                        product_id = product_id.get('id', '')
                    
                    # Ensure product exists first
                    if product_id:
                        product_check = self.supabase.table('products')\
                            .select('id')\
                            .eq('id', product_id)\
                            .limit(1)\
                            .execute()
                        
                        if not product_check.data or len(product_check.data) == 0:
                            logger.warning(f"Product {product_id} not found. Creating placeholder...")
                            # Create minimal product record
                            product_data = items[0].get('price', {}).get('product', {})
                            if isinstance(product_data, dict):
                                product_name = product_data.get('name', 'Unknown Product')
                                product_description = product_data.get('description')
                            else:
                                product_name = 'Unknown Product'
                                product_description = None
                            
                            try:
                                self.supabase.table('products')\
                                    .upsert({
                                        'id': product_id,
                                        'active': True,
                                        'name': product_name,
                                        'description': product_description,
                                    })\
                                    .execute()
                                logger.info(f"Created placeholder product record: {product_id}")
                            except Exception as prod_err:
                                logger.warning(f"Could not create product placeholder: {prod_err}")
                    
                    # Create minimal price record
                    price_record = {
                        'id': price_id,
                        'product_id': product_id if product_id else None,
                        'active': True,
                        'unit_amount': items[0].get('price', {}).get('unit_amount', 0),
                        'currency': subscription.get('currency', 'aud'),
                        'type': 'recurring',
                        'interval': items[0].get('price', {}).get('recurring', {}).get('interval', 'month'),
                        'interval_count': items[0].get('price', {}).get('recurring', {}).get('interval_count', 1),
                    }
                    
                    self.supabase.table('prices')\
                        .upsert(price_record)\
                        .execute()
                    logger.info(f"Created placeholder price record: {price_id}")
                except Exception as price_err:
                    error_details = str(price_err)
                    if hasattr(price_err, 'message'):
                        error_details = price_err.message
                    logger.error(f"Failed to create placeholder price: {error_details}")
                    # Continue anyway - might work if constraint is not enforced
            
            # All subscriptions are now individual only (company subscriptions removed)
            subscription_data['user_id'] = user_id
            
            # Upsert subscription
            result = self.supabase.table('subscriptions')\
                .upsert(subscription_data)\
                .execute()
            
            if not result.data:
                error_msg = f"Upsert returned no data for subscription {subscription_id}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            logger.info(f"Upserted subscription {subscription_id} for user {user_id}")
            
            # Invalidate subscription cache for this user
            # Note: This service uses 'subscriptions' table, but cache is shared
            # We'll invalidate via the main subscription_service if available
            try:
                from services.subscription_service import subscription_service
                subscription_service.invalidate_cache(user_id)
                logger.debug(f"Invalidated subscription cache for user {user_id} after upsert")
            except Exception:
                pass  # Cache invalidation is optional
            
            return True
            
        except Exception as e:
            error_details = str(e)
            # Try to extract more details from Supabase error
            if hasattr(e, 'message'):
                error_details = e.message
            elif hasattr(e, 'details'):
                error_details = f"{str(e)} - Details: {e.details}"
            
            logger.error(f"Failed to upsert subscription {subscription_id}: {error_details}", exc_info=True)
            raise Exception(f"Failed to sync subscription: {error_details}")

    async def cancel_subscription(self, subscription_id: str, immediately: bool = False) -> bool:
        """
        DEPRECATED: Subscription cancellation is now handled via Stripe Customer Portal.
        
        This method is kept for backward compatibility but should not be used.
        All subscription management (cancel, update, change plan) should be done via Stripe Customer Portal.
        Webhooks will automatically sync subscription status changes to the database.
        
        Raises NotImplementedError to prevent usage.
        """
        logger.warning(f"cancel_subscription called but deprecated. Use Stripe Customer Portal instead. subscription_id={subscription_id}")
        raise NotImplementedError(
            "Subscription cancellation is now handled via Stripe Customer Portal. "
            "Please use the 'Manage Billing' button to access the Stripe Customer Portal."
        )

    async def clear_stripe_customer_mapping(self, user_id: str, stripe_customer_id: Optional[str] = None) -> None:
        """Remove stale Stripe customer id from Supabase (customers row + profiles)."""
        try:
            self.supabase.table("customers").delete().eq("id", user_id).execute()
        except Exception as del_err:
            logger.warning("Could not delete customers row for user %s: %s", user_id, del_err)
        try:
            if stripe_customer_id:
                self.supabase.table("profiles").update({"stripe_customer_id": None}).eq(
                    "id", user_id
                ).eq("stripe_customer_id", stripe_customer_id).execute()
            else:
                self.supabase.table("profiles").update({"stripe_customer_id": None}).eq(
                    "id", user_id
                ).execute()
        except Exception as p_err:
            logger.warning("Could not clear profile stripe_customer_id for user %s: %s", user_id, p_err)

    async def get_or_create_customer(self, user_id: str, email: str) -> Optional[str]:
        """
        Get or create Stripe customer (Reference 2 pattern)
        """
        try:
            # Check if customer exists
            customer_result = self.supabase.table('customers')\
                .select('stripe_customer_id')\
                .eq('id', user_id)\
                .limit(1)\
                .execute()
            
            if customer_result.data and len(customer_result.data) > 0 and customer_result.data[0].get('stripe_customer_id'):
                existing_id = customer_result.data[0]['stripe_customer_id']
                try:
                    cust_obj = await stripe_service.get_customer(existing_id)
                    if stripe_object_is_deleted(cust_obj):
                        logger.warning(
                            "Stored Stripe customer %s is deleted; clearing mapping for user %s",
                            existing_id,
                            user_id,
                        )
                        await self.clear_stripe_customer_mapping(user_id, existing_id)
                    else:
                        return existing_id
                except StripeError as e:
                    msg = str(e)
                    if "No such customer" in msg or getattr(e, "code", "") == "resource_missing":
                        logger.warning(
                            "Stripe rejected stored customer id for user %s (%s); clearing mapping: %s",
                            user_id,
                            existing_id,
                            msg,
                        )
                        try:
                            self.supabase.table("customers").delete().eq("id", user_id).execute()
                        except Exception as del_err:
                            logger.warning("Could not delete stale customers row: %s", del_err)
                        try:
                            self.supabase.table("profiles").update({"stripe_customer_id": None}).eq(
                                "id", user_id
                            ).eq("stripe_customer_id", existing_id).execute()
                        except Exception as p_err:
                            logger.warning("Could not clear stale profile stripe_customer_id: %s", p_err)
                    else:
                        raise
            
            # Create customer in Stripe
            customer = await stripe_service.create_customer(
                email=email,
                metadata={'user_id': user_id}
            )
            
            if not customer:
                return None
            
            # Insert into customers table
            self.supabase.table('customers')\
                .upsert({
                    'id': user_id,
                    'stripe_customer_id': customer['id']
                })\
                .execute()
            
            # SECURITY: Don't log Stripe customer IDs in production
            logger.debug(f"Created Stripe customer for user {user_id}")
            logger.info(f"Created Stripe customer for user {user_id}")
            return customer['id']
            
        except Exception as e:
            logger.error(f"Failed to get or create customer: {str(e)}", exc_info=True)
            return None

