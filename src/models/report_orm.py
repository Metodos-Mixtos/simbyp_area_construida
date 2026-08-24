"""SQLAlchemy ORM model for reports_sent table."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, Date, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class ReportSent(Base):
    """ORM mapping for reports_sent."""

    __tablename__ = "reports_sent"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    alert_type = Column(String(50), nullable=False)
    report_title = Column(String(500), nullable=False)
    report_url = Column(Text, nullable=True)
    report_date = Column(Date, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    recipient_count = Column(Integer, default=0)
    status = Column(String(20), default="generated")
    error_message = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=True)

    def __repr__(self):
        return (
            f"<ReportSent(id={self.id}, alert_type={self.alert_type}, "
            f"status={self.status})>"
        )
