from sqlmodel import SQLModel, Field, UniqueConstraint
from sqlalchemy import Column, BigInteger
from datetime import date


class Company(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(unique=True, index=True)
    name: str
    cik: str | None = None
    sic_code: str | None = None
    sector: str | None = None
    industry: str | None = None
    exchange: str | None = None


class DailyPrice(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("company_id", "date", name="uq_company_date"),)
    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    date: date
    open: float
    high: float
    low: float
    close: float  # yfinance adjusted close
    volume: int = Field(sa_column=Column(BigInteger))


class FinancialFact(SQLModel, table=True):
    __tablename__ = "financial_fact"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "metric",
            "end_date",
            "period_type",
            name="uq_company_metric_end_period",
        ),
    )
    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    metric: str = Field(index=True)
    value: float
    unit: str
    end_date: date
    period_type: str  # e.g. "Q1", "FY"
    filing_date: date | None = None
    source: str | None = None


class TaxonomyMapping(SQLModel, table=True):
    __tablename__ = "taxonomy_mapping"
    id: int | None = Field(default=None, primary_key=True)
    xbrl_tag: str = Field(unique=True, index=True)
    metric: str = Field(index=True)
    description: str | None = None
