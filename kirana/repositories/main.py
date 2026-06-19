from .base import BaseRepositoryMixin
from .auth import AuthRepositoryMixin
from .store import StoreRepositoryMixin
from .inventory import InventoryRepositoryMixin
from .finance import FinanceRepositoryMixin
from .customer import CustomerRepositoryMixin
from .ai_prefs import Ai_prefsRepositoryMixin
from .subscription import SubscriptionRepositoryMixin
from .referral import ReferralRepositoryMixin
from .baskets import BasketsRepositoryMixin
from .associations import AssociationsRepositoryMixin

class KiranaRepository(
    BaseRepositoryMixin,
    AuthRepositoryMixin,
    StoreRepositoryMixin,
    InventoryRepositoryMixin,
    FinanceRepositoryMixin,
    CustomerRepositoryMixin,
    Ai_prefsRepositoryMixin,
    SubscriptionRepositoryMixin,
    ReferralRepositoryMixin,
    BasketsRepositoryMixin,
    AssociationsRepositoryMixin,
):
    """
    Unified repository combining all domain-specific mixins.
    """
    pass
