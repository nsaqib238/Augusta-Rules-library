"""
Webhook API Endpoints - Handles Stripe webhooks
"""
from fastapi import APIRouter, Request, HTTPException, Header
from typing import Any, Dict, Optional
from datetime import datetime
from services.stripe_service import stripe_service
from services.subscription_service import subscription_service
from services.supabase_client import get_supabase_client
import stripe
import logging
import json

logger = logging.getLogger(__name__)

router = APIRouter()


def _stripe_get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from Stripe SDK objects or dicts. Do not use ``.get()`` on StripeObject (raises AttributeError)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except (KeyError, TypeError):
        return default


def _stripe_dict(obj: Any) -> Dict[str, Any]:
    """Coerce Stripe metadata (or dict-like) to a plain dict for ``{**...}`` merges."""
    if not obj:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    try:
        return dict(obj)
    except (TypeError, ValueError):
        pass
    out: Dict[str, Any] = {}
    try:
        for k in obj.keys():
            out[str(k)] = obj[k]
    except Exception:
        pass
    return out


async def sync_subscription_record(
    stripe_subscription,
    metadata: Optional[dict] = None,
    fallback_email: Optional[str] = None,
    source: str = ''
):
    """Create or update a subscription record in Supabase based on Stripe data."""
    supabase = get_supabase_client()

    subscription_metadata = _stripe_dict(_stripe_get(stripe_subscription, 'metadata'))
    # Priority: passed metadata > subscription metadata
    metadata_dict = _stripe_dict(metadata)
    combined_metadata = {**subscription_metadata, **metadata_dict}
    logger.info(f"[sync_subscription_record] Source: {source}, Combined metadata: {combined_metadata}")

    customer_id = _stripe_get(stripe_subscription, 'customer')
    user_id = combined_metadata.get('user_id')
    # company_id removed - all subscriptions are individual

    # Only paid plan is professional
    plan_name = combined_metadata.get('plan_name') or 'professional'
    items_container = _stripe_get(stripe_subscription, 'items') or {}
    items = _stripe_get(items_container, 'data', []) or []
    if not items:
        logger.error(
            f"Stripe subscription {_stripe_get(stripe_subscription, 'id')} missing line items (source={source})"
        )
        return

    price_data = items[0]['price']
    # Any paid subscription => professional
    if plan_name not in ('professional', 'sole'):
        plan_name = 'professional'

    # Resolve user if metadata missing (use Stripe customer lookup)
    if not user_id and customer_id:
        profile_result = supabase.table('profiles')\
            .select('id, email, stripe_customer_id')\
            .eq('stripe_customer_id', customer_id)\
            .single()\
            .execute()

        if profile_result.data:
            user_id = profile_result.data['id']
        else:
            email = fallback_email
            if not email:
                try:
                    customer = await stripe_service.get_customer(customer_id)
                    email = customer.email
                except Exception as customer_err:
                    logger.warning(
                        f"Failed to recover customer details for {customer_id}: {customer_err}"
                    )
                    email = None

            if email:
                profile_result = supabase.table('profiles')\
                    .select('id, stripe_customer_id')\
                    .eq('email', email)\
                    .single()\
                    .execute()
                if profile_result.data:
                    user_id = profile_result.data['id']
                    # Always update stripe_customer_id if it's missing or different
                    existing_customer_id = profile_result.data.get('stripe_customer_id')
                    if not existing_customer_id or existing_customer_id != customer_id:
                        supabase.table('profiles')\
                            .update({'stripe_customer_id': customer_id})\
                            .eq('id', user_id)\
                            .execute()
                        logger.info(f"Updated stripe_customer_id for user {user_id} to {customer_id} (source: {source})")

    # If we have user_id from metadata, ensure stripe_customer_id is saved to profile
    if user_id and customer_id:
        profile_check = supabase.table('profiles')\
            .select('stripe_customer_id')\
            .eq('id', user_id)\
            .single()\
            .execute()
        
        if profile_check.data:
            existing_customer_id = profile_check.data.get('stripe_customer_id')
            if not existing_customer_id or existing_customer_id != customer_id:
                supabase.table('profiles')\
                    .update({'stripe_customer_id': customer_id})\
                    .eq('id', user_id)\
                    .execute()
                logger.info(f"Ensured stripe_customer_id {customer_id} is saved to profile for user {user_id} (source: {source})")

    if not user_id:
        logger.error(
            f"Unable to resolve user for Stripe subscription {_stripe_get(stripe_subscription, 'id')} (source={source})"
        )
        logger.error(f"Customer ID: {customer_id}, Metadata: {combined_metadata}")
        return

    stripe_status = (_stripe_get(stripe_subscription, "status") or "").strip().lower()
    paid_ok = stripe_status in ("active", "trialing", "past_due")
    subscription_id = _stripe_get(stripe_subscription, "id")
    meta_plan = (combined_metadata.get("plan_name") or "").strip().lower()
    is_company_plan = meta_plan in ("company_small", "company_large") or bool(
        combined_metadata.get("company_id")
    )

    # Never re-activate paid access from a canceled/unpaid Stripe subscription
    if not paid_ok:
        logger.info(
            "sync_subscription_record: skipping activate for non-paid status=%s sub=%s source=%s",
            stripe_status,
            subscription_id,
            source,
        )
        if subscription_id:
            try:
                existing = (
                    supabase.table("user_subscriptions")
                    .select("id")
                    .eq("stripe_subscription_id", subscription_id)
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    supabase.table("user_subscriptions").update(
                        {
                            "status": "cancelled" if stripe_status in ("canceled", "cancelled") else stripe_status,
                            "cancelled_at": datetime.utcnow().isoformat(),
                        }
                    ).eq("id", existing.data[0]["id"]).execute()
            except Exception as mark_err:
                logger.warning("Failed to mark user_subscriptions cancelled: %s", mark_err)
        return

    period_start_ts = _stripe_get(stripe_subscription, "current_period_start")
    period_end_ts = _stripe_get(stripe_subscription, "current_period_end")
    if period_start_ts is None or period_end_ts is None:
        logger.error(
            "Stripe subscription %s missing period timestamps (source=%s)",
            subscription_id,
            source,
        )
        return

    current_period_start = datetime.fromtimestamp(period_start_ts)
    current_period_end = datetime.fromtimestamp(period_end_ts)
    unit_amount = _stripe_get(price_data, "unit_amount") or 0
    plan_price = unit_amount / 100  # Convert cents

    if user_id:
        await subscription_service.create_individual_subscription(
            user_id=user_id,
            plan_name=plan_name,
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            plan_price=plan_price,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
        )
        # Company checkout: keep company as source of truth (create_individual sets stripe)
        if is_company_plan:
            company_id = combined_metadata.get("company_id")
            profile_patch: Dict[str, Any] = {
                "subscription_source": "company",
                "account_type": "professional",
                "subscription_status": "active",
            }
            if company_id:
                profile_patch["company_id"] = company_id
            supabase.table("profiles").update(profile_patch).eq("id", user_id).execute()
            subscription_service.invalidate_cache(user_id)


async def _upsert_stripe_catalog_product(product_data: dict) -> bool:
    """Sync Stripe Product to Supabase ``products`` (catalog for GET /subscriptions/products)."""
    try:
        supabase = get_supabase_client()
        product = {
            'id': product_data['id'],
            'active': _stripe_get(product_data, 'active', False),
            'name': _stripe_get(product_data, 'name'),
            'description': _stripe_get(product_data, 'description'),
            'image': (_stripe_get(product_data, 'images') or [None])[0]
            if _stripe_get(product_data, 'images')
            else None,
            'metadata': _stripe_dict(_stripe_get(product_data, 'metadata')),
        }
        supabase.table('products').upsert(product).execute()
        logger.info("Stripe catalog product upserted: %s", product_data['id'])
        return True
    except Exception as e:
        logger.error("Failed to upsert Stripe product: %s", e, exc_info=True)
        return False


async def _upsert_stripe_catalog_price(price_data: dict) -> bool:
    """Sync Stripe Price to Supabase ``prices``."""
    try:
        supabase = get_supabase_client()
        product_id = _stripe_get(price_data, 'product')
        if isinstance(product_id, dict):
            product_id = product_id.get('id')
        elif product_id is not None and not isinstance(product_id, str):
            product_id = getattr(product_id, 'id', None)

        recurring = _stripe_get(price_data, 'recurring') or {}
        price = {
            'id': price_data['id'],
            'product_id': product_id,
            'active': _stripe_get(price_data, 'active', False),
            'currency': _stripe_get(price_data, 'currency'),
            'description': _stripe_get(price_data, 'nickname'),
            'type': _stripe_get(price_data, 'type'),
            'unit_amount': _stripe_get(price_data, 'unit_amount'),
            'interval': _stripe_get(recurring, 'interval') if recurring else None,
            'interval_count': _stripe_get(recurring, 'interval_count') if recurring else None,
            'trial_period_days': _stripe_get(recurring, 'trial_period_days') if recurring else None,
            'metadata': _stripe_dict(_stripe_get(price_data, 'metadata')),
        }
        supabase.table('prices').upsert(price).execute()
        logger.info("Stripe catalog price upserted: %s", price_data['id'])
        return True
    except Exception as e:
        logger.error("Failed to upsert Stripe price: %s", e, exc_info=True)
        return False


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature")
):
    """
    Handle Stripe webhook events
    """
    try:
        # Get raw request body
        payload = await request.body()
        
        if not stripe_signature:
            raise HTTPException(status_code=400, detail="Missing stripe-signature header")
        
        # Verify webhook signature
        try:
            event = stripe_service.construct_webhook_event(payload, stripe_signature)
        except ValueError as e:
            logger.error(f"Invalid webhook signature: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid signature")
        
        # Handle the event
        event_type = event['type']
        event_data = event['data']['object']
        
        logger.info(f"Processing webhook event: {event_type}")

        # Stripe catalog → Supabase (single webhook URL; formerly only on /webhooks-v2)
        if event_type in ('product.created', 'product.updated'):
            await _upsert_stripe_catalog_product(event_data)
            return {"status": "success"}

        if event_type in ('price.created', 'price.updated'):
            await _upsert_stripe_catalog_price(event_data)
            return {"status": "success"}
        
        # Handle different event types
        if event_type == 'checkout.session.completed':
            await handle_checkout_completed(event_data)
        
        elif event_type == 'customer.subscription.created':
            await handle_subscription_created(event_data)
        
        elif event_type == 'customer.subscription.updated':
            await handle_subscription_updated(event_data)
        
        elif event_type == 'customer.subscription.deleted':
            await handle_subscription_deleted(event_data)
        
        elif event_type == 'invoice.payment_succeeded':
            await handle_payment_succeeded(event_data)
        
        elif event_type == 'invoice.payment_failed':
            await handle_payment_failed(event_data)
        
        elif event_type == 'customer.subscription.trial_will_end':
            await handle_trial_will_end(event_data)
        
        else:
            logger.info(f"Unhandled event type: {event_type}")
        
        return {"status": "success"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")


async def handle_checkout_completed(session):
    """Handle successful checkout session"""
    try:
        logger.info(f"Processing checkout.session.completed: {session.id}")
        logger.info(f"Session metadata: {_stripe_dict(_stripe_get(session, 'metadata'))}")
        
        customer_id = _stripe_get(session, 'customer')
        subscription_id = _stripe_get(session, 'subscription')
        metadata = _stripe_dict(_stripe_get(session, 'metadata'))
        
        if not subscription_id:
            logger.warning("No subscription ID in checkout session")
            return
        
        # Get subscription details from Stripe
        try:
            subscription = await stripe_service.get_subscription(subscription_id)
            logger.info(
                f"Retrieved subscription: {subscription.id}, metadata: {_stripe_dict(_stripe_get(subscription, 'metadata'))}"
            )
        except Exception as e:
            logger.error(f"Failed to retrieve subscription {subscription_id} from Stripe: {e}")
            # If subscription doesn't exist yet, wait a moment and try again
            import asyncio
            await asyncio.sleep(2)
            try:
                subscription = await stripe_service.get_subscription(subscription_id)
                logger.info(f"Retrieved subscription on retry: {subscription.id}")
            except Exception as retry_error:
                logger.error(f"Failed to retrieve subscription on retry: {retry_error}")
                raise
        
        # Always merge checkout session metadata onto the Stripe subscription
        # (needed so subscription.created/updated/deleted keep company_id / plan_name)
        if metadata:
            try:
                import stripe

                existing = _stripe_dict(_stripe_get(subscription, "metadata"))
                merged = {**existing, **metadata}
                stripe.Subscription.modify(subscription_id, metadata=merged)
                logger.info("Merged checkout metadata onto subscription %s: %s", subscription_id, merged)
                subscription = await stripe_service.get_subscription(subscription_id)
            except Exception as e:
                logger.warning("Failed to update subscription metadata: %s", e)

        # Merge checkout session metadata with subscription metadata
        # Checkout session metadata takes precedence
        combined_metadata = {**_stripe_dict(_stripe_get(subscription, "metadata")), **metadata}
        logger.info(f"Combined metadata: {combined_metadata}")

        customer_details = _stripe_get(session, 'customer_details') or {}
        checkout_email = _stripe_get(customer_details, 'email')
        
        # CRITICAL: Update customer email in Stripe if it's different from checkout email
        # This ensures subscription emails go to the correct address
        if customer_id and checkout_email:
            try:
                customer = await stripe_service.get_customer(customer_id)
                if customer.email != checkout_email:
                    logger.info(f"Updating Stripe customer {customer_id} email from '{customer.email}' to '{checkout_email}'")
                    await stripe_service.update_customer(
                        customer_id=customer_id,
                        email=checkout_email
                    )
                    logger.info(f"✅ Updated Stripe customer email to match checkout email: {checkout_email}")
            except Exception as email_update_error:
                logger.warning(f"Failed to update customer email: {str(email_update_error)}")

        plan_name = (combined_metadata.get("plan_name") or "").strip().lower()
        company_id = combined_metadata.get("company_id")
        user_id = combined_metadata.get("user_id")

        if company_id and user_id and plan_name in ("company_small", "company_large"):
            from services.company_service import PLAN_SEATS, activate_company_from_checkout

            max_seats = combined_metadata.get("max_seats") or PLAN_SEATS.get(plan_name)
            try:
                max_seats_int = int(max_seats) if max_seats is not None else PLAN_SEATS.get(plan_name)
            except (TypeError, ValueError):
                max_seats_int = PLAN_SEATS.get(plan_name)
            activate_company_from_checkout(
                company_id=company_id,
                owner_user_id=user_id,
                stripe_subscription_id=subscription_id,
                stripe_customer_id=customer_id,
                max_seats=max_seats_int,
                referral_code=combined_metadata.get("referral_code"),
            )
            subscription_service.invalidate_cache(user_id)
            logger.info("Activated company %s from checkout %s", company_id, session.id)
            await sync_subscription_record(
                subscription,
                metadata=combined_metadata,
                fallback_email=checkout_email,
                source="checkout.session.completed",
            )
            if user_id:
                try:
                    from services.welcome_email_service import maybe_send_welcome_email

                    await maybe_send_welcome_email(user_id, reason="checkout")
                except Exception as welcome_err:
                    logger.warning("Welcome email after company checkout failed: %s", welcome_err)
            return

        await sync_subscription_record(
            subscription,
            metadata=combined_metadata,
            fallback_email=checkout_email,
            source='checkout.session.completed'
        )

        # Backstop: paid sub but company metadata missing — still activate owner's pending company
        if user_id and subscription_id:
            from services.company_service import activate_pending_company_for_paid_subscription

            activated = activate_pending_company_for_paid_subscription(
                owner_user_id=user_id,
                stripe_subscription_id=subscription_id,
                stripe_customer_id=customer_id,
                metadata=combined_metadata,
            )
            if activated:
                logger.info(
                    "Activated pending company %s via checkout backstop for user %s",
                    activated,
                    user_id,
                )

        # Invalidate subscription cache for the user
        if user_id:
            subscription_service.invalidate_cache(user_id)
            logger.debug(f"Invalidated subscription cache for user {user_id} after checkout")
            try:
                from services.welcome_email_service import maybe_send_welcome_email

                await maybe_send_welcome_email(user_id, reason="checkout")
            except Exception as welcome_err:
                logger.warning("Welcome email after checkout failed: %s", welcome_err)
        
        logger.info(f"Successfully created subscription from checkout: {subscription_id}")
        
    except Exception as e:
        logger.error(f"Error handling checkout completed: {str(e)}", exc_info=True)
        raise


async def handle_subscription_created(subscription):
    """Handle new subscription creation"""
    try:
        logger.info(f"Processing customer.subscription.created: {subscription.id}")
        meta = _stripe_dict(_stripe_get(subscription, "metadata"))

        # Company plans: activate even if checkout.session.completed was missed/reordered
        plan_name = (meta.get("plan_name") or "").strip().lower()
        company_id = meta.get("company_id")
        user_id = meta.get("user_id")
        customer_id = _stripe_get(subscription, "customer")
        if not user_id and customer_id:
            try:
                supabase = get_supabase_client()
                prof = (
                    supabase.table("profiles")
                    .select("id")
                    .eq("stripe_customer_id", customer_id)
                    .limit(1)
                    .execute()
                )
                if prof.data:
                    user_id = prof.data[0]["id"]
            except Exception as lookup_err:
                logger.warning("user lookup by customer failed: %s", lookup_err)

        if company_id and user_id and plan_name in ("company_small", "company_large"):
            from services.company_service import PLAN_SEATS, activate_company_from_checkout

            max_seats = meta.get("max_seats") or PLAN_SEATS.get(plan_name)
            try:
                max_seats_int = int(max_seats) if max_seats is not None else PLAN_SEATS.get(plan_name)
            except (TypeError, ValueError):
                max_seats_int = PLAN_SEATS.get(plan_name)
            activate_company_from_checkout(
                company_id=company_id,
                owner_user_id=user_id,
                stripe_subscription_id=_stripe_get(subscription, "id"),
                stripe_customer_id=customer_id,
                max_seats=max_seats_int,
                referral_code=meta.get("referral_code"),
            )
            subscription_service.invalidate_cache(user_id)
            logger.info(
                "Activated company %s from subscription.created %s",
                company_id,
                _stripe_get(subscription, "id"),
            )
            await sync_subscription_record(
                subscription,
                metadata=meta,
                source="customer.subscription.created",
            )
            return

        await sync_subscription_record(
            subscription,
            metadata=meta,
            source='customer.subscription.created'
        )

        # Backstop when metadata incomplete but owner has a pending company
        if user_id:
            from services.company_service import activate_pending_company_for_paid_subscription

            activated = activate_pending_company_for_paid_subscription(
                owner_user_id=user_id,
                stripe_subscription_id=_stripe_get(subscription, "id"),
                stripe_customer_id=customer_id,
                metadata=meta,
            )
            if activated:
                logger.info(
                    "Activated pending company %s via subscription.created backstop",
                    activated,
                )

        # Invalidate subscription cache
        if user_id:
            subscription_service.invalidate_cache(user_id)
            logger.debug(f"Invalidated subscription cache for user {user_id} after subscription.created")
        
    except Exception as e:
        logger.error(f"Error handling subscription created: {str(e)}")
        raise


async def handle_subscription_updated(subscription):
    """Handle subscription updates"""
    try:
        logger.info(f"Processing customer.subscription.updated: {subscription.id}")

        from services.company_service import (
            activate_company_from_checkout,
            activate_pending_company_for_paid_subscription,
            PLAN_SEATS,
            set_company_status_by_subscription,
        )

        meta = _stripe_dict(_stripe_get(subscription, "metadata"))
        plan_name = (meta.get("plan_name") or "").strip().lower()
        company_id = meta.get("company_id")
        user_id = meta.get("user_id")
        stripe_status = (_stripe_get(subscription, "status") or "").strip().lower()

        # Pending company with paid subscription: activate on first update too
        if (
            company_id
            and user_id
            and plan_name in ("company_small", "company_large")
            and stripe_status in ("active", "trialing", "past_due")
        ):
            max_seats = meta.get("max_seats") or PLAN_SEATS.get(plan_name)
            try:
                max_seats_int = int(max_seats) if max_seats is not None else PLAN_SEATS.get(plan_name)
            except (TypeError, ValueError):
                max_seats_int = PLAN_SEATS.get(plan_name)
            activate_company_from_checkout(
                company_id=company_id,
                owner_user_id=user_id,
                stripe_subscription_id=_stripe_get(subscription, "id"),
                stripe_customer_id=_stripe_get(subscription, "customer"),
                max_seats=max_seats_int,
                referral_code=meta.get("referral_code"),
            )
            subscription_service.invalidate_cache(user_id)
        elif user_id and stripe_status in ("active", "trialing", "past_due"):
            activated = activate_pending_company_for_paid_subscription(
                owner_user_id=user_id,
                stripe_subscription_id=_stripe_get(subscription, "id"),
                stripe_customer_id=_stripe_get(subscription, "customer"),
                metadata=meta,
            )
            if activated:
                logger.info(
                    "Activated pending company %s via subscription.updated backstop",
                    activated,
                )
                subscription_service.invalidate_cache(user_id)

        set_company_status_by_subscription(
            _stripe_get(subscription, "id"),
            _stripe_get(subscription, "status") or "",
            company_id=company_id,
            owner_user_id=user_id,
        )

        # Canceled/unpaid company: always force owner Sole (even if membership flags lag)
        if (
            plan_name in ("company_small", "company_large")
            and user_id
            and stripe_status not in ("active", "trialing", "past_due")
        ):
            from services.company_service import downgrade_owner_after_company_cancel

            downgrade_owner_after_company_cancel(user_id)

        # sync_subscription_record no-ops activate when canceled; still OK to call for row cleanup
        await sync_subscription_record(
            subscription,
            metadata=meta,
            source='customer.subscription.updated'
        )
        
        supabase = get_supabase_client()
        subscription_id = subscription['id']
        
        # Find subscription in database
        result = supabase.table('user_subscriptions')\
            .select('*')\
            .eq('stripe_subscription_id', subscription_id)\
            .execute()
        
        if not result.data:
            logger.warning(f"Subscription not found in database: {subscription_id}")
            return
        
        db_subscription = result.data[0]
        
        # Update subscription details
        update_data = {
            'status': subscription['status'],
            'current_period_start': datetime.fromtimestamp(subscription['current_period_start']).isoformat(),
            'current_period_end': datetime.fromtimestamp(subscription['current_period_end']).isoformat(),
            'cancel_at_period_end': _stripe_get(subscription, 'cancel_at_period_end', False),
        }
        
        if _stripe_get(subscription, 'canceled_at'):
            update_data['cancelled_at'] = datetime.fromtimestamp(subscription['canceled_at']).isoformat()
        
        supabase.table('user_subscriptions')\
            .update(update_data)\
            .eq('id', db_subscription['id'])\
            .execute()
        
        # Log event
        supabase.table('subscription_events').insert({
            'subscription_id': db_subscription['id'],
            'event_type': 'updated',
            'triggered_by': 'stripe_webhook',
            'event_data': {'stripe_subscription_status': subscription['status']}
        }).execute()
        
        logger.info(f"Successfully updated subscription: {subscription_id}")
        
    except Exception as e:
        logger.error(f"Error handling subscription updated: {str(e)}")
        raise


async def handle_subscription_deleted(subscription):
    """Handle subscription cancellation (immediate or end of period)."""
    try:
        logger.info(f"Processing customer.subscription.deleted: {subscription.id}")

        from services.company_service import (
            downgrade_owner_after_company_cancel,
            find_companies_for_stripe_subscription,
            set_company_status_by_subscription,
        )

        meta = _stripe_dict(_stripe_get(subscription, "metadata"))
        plan_name = (meta.get("plan_name") or "").strip().lower()
        company_id = meta.get("company_id")
        user_id = meta.get("user_id")
        subscription_id = _stripe_get(subscription, "id") or subscription.get("id")
        customer_id = _stripe_get(subscription, "customer")

        supabase = get_supabase_client()

        # Resolve owner when Stripe metadata is empty (still cancel safely)
        if not user_id and customer_id:
            try:
                prof = (
                    supabase.table("profiles")
                    .select("id")
                    .eq("stripe_customer_id", customer_id)
                    .limit(1)
                    .execute()
                )
                if prof.data:
                    user_id = prof.data[0]["id"]
            except Exception as lookup_err:
                logger.warning("owner lookup by customer failed: %s", lookup_err)

        touched_companies = set_company_status_by_subscription(
            subscription_id,
            "canceled",
            company_id=company_id,
            owner_user_id=user_id,
        )
        # Extra fallback: companies that still match this Stripe sub
        if not touched_companies:
            for c in find_companies_for_stripe_subscription(
                subscription_id, company_id=company_id, owner_user_id=user_id
            ):
                if c.get("owner_user_id") and not user_id:
                    user_id = c["owner_user_id"]

        # Find subscription in database (optional — still downgrade if missing)
        result = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("stripe_subscription_id", subscription_id)
            .execute()
        )

        db_subscription = (result.data or [None])[0]
        owner_from_db = db_subscription.get("user_id") if db_subscription else None
        owner_id = user_id or owner_from_db
        was_company = bool(
            plan_name in ("company_small", "company_large")
            or company_id
            or touched_companies
        )

        if db_subscription:
            supabase.table("user_subscriptions").update(
                {
                    "status": "cancelled",
                    "cancelled_at": datetime.utcnow().isoformat(),
                }
            ).eq("id", db_subscription["id"]).execute()
            try:
                supabase.table("subscription_events").insert(
                    {
                        "subscription_id": db_subscription["id"],
                        "event_type": "cancelled",
                        "triggered_by": "stripe_webhook",
                    }
                ).execute()
            except Exception as ev_err:
                logger.warning("subscription_events insert failed: %s", ev_err)
        else:
            logger.warning(
                "Subscription not in user_subscriptions (still downgrading profile/company): %s",
                subscription_id,
            )

        # Always drop paid access for the owner (company or professional)
        if owner_id:
            if was_company:
                downgrade_owner_after_company_cancel(owner_id)
            else:
                supabase.table("profiles").update(
                    {
                        "subscription_status": "inactive",
                        "account_type": "sole",
                        "stripe_subscription_id": None,
                        "access_expires_at": None,
                        "subscription_source": None,
                        "company_id": None,
                    }
                ).eq("id", owner_id).execute()
                subscription_service.invalidate_cache(owner_id)

        logger.info(
            "Successfully cancelled subscription: %s owner=%s company=%s",
            subscription_id,
            owner_id,
            was_company,
        )

    except Exception as e:
        logger.error(f"Error handling subscription deleted: {str(e)}")
        raise


