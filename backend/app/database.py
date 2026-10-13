import os
from datetime import datetime
from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String,
                        UniqueConstraint, create_engine, inspect, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./office.db"))
Session = sessionmaker(engine)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "build_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class CanonicalProduct(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_identity_key: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(80), nullable=True)
    canonical_name: Mapped[str] = mapped_column(String(255))
    chipset: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    variant: Mapped[str | None] = mapped_column(String(120), nullable=True)
    vram_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_number: Mapped[str | None] = mapped_column(String(160), nullable=True)
    normalized_model: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    blocking_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    capacity_gb: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    wattage: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    identity: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RetailerListing(Base):
    __tablename__ = "retailer_listings"
    id: Mapped[str] = mapped_column(String(320), primary_key=True)
    canonical_product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    retailer_sku: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500))
    price: Mapped[int] = mapped_column(Integer)
    shipping: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stock: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    identity_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    blocking_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    __table_args__ = (UniqueConstraint("source", "retailer_sku"),)


class PriceHistory(Base):
    __tablename__ = "price_history"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    retailer_listing_id: Mapped[str] = mapped_column(ForeignKey("retailer_listings.id"), index=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    price: Mapped[int] = mapped_column(Integer)
    effective_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stock: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SourceRefresh(Base):
    __tablename__ = "source_refresh"
    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
    listing_count: Mapped[int] = mapped_column(Integer, default=0)
    last_refresh: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SourceCoverage(Base):
    __tablename__ = "source_coverage"
    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    category: Mapped[str] = mapped_column(String(32), primary_key=True)
    collected: Mapped[int] = mapped_column(Integer)
    reported_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_fetched: Mapped[int] = mapped_column(Integer)
    page_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20))
    last_refresh: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MatchCandidate(Base):
    __tablename__ = "match_candidates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    source_a: Mapped[str] = mapped_column(String(40), index=True)
    source_b: Mapped[str] = mapped_column(String(40), index=True)
    listing_a_id: Mapped[str] = mapped_column(String(320), index=True)
    listing_b_id: Mapped[str] = mapped_column(String(320), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    blocking_key: Mapped[str] = mapped_column(String(255), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    matching_fields: Mapped[list] = mapped_column(JSON)
    unknown_fields: Mapped[list] = mapped_column(JSON)
    conflicts: Mapped[list] = mapped_column(JSON)
    decision_reason: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("listing_a_id", "listing_b_id"),)


class MatchPerformance(Base):
    __tablename__ = "match_performance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_run: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mode: Mapped[str] = mapped_column(String(20))
    metrics: Mapped[dict] = mapped_column(JSON)


class CurrentInventory(Base):
    __tablename__ = "current_inventory"
    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    retailer_skus: Mapped[list] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DealOpportunity(Base):
    __tablename__ = "deal_opportunities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    canonical_product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    retailer_listing_id: Mapped[str] = mapped_column(ForeignKey("retailer_listings.id"), index=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    active: Mapped[bool] = mapped_column(Boolean, index=True)
    current_price: Mapped[int] = mapped_column(Integer)
    previous_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    median_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    low_30d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    all_time_low: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_vs_previous_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    discount_vs_7d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    discount_vs_30d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    deal_score: Mapped[float] = mapped_column(Float, index=True)
    grade: Mapped[str] = mapped_column(String(20), index=True)
    opportunity_types: Mapped[list] = mapped_column(JSON)
    reasons: Mapped[list] = mapped_column(JSON)
    history_metrics: Mapped[dict] = mapped_column(JSON)
    cross_store: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    build_impact: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    product_identity: Mapped[dict] = mapped_column(JSON)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DealScanState(Base):
    __tablename__ = "deal_scan_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict] = mapped_column(JSON)


def ensure_market_schema() -> None:
    """Idempotently add incremental-matching columns to existing databases."""
    Base.metadata.create_all(engine)
    additions = {
        "products": {
            "normalized_model": "VARCHAR(160)", "blocking_key": "VARCHAR(255)",
            "capacity_gb": "INTEGER", "wattage": "INTEGER",
        },
        "retailer_listings": {
            "identity_fingerprint": "VARCHAR(64)", "blocking_key": "VARCHAR(255)",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
        indexes = (
            ("ix_products_blocking_key", "products", "blocking_key"),
            ("ix_products_normalized_model", "products", "normalized_model"),
            ("ix_products_chipset", "products", "chipset"),
            ("ix_products_capacity_gb", "products", "capacity_gb"),
            ("ix_products_wattage", "products", "wattage"),
            ("ix_retailer_listings_identity_fingerprint", "retailer_listings", "identity_fingerprint"),
            ("ix_retailer_listings_blocking_key", "retailer_listings", "blocking_key"),
        )
        for name, table, column in indexes:
            connection.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))


def save_job(data: dict):
    with Session.begin() as session:
        existing = session.get(Job, data["id"])
        if existing:
            existing.data = data
        else:
            session.add(Job(id=data["id"], data=data))


def read_job(job_id: str) -> dict | None:
    with Session() as session:
        job = session.get(Job, job_id)
        return job.data if job else None
