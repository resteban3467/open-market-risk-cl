"""Shared data contracts and database access for the risk workspace."""

from risk_data_core.database import RiskDatabase
from risk_data_core.models import Base

__all__ = ["Base", "RiskDatabase"]
