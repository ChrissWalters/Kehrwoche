"""SQLAlchemy models.

Importing this package imports every model module, so ``Base.metadata`` is complete for
Alembic autogeneration and for ``create_all`` in the tests.
"""

from app.models.base import Entity, UtcDateTime, utcnow
from app.models.chore import ON_DEMAND, Chore, ChoreCompletion
from app.models.expense import Expense, ExpenseShare, SettlementPayment, SettlementPeriod
from app.models.feed import Comment, FeedEvent, FeedEventType, Like
from app.models.household import JOIN_CODE_LENGTH, Household, HouseholdType
from app.models.notification import Notification, NotificationType
from app.models.session import Session
from app.models.shopping import ShoppingItem
from app.models.user import User, UserRole

__all__ = [
    "JOIN_CODE_LENGTH",
    "ON_DEMAND",
    "Chore",
    "ChoreCompletion",
    "Comment",
    "Entity",
    "Expense",
    "ExpenseShare",
    "FeedEvent",
    "FeedEventType",
    "Household",
    "HouseholdType",
    "Like",
    "Notification",
    "NotificationType",
    "Session",
    "SettlementPayment",
    "SettlementPeriod",
    "ShoppingItem",
    "User",
    "UserRole",
    "UtcDateTime",
    "utcnow",
]