async def handle_payment_succeeded(invoice):
    """Handle successful payment - captures user_id and all payment details"""
    try:
        logger.info(f"Processing invoice.payment_succeeded: {invoice.id}")
        logger.info(
            f"Invoice details: customer={_stripe_get(invoice, 'customer')}, "
            f"subscription={_stripe_get(invoice, 'subscription')}, amount={_stripe_get(invoice, 'amount_paid')}"
        )
        
        supabase = get_supabase_client()
        
        customer_id = _stripe_get(invoice, 'customer')
        subscription_id = _stripe_get(invoice, 'subscription')
        user_id = None
        db_subscription_id = None
        
        # Method 1: Try to get user_id from subscription metadata
        if subscription_id:
            try:
                subscription = await stripe_service.get_subscription(subscription_id)
                metadata = _stripe_dict(_stripe_get(subscription, 'metadata'))
                user_id = metadata.get('user_id')
                logger.info(f"Found user_id from subscription metadata: {user_id}")
            except Exception as e:
                logger.warning(f"Could not retrieve subscription metadata: {e}")
        
        # Method 2: Find user_id from subscription table by customer_id
        if not user_id and customer_id:
            sub_result = supabase.table('user_subscriptions')\
                .select('id, user_id')\
                .eq('stripe_customer_id', customer_id)\
                .order('created_at', desc=True)\
                .limit(1)\
                .execute()
            
            if sub_result.data:
                subscription = sub_result.data[0]
                db_subscription_id = subscription['id']
                user_id = subscription.get('user_id')
                logger.info(f"Found user_id from subscription table: {user_id}")
        
        # Method 3: Find user_id from profile by customer_id
        if not user_id and customer_id:
            profile_result = supabase.table('profiles')\
                .select('id, email')\
                .eq('stripe_customer_id', customer_id)\
                .single()\
                .execute()
            
            if profile_result.data:
                user_id = profile_result.data['id']
                logger.info(f"Found user_id from profile table: {user_id}")
        
        # Create payment history record with all available information
        payment_data = {
            'stripe_payment_intent_id': _stripe_get(invoice, 'payment_intent'),
            'stripe_invoice_id': invoice['id'],
            'amount': invoice['amount_paid'] / 100,  # Convert from cents
            'currency': invoice['currency'].upper(),
            'status': 'succeeded',
            'description': _stripe_get(invoice, 'description', 'Subscription payment'),
            'receipt_url': _stripe_get(invoice, 'hosted_invoice_url'),
            'payment_date': datetime.fromtimestamp(invoice['created']).isoformat(),
            'user_id': user_id,
            'subscription_id': db_subscription_id
        }
        
        # Remove None values
        payment_data = {k: v for k, v in payment_data.items() if v is not None}
        
        result = supabase.table('payment_history').insert(payment_data).execute()
        
        # Invalidate subscription cache since payment succeeded (may update subscription status)
        if user_id:
            subscription_service.invalidate_cache(user_id)
            logger.debug(f"Invalidated subscription cache for user {user_id} after payment.succeeded")
        
        logger.info(f"Successfully recorded payment: invoice_id={invoice.id}, user_id={user_id}, amount={payment_data.get('amount')}")
        
        # Log payment details for debugging
        logger.info(f"Payment record: {json.dumps(payment_data, indent=2, default=str)}")
        
    except Exception as e:
        logger.error(f"Error handling payment succeeded: {str(e)}", exc_info=True)
        raise


