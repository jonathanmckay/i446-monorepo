"""
Database models for tracking lease applications and notes.
"""
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Text, DateTime, Float, Integer, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from pathlib import Path

Base = declarative_base()


class Application(Base):
    """
    Represents a lease application from AppFolio.
    This data is synced from AppFolio - don't manually edit these fields.
    """
    __tablename__ = 'applications'
    
    # Primary identifier from AppFolio
    application_id = Column(String(100), primary_key=True)
    
    # Applicant info
    applicant_name = Column(String(255))
    applicant_email = Column(String(255))
    applicant_phone = Column(String(50))
    
    # Property info
    property_name = Column(String(255))
    property_address = Column(String(500))
    unit = Column(String(50))
    
    # Application details
    status = Column(String(100))  # In Progress, Pending Approval, Approved, etc.
    application_date = Column(DateTime)
    desired_move_in = Column(DateTime)
    lease_start_date = Column(DateTime)
    lease_end_date = Column(DateTime)
    
    # Financial
    rent_amount = Column(Float)
    deposit_amount = Column(Float)
    
    # Screening
    screening_status = Column(String(100))
    credit_score = Column(Integer)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_synced_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship to notes
    notes = relationship("ApplicationNote", back_populates="application", cascade="all, delete-orphan")
    
    # Custom fields (your additions, not from AppFolio)
    custom_fields = relationship("CustomField", back_populates="application", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Application {self.application_id}: {self.applicant_name} @ {self.property_address}>"


class ApplicationNote(Base):
    """
    Notes you add to track application progress.
    These are YOUR notes and persist across data refreshes.
    """
    __tablename__ = 'application_notes'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(String(100), ForeignKey('applications.application_id'), nullable=False)
    
    # Note content
    note = Column(Text)
    note_type = Column(String(50), default='general')  # general, follow_up, issue, etc.
    
    # Who added it (if you have multiple users)
    added_by = Column(String(100))
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship
    application = relationship("Application", back_populates="notes")
    
    def __repr__(self):
        return f"<Note {self.id} for {self.application_id}>"


class CustomField(Base):
    """
    Custom fields you want to track that aren't in AppFolio.
    Examples: Follow-up date, Priority level, Assigned agent, etc.
    """
    __tablename__ = 'custom_fields'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(String(100), ForeignKey('applications.application_id'), nullable=False)
    
    field_name = Column(String(100), nullable=False)
    field_value = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    application = relationship("Application", back_populates="custom_fields")
    
    def __repr__(self):
        return f"<CustomField {self.field_name}={self.field_value}>"


class SyncLog(Base):
    """
    Tracks sync history for debugging and auditing.
    """
    __tablename__ = 'sync_log'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    sync_type = Column(String(50))  # 'appfolio_import', 'sheets_export'
    sync_source = Column(String(255))  # File path or API endpoint
    records_processed = Column(Integer, default=0)
    records_added = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    success = Column(Boolean, default=True)
    error_message = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    
    def __repr__(self):
        return f"<SyncLog {self.id}: {self.sync_type} @ {self.started_at}>"


def get_database_path() -> Path:
    """Get the path to the SQLite database file."""
    return Path(__file__).parent.parent / "data" / "leasing.db"


def init_database(db_path: Path = None) -> sessionmaker:
    """
    Initialize the database and return a session factory.
    
    Args:
        db_path: Path to the SQLite database. Defaults to ./data/leasing.db
        
    Returns:
        sessionmaker: SQLAlchemy session factory
    """
    if db_path is None:
        db_path = get_database_path()
    
    # Create data directory if it doesn't exist
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create engine and tables
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    
    # Return session factory
    Session = sessionmaker(bind=engine)
    return Session

