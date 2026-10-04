"""Persistent records and validated inputs for the test memory service."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    JsonValue,
    StringConstraints,
)
from pydantic import Field as PydanticField
from sqlalchemy import CheckConstraint, Column, DateTime, Text, UniqueConstraint, text
from sqlmodel import Field, SQLModel

TestStatus = Literal["passed", "failed", "error", "skipped"]
NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
PositiveId = Annotated[int, PydanticField(strict=True, gt=0)]
StatusCode = Annotated[int, PydanticField(strict=True, ge=100, le=599)]
Duration = Annotated[float, PydanticField(ge=0.0, allow_inf_nan=False)]


def utc_now() -> datetime:
    """Return a naive UTC timestamp matching SQLite CURRENT_TIMESTAMP."""
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_method(value: object) -> object:
    if isinstance(value, str):
        return value.strip().upper()
    return value


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        cleaned = tag.strip()
        if not cleaned:
            raise ValueError("Tags must not be empty.")
        if "," in cleaned:
            raise ValueError("Tags must not contain commas.")
        if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
            raise ValueError("Tags must not contain control characters.")
        if cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


HttpMethod = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[!#$%&'*+\-.^_`|~0-9A-Z]+$",
    ),
    BeforeValidator(_normalize_method),
]
Tags = Annotated[list[str], AfterValidator(_normalize_tags)]


class Fixture(SQLModel, table=True):
    """A named fixture whose content is stored as canonical JSON."""

    __tablename__ = "fixtures"
    __table_args__ = (
        UniqueConstraint("name", name="uq_fixtures_name"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(sa_column=Column(Text, nullable=False))
    type: str = Field(sa_column=Column(Text, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
    hash: str = Field(sa_column=Column(Text, nullable=False))
    tags: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
        ),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=utc_now,
        ),
    )


class APIContract(SQLModel, table=True):
    """An HTTP request and response contract for one status code."""

    __tablename__ = "api_contracts"
    __table_args__ = (
        UniqueConstraint(
            "endpoint", "method", "status_code", name="uq_api_contracts_identity"
        ),
        CheckConstraint(
            "status_code BETWEEN 100 AND 599", name="ck_api_contracts_status_code"
        ),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    endpoint: str = Field(sa_column=Column(Text, nullable=False))
    method: str = Field(sa_column=Column(Text, nullable=False))
    request_schema: str = Field(sa_column=Column(Text, nullable=False))
    response_schema: str = Field(sa_column=Column(Text, nullable=False))
    status_code: int
    tags: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
        ),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=utc_now,
        ),
    )


class Failure(SQLModel, table=True):
    """A recorded failure with optional diagnosis and resolution."""

    __tablename__ = "failures"
    __table_args__ = (
        CheckConstraint("resolved IN (0, 1)", name="ck_failures_resolved"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    test_name: str = Field(sa_column=Column(Text, nullable=False, index=True))
    error_message: str = Field(sa_column=Column(Text, nullable=False))
    stack_trace: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    root_cause: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    fix: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    resolved: int = Field(default=0, ge=0, le=1)
    tags: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
        ),
    )
    resolved_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )


class TestRun(SQLModel, table=True):
    """One completed test execution, with duration measured in seconds."""

    __tablename__ = "test_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('passed', 'failed', 'error', 'skipped')",
            name="ck_test_runs_status",
        ),
        CheckConstraint("duration >= 0", name="ck_test_runs_duration"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    test_name: str = Field(sa_column=Column(Text, nullable=False, index=True))
    framework: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(sa_column=Column(Text, nullable=False))
    duration: float = Field(ge=0.0)
    error_message: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    timestamp: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
        ),
    )


class FlakyTest(SQLModel, table=True):
    """Aggregate test history and persistent quarantine metadata.

    Quarantine fields extend the aggregate record so quarantine decisions
    survive service restarts without requiring an additional table.
    """

    __tablename__ = "flaky_tests"
    __table_args__ = (
        CheckConstraint("run_count >= 0", name="ck_flaky_tests_run_count"),
        CheckConstraint(
            "fail_count >= 0 AND fail_count <= run_count",
            name="ck_flaky_tests_fail_count",
        ),
        CheckConstraint(
            "flaky_score >= 0.0 AND flaky_score <= 1.0",
            name="ck_flaky_tests_score",
        ),
        CheckConstraint(
            "quarantined IN (0, 1)", name="ck_flaky_tests_quarantined"
        ),
    )

    test_name: str = Field(
        sa_column=Column(Text, primary_key=True, nullable=False)
    )
    framework: str = Field(
        sa_column=Column(Text, primary_key=True, nullable=False)
    )
    run_count: int = Field(default=0, ge=0)
    fail_count: int = Field(default=0, ge=0)
    flaky_score: float = Field(default=0.0, ge=0.0, le=1.0)
    last_updated: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=utc_now,
        ),
    )
    quarantined: int = Field(default=0, ge=0, le=1)
    quarantine_reason: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    quarantined_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )


class RequestModel(BaseModel):
    """Base input schema that rejects unrecognized fields."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FixtureCreate(RequestModel):
    """Input for remembering or replacing a named fixture."""

    name: NonEmptyString
    type: NonEmptyString
    content: dict[str, JsonValue]
    tags: Tags | None = None


class ContractCreate(RequestModel):
    """Input for remembering a contract with JSON Schema documents.

    Draft 2020-12 schema validation is performed by the contracts module
    before the record is persisted.
    """

    endpoint: NonEmptyString
    method: HttpMethod
    request_schema: dict[str, JsonValue]
    response_schema: dict[str, JsonValue]
    status_code: StatusCode
    tags: Tags | None = None


class FailureCreate(RequestModel):
    """Input for recording a failure, including any available diagnosis."""

    test_name: NonEmptyString
    error_message: NonEmptyString
    stack_trace: NonEmptyString
    root_cause: NonEmptyString
    fix: NonEmptyString
    tags: Tags | None = None


class FailureResolve(RequestModel):
    """Required diagnosis and fix for resolving a recorded failure."""

    root_cause: NonEmptyString
    fix: NonEmptyString


class TestRunCreate(RequestModel):
    """Input for recording an execution and refreshing flaky statistics."""

    test_name: NonEmptyString
    framework: NonEmptyString
    status: TestStatus
    duration: Duration
    error_message: str | None = None


class QuarantineRequest(RequestModel):
    """Input for persistently quarantining a test within its framework."""

    test_name: NonEmptyString
    framework: NonEmptyString
    reason: NonEmptyString


class HealthResponse(BaseModel):
    """Stable response for both health endpoint paths."""

    status: Literal["ok"] = "ok"
    version: Literal["0.1.0"] = "0.1.0"