"""
Domain model.

  Contract          one logical agreement (the thing a business tracks)
   └─ ContractVersion   v1, v2, an amendment ... each is separately extracted
       └─ Document      the uploaded file(s) backing that version
       └─ extracted data: ExtractedField, ContractParty, Clause, Obligation,
                          Deadline, ReviewFlag, ContractChunk

Every extracted item carries the shared shape:
    value · source_quote · page · section · confidence (0-1) · status · contract_version_id
so any insight can be traced back to the exact text of the exact version it came from.
"""

import enum
import uuid
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def generate_uuid():
    return uuid.uuid4()


# ─── Enumerations ─────────────────────────────────────────────────────────────

class ContractStatus(str, enum.Enum):
    """Processing state of a contract VERSION."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"


class ClauseType(str, enum.Enum):
    PAYMENT = "PAYMENT"
    RENEWAL = "RENEWAL"
    TERMINATION = "TERMINATION"
    CONFIDENTIALITY = "CONFIDENTIALITY"
    INDEMNITY = "INDEMNITY"
    FORCE_MAJEURE = "FORCE_MAJEURE"
    DATA_PROTECTION = "DATA_PROTECTION"
    LIABILITY = "LIABILITY"
    INTELLECTUAL_PROPERTY = "INTELLECTUAL_PROPERTY"
    OTHER = "OTHER"


class ObligationStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    OVERDUE = "OVERDUE"


class DeadlineType(str, enum.Enum):
    EXPIRY = "EXPIRY"
    RENEWAL_NOTICE = "RENEWAL_NOTICE"
    PAYMENT = "PAYMENT"
    OBLIGATION = "OBLIGATION"
    OTHER = "OTHER"


class FieldStatus(str, enum.Enum):
    """Shared verification status carried by every extracted item."""
    VERIFIED = "verified"                              # source_quote found in the document text
    NEEDS_REVIEW = "needs_review"                      # extracted, but unverifiable / failed a validator
    NOT_FOUND = "not_found"                            # the document does not state it
    EXTRACTION_UNAVAILABLE = "extraction_unavailable"  # extraction could not run


class ExtractionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"          # some chunks failed; results cover part of the document
    UNAVAILABLE = "unavailable"  # no usable LLM / quota exhausted / every chunk failed
    UNSUPPORTED = "unsupported"  # no text layer (scanned) or unreadable PDF
    LEGACY = "legacy"            # from the pre-verification pipeline; never verified


class DocumentKind(str, enum.Enum):
    MAIN = "MAIN"
    AMENDMENT = "AMENDMENT"
    SOW = "SOW"
    EXHIBIT = "EXHIBIT"
    OTHER = "OTHER"


class FlagSeverity(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FlagStatus(str, enum.Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class AlertStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DISMISSED = "DISMISSED"


class ChangeType(str, enum.Enum):
    UNCHANGED = "UNCHANGED"
    MODIFIED = "MODIFIED"
    ADDED = "ADDED"
    REMOVED = "REMOVED"


class MessageRole(str, enum.Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


# ─── Identity ─────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consented_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    onboarded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    contracts: Mapped[List["Contract"]] = relationship(
        "Contract", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def onboarded(self) -> bool:
        """The demo/legacy user, and anyone who never went through sign-up, counts as onboarded."""
        return self.onboarded_at is not None or self.consented_at is None


class EmailVerification(Base):
    """A one-time 6-digit code sent to an address to prove it belongs to the person signing up."""
    __tablename__ = "email_verifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertSetting(Base):
    """Per-user alert lead times and optional email delivery address."""
    __tablename__ = "alert_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    lead_days: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)   # e.g. [30, 14, 7]; null -> server default
    email_to: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Repeat notifications until the alert is marked read.
    repeat_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    repeat_hours: Mapped[int] = mapped_column(Integer, default=24, server_default="24")
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Unguessable token behind the one-click "mark as read" link in emails. Rotatable.
    ack_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    # Morning briefing: with it on, email goes out once a day at briefing_hour in the user's time zone.
    briefing_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    briefing_hour: Mapped[int] = mapped_column(Integer, default=8, server_default="8")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    paused_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_briefing_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)


class PushSubscription(Base):
    """A browser registered for Web Push notifications."""
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PolicyRule(Base):
    """A person's own standard, e.g. 'flag payment terms longer than 45 days'. Evaluated in code, never by a model."""
    __tablename__ = "policy_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    field: Mapped[str] = mapped_column(String(40), nullable=False)          # see policy_service.CATALOG
    operator: Mapped[str] = mapped_column(String(12), nullable=False)
    value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # for "missing": which field
    severity: Mapped[str] = mapped_column(String(10), default="MEDIUM", server_default="MEDIUM", nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


# ─── Contract / version / document ────────────────────────────────────────────

class Contract(Base):
    """One logical agreement. Holds no extracted data itself: that lives on versions."""
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    counterparty: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contract_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    current_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contract_versions.id", use_alter=True, name="fk_contracts_current_version", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    user: Mapped["User"] = relationship("User", back_populates="contracts")
    versions: Mapped[List["ContractVersion"]] = relationship(
        "ContractVersion", back_populates="contract", cascade="all, delete-orphan",
        foreign_keys="ContractVersion.contract_id", order_by="ContractVersion.version_number",
    )
    current_version: Mapped[Optional["ContractVersion"]] = relationship(
        "ContractVersion", foreign_keys=[current_version_id], post_update=True, viewonly=True,
    )
    documents: Mapped[List["Document"]] = relationship("Document", back_populates="contract", cascade="all, delete-orphan")
    chat_messages: Mapped[List["ChatMessage"]] = relationship("ChatMessage", back_populates="contract", cascade="all, delete-orphan")
    changes: Mapped[List["VersionChange"]] = relationship("VersionChange", back_populates="contract", cascade="all, delete-orphan")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class ContractVersion(Base):
    """One extracted revision of a contract (v1, v2, an amendment)."""
    __tablename__ = "contract_versions"
    __table_args__ = (UniqueConstraint("contract_id", "version_number", name="uq_version_number_per_contract"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)          # "v1", "Amendment 1", ...

    # Processing / job state
    status: Mapped[ContractStatus] = mapped_column(Enum(ContractStatus), default=ContractStatus.PENDING, nullable=False)
    stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    extraction_status: Mapped[str] = mapped_column(String(24), default=ExtractionStatus.PENDING.value, server_default="pending")
    extraction_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extraction_meta: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Convenience copies of key extracted values (authoritative copy is in extracted_fields)
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    renewal_terms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payment_terms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    termination_conditions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    contract: Mapped["Contract"] = relationship("Contract", back_populates="versions", foreign_keys=[contract_id])
    documents: Mapped[List["Document"]] = relationship("Document", back_populates="version", cascade="all, delete-orphan")
    fields: Mapped[List["ExtractedField"]] = relationship("ExtractedField", back_populates="version", cascade="all, delete-orphan")
    parties: Mapped[List["ContractParty"]] = relationship("ContractParty", back_populates="version", cascade="all, delete-orphan")
    clauses: Mapped[List["Clause"]] = relationship("Clause", back_populates="version", cascade="all, delete-orphan")
    obligations: Mapped[List["Obligation"]] = relationship("Obligation", back_populates="version", cascade="all, delete-orphan")
    deadlines: Mapped[List["Deadline"]] = relationship("Deadline", back_populates="version", cascade="all, delete-orphan")
    flags: Mapped[List["ReviewFlag"]] = relationship("ReviewFlag", back_populates="version", cascade="all, delete-orphan")
    chunks: Mapped[List["ContractChunk"]] = relationship("ContractChunk", back_populates="version", cascade="all, delete-orphan")


class Document(Base):
    """An uploaded file. The main document of a version, or a related document."""
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[DocumentKind] = mapped_column(Enum(DocumentKind), default=DocumentKind.MAIN, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)     # sanitized display name only
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)  # server-generated, relative to STORAGE_PATH
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    contract: Mapped["Contract"] = relationship("Contract", back_populates="documents")
    version: Mapped[Optional["ContractVersion"]] = relationship("ContractVersion", back_populates="documents")


# ─── Extracted data (all version-scoped, all in the shared shape) ─────────────

class ExtractedField(Base):
    __tablename__ = "extracted_fields"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    field_key: Mapped[str] = mapped_column(String(40), nullable=False)
    item_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    display_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    notes: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    version: Mapped["ContractVersion"] = relationship("ContractVersion", back_populates="fields")


class ContractParty(Base):
    __tablename__ = "contract_parties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default=FieldStatus.NEEDS_REVIEW.value, server_default="needs_review")

    version: Mapped["ContractVersion"] = relationship("ContractVersion", back_populates="parties")


class Clause(Base):
    __tablename__ = "clauses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    clause_type: Mapped[ClauseType] = mapped_column(Enum(ClauseType), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default=FieldStatus.NEEDS_REVIEW.value, server_default="needs_review")

    version: Mapped["ContractVersion"] = relationship("ContractVersion", back_populates="clauses")


class Obligation(Base):
    __tablename__ = "obligations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    responsible_party: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    due_rule: Mapped[str] = mapped_column(Text, nullable=False)
    # Phase 5 fills these: fixed | relative | recurring | none, plus the parsed rule.
    due_rule_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    due_rule_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    recurrence: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[ObligationStatus] = mapped_column(Enum(ObligationStatus), default=ObligationStatus.PENDING, nullable=False)
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(24), default=FieldStatus.NEEDS_REVIEW.value, server_default="needs_review")

    @property
    def is_overdue(self) -> bool:
        """Due date passed and not completed. Derived at read time so it never goes stale."""
        return self.status != ObligationStatus.COMPLETED and self.due_date is not None and self.due_date < date.today()

    @property
    def due_computed(self) -> bool:
        return bool((self.due_rule_json or {}).get("computed"))

    @property
    def due_basis(self) -> Optional[str]:
        return (self.due_rule_json or {}).get("basis")

    @property
    def due_reason(self) -> Optional[str]:
        """Why no date could be worked out (an honest gap, never an invented date)."""
        return (self.due_rule_json or {}).get("reason")

    version: Mapped["ContractVersion"] = relationship("ContractVersion", back_populates="obligations")
    deadlines: Mapped[List["Deadline"]] = relationship("Deadline", back_populates="obligation")
    occurrences: Mapped[List["ObligationOccurrence"]] = relationship(
        "ObligationOccurrence", back_populates="obligation", cascade="all, delete-orphan"
    )


