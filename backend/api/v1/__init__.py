from fastapi import APIRouter
from .uploads import router as uploads_router
from .documents import router as documents_router
from .admin import router as admin_router
from .admin_tables import router as admin_tables_router
from .admin_sir import router as admin_sir_router
from .admin_ncc import router as admin_ncc_router
from .admin_library import router as admin_library_router
from .query import router as query_router
from .subscriptions import router as subscriptions_router
from .access_codes import router as access_codes_router
from .webhooks import router as webhooks_router
from .account import router as account_router

try:
    from .subscriptions_v2 import router as subscriptions_v2_router
    SUBSCRIPTIONS_V2_AVAILABLE = True
except ImportError:
    SUBSCRIPTIONS_V2_AVAILABLE = False
    subscriptions_v2_router = None

router = APIRouter()
router.include_router(uploads_router, prefix="/uploads", tags=["uploads"])
router.include_router(documents_router, prefix="/documents", tags=["documents"])
router.include_router(query_router, prefix="/query", tags=["query"])
router.include_router(admin_router, prefix="/admin", tags=["admin"])
router.include_router(admin_tables_router, prefix="/admin/tables", tags=["admin-tables"])
router.include_router(admin_sir_router, prefix="/admin/sir", tags=["admin-sir"])
router.include_router(admin_ncc_router, prefix="/admin/ncc", tags=["admin-ncc"])
router.include_router(admin_library_router, prefix="/admin/library", tags=["admin-library"])
router.include_router(subscriptions_router, prefix="/subscriptions", tags=["subscriptions"])
router.include_router(access_codes_router, prefix="/access-codes", tags=["access-codes"])
router.include_router(webhooks_router, prefix="/webhooks", tags=["webhooks"])
router.include_router(account_router, prefix="/account", tags=["account"])

if SUBSCRIPTIONS_V2_AVAILABLE:
    router.include_router(subscriptions_v2_router, prefix="/subscriptions-v2", tags=["subscriptions-v2-legacy"])
