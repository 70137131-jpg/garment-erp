"""Aggregate import of every table model.

Importing this module registers all tables on ``SQLModel.metadata``. Both
``init_db`` (dev) and Alembic autogenerate (production migrations) import it so
they always see the complete schema regardless of which routers were loaded.
"""

from .finance import models as finance_models  # noqa: F401
from .costing import models as costing_models  # noqa: F401
from .inventory import models as inventory_models  # noqa: F401
from .kernel import idempotency as kernel_idempotency  # noqa: F401
from .kernel import numbering as kernel_numbering  # noqa: F401
from .masters import models as masters_models  # noqa: F401
from .procurement import models as procurement_models  # noqa: F401
from .production import models as production_models  # noqa: F401
from .quality import models as quality_models  # noqa: F401
from .sales import models as sales_models  # noqa: F401
from .security import models as security_models  # noqa: F401
from .styles import models as styles_models  # noqa: F401
