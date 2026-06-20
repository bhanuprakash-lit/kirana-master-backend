from fastapi import APIRouter

from .auth import router as auth_router
from .telemetry import router as telemetry_router
from .users import router as users_router
from .stores import router as stores_router
from .intelligence import router as intelligence_router
from .inventory import router as inventory_router
from .finance import router as finance_router
from .subscriptions import router as subscriptions_router
from .admin import router as admin_router
from .baskets import router as baskets_router
from .marketing import router as marketing_router
from .variants import router as variants_router
from .tax import router as tax_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(telemetry_router)
router.include_router(users_router)
router.include_router(stores_router)
router.include_router(intelligence_router)
router.include_router(inventory_router)
router.include_router(finance_router)
router.include_router(subscriptions_router)
router.include_router(admin_router)
router.include_router(baskets_router)
router.include_router(marketing_router)
router.include_router(variants_router)
router.include_router(tax_router)
