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
from .loyalty import router as loyalty_router
from .services import router as services_router
from .multistore import router as multistore_router
from .staff import router as staff_router
from .fulfilment import router as fulfilment_router
from .categorygroups import router as categorygroups_router
from .stocklocations import router as stocklocations_router
from .warranty import router as warranty_router
from .customer360 import router as customer360_router
from .jobcards import router as jobcards_router

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
router.include_router(loyalty_router)
router.include_router(services_router)
router.include_router(multistore_router)
router.include_router(staff_router)
router.include_router(fulfilment_router)
router.include_router(categorygroups_router)
router.include_router(stocklocations_router)
router.include_router(warranty_router)
router.include_router(customer360_router)
router.include_router(jobcards_router)
