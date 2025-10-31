from sqlmodel import SQLModel, Field, UniqueConstraint
from datetime import date


class Instument(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ticket: str = Field(unique=True, index=True)
    name: str


class DailyPrice(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("instrument_id", "date", "uq_instrument_date"),)
    id: int | None = Field(default=None, primary_key=True)
    instrument_id: int = Field(foreign_key="instrument.id", index=True)
    date: date
    open: float
    high: float
    low: float
    close: float
    adjusted_close: float
    volume: int
