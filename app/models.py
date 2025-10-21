from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Integer, Text, Date, JSON

class Base(DeclarativeBase):
    pass

class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    opportunity_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    agency: Mapped[str | None] = mapped_column(Text)
    mechanism: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)  # comma-joined for MVP
    summary: Mapped[str | None] = mapped_column(Text)
    eligibility: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[str | None] = mapped_column(Text)  # comma-joined for MVP
    posted_date: Mapped[str | None] = mapped_column(Date)
    close_date: Mapped[str | None] = mapped_column(Date)
    urls: Mapped[dict | None] = mapped_column(JSON)
    assistance_listing: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSON)
    hash: Mapped[str | None] = mapped_column(Text, unique=True)