class ObligationOccurrence(Base):
    """One computed occurrence of a recurring obligation (Phase 5)."""
    __tablename__ = "obligation_occurrences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    obligation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="CASCADE"), nullable=False, index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ObligationStatus] = mapped_column(Enum(ObligationStatus), default=ObligationStatus.PENDING, nullable=False)
    basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    obligation: Mapped["Obligation"] = relationship("Obligation", back_populates="occurrences")


class Deadline(Base):
    __tablename__ = "deadlines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    obligation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="CASCADE"), nullable=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    deadline_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    deadline_type: Mapped[DeadlineType] = mapped_column(Enum(DeadlineType), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # how the date was derived

    version: Mapped["ContractVersion"] = relationship("ContractVersion", back_populates="deadlines")
    obligation: Mapped[Optional["Obligation"]] = relationship("Obligation", back_populates="deadlines")
    alerts: Mapped[List["Alert"]] = relationship("Alert", back_populates="deadline", cascade="all, delete-orphan")


class ReviewFlag(Base):
    """A reason a human should look at something (Phase 4 populates these)."""
    __tablename__ = "review_flags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)          # LOW_CONFIDENCE, QUOTE_NOT_FOUND, ...
    severity: Mapped[FlagSeverity] = mapped_column(Enum(FlagSeverity), default=FlagSeverity.MEDIUM, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)              # plain English
    target_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)   # field | obligation | clause | party
    target_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    target_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[FlagStatus] = mapped_column(Enum(FlagStatus), default=FlagStatus.OPEN, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    version: Mapped["ContractVersion"] = relationship("ContractVersion", back_populates="flags")


class Alert(Base):
    """A reminder to fire at (deadline - lead_days). Replaces the old `reminders` table."""
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    deadline_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deadlines.id", ondelete="CASCADE"), nullable=False, index=True)
    lead_days: Mapped[int] = mapped_column(Integer, nullable=False)
    fire_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), default=AlertStatus.PENDING, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    emailed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)   # last email
    email_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    pushed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)    # last push
    push_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    deadline: Mapped["Deadline"] = relationship("Deadline", back_populates="alerts")

    @property
    def acknowledged(self) -> bool:
        return self.status in (AlertStatus.ACKNOWLEDGED, AlertStatus.DISMISSED)


class VersionChange(Base):
    """A difference between two versions of the same contract (Phase 6 populates these)."""
    __tablename__ = "version_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    from_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    to_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    change_type: Mapped[ChangeType] = mapped_column(Enum(ChangeType), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)      # field key, or "section"
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    old_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    old_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    old_section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    new_section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_material: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    impact_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    contract: Mapped["Contract"] = relationship("Contract", back_populates="changes")


class Summary(Base):
    """Generated stakeholder summary for one version (Phase 7 populates these)."""
    __tablename__ = "summaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    lines: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)    # [{text, page, section, quote, status}]
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ContractChunk(Base):
    """Retrieval chunk. Full-text index lives in a generated `tsv` column."""
    __tablename__ = "contract_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_page: Mapped[int] = mapped_column(Integer, default=1)
    source_section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    embedding: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)  # Phase 7 (pgvector)

    version: Mapped["ContractVersion"] = relationship("ContractVersion", back_populates="chunks")


class ChatMessage(Base):
    """Conversation lives on the contract; each answer records the version it used."""
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    answer_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    contract: Mapped["Contract"] = relationship("Contract", back_populates="chat_messages")
