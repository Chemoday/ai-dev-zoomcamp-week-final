"""Single import point for every module's SQLAlchemy models.

alembic/env.py imports this file before reading Base.metadata, so autogenerate sees all tables.
Each module that introduces models MUST add its import here (see docs/BACKEND_CONVENTIONS.md).
"""

from src.core.database import Base
from src.core.domain_events import DomainEventRecord  # noqa: F401
from src.modules.auth.models import (  # noqa: F401
    AuthAttempt,
    AuthInvitation,
    AuthSession,
    AuthToken,
    RefreshToken,
    UserIdentity,
)
from src.modules.communication.models import Comment, EmergencyAck, Post  # noqa: F401
from src.modules.governance.models import (  # noqa: F401
    Ballot,
    BallotAnswer,
    BallotRevision,
    Poll,
    PollElector,
    PollOption,
    PollQuestion,
)
from src.modules.meters.models import (  # noqa: F401
    MeterDevice,
    MeterReading,
    MeterReadingLog,
    ReadingPeriod,
)
from src.modules.notifications.models import ChannelBinding, Delivery, Preference  # noqa: F401
from src.modules.property.models import (  # noqa: F401
    Apartment,
    ApartmentMembership,
    Building,
    User,
)

__all__ = ["Base"]