async def handle_payment_failed(invoice):
    """Handle failed payment"""
    try:
        logger.info(f"Processing invoice.payment_failed: {invoice.id}")
        
        supabase = get_supabase_client()
        
        # Create payment history record
        payment_data = {
            'stripe_invoice_id': invoice['id'],
            'amount': invoice['amount_due'] / 100,
            'currency': invoice['currency'].upper(),
            'status': 'failed',
            'description': 'Payment failed',
            'payment_date': datetime.fromtimestamp(invoice['created']).isoformat()
        }
        
        # Find subscription and update status
        customer_id = _stripe_get(invoice, 'customer')
        if customer_id:
            sub_result = supabase.table('user_subscriptions')\
                .select('*')\
                .eq('stripe_customer_id', customer_id)\
                .execute()
            
            if sub_result.data:
                subscription = sub_result.data[0]
                payment_data['subscription_id'] = subscription['id']
                payment_data['user_id'] = subscription.get('user_id')
                
                # Update subscription status to past_due
                supabase.table('user_subscriptions')\
                    .update({'status': 'past_due'})\
                    .eq('id', subscription['id'])\
                    .execute()
                
                # Log event
                supabase.table('subscription_events').insert({
                    'subscription_id': subscription['id'],
                    'event_type': 'payment_failed',
                    'triggered_by': 'stripe_webhook'
                }).execute()
        
        supabase.table('payment_history').insert(payment_data).execute()
        
        # Invalidate subscription cache since payment failed (may update subscription status)
        customer_id = _stripe_get(invoice, 'customer')
        if customer_id:
            # Find user_id from customer_id
            profile_result = supabase.table('profiles')\
                .select('id')\
                .eq('stripe_customer_id', customer_id)\
                .single()\
                .execute()
            if profile_result.data:
                user_id = profile_result.data['id']
                subscription_service.invalidate_cache(user_id)
                logger.debug(f"Invalidated subscription cache for user {user_id} after payment.failed")
        
        logger.info(f"Successfully recorded failed payment: {invoice.id}")
        
    except Exception as e:
        logger.error(f"Error handling payment failed: {str(e)}")
        raise


async def handle_trial_will_end(subscription):
    """Handle trial ending soon"""
    try:
        logger.info(f"Processing customer.subscription.trial_will_end: {subscription.id}")
        
        # Here you would typically send an email notification to the user
        # reminding them that their trial will end soon
        
        # For now, just log it
        logger.info(f"Trial will end for subscription: {subscription.id}")
        
    except Exception as e:
        logger.error(f"Error handling trial will end: {str(e)}")
        raise

