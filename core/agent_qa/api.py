"""REST interface and loopback-only entry point for the core service."""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Path, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from agent_qa import __version__
from agent_qa.config import CORE_HOST, CORE_PORT, Settings, get_settings
from agent_qa.logging_config import configure_logging, get_logger
from agent_qa.models import (
    ContractCreate,
    FailureCreate,
    FailureResolve,
    FixtureCreate,
    HealthResponse,
    QuarantineRequest,
    TestRunCreate,
)
from agent_qa.storage import MemoryNotFoundError, MemoryRecord, Storage


def _get_storage(request: Request) -> Storage:
    storage = getattr(request.app.state, "storage", None)
    if not isinstance(storage, Storage):
        raise RuntimeError("Storage is not initialized.")
    return storage


StorageDependency = Annotated[Storage, Depends(_get_storage)]
ResultLimit = Annotated[int, Query(ge=1, le=1000)]
RequiredPath = Annotated[str, Path(min_length=1)]
OptionalFilter = Annotated[str | None, Query(min_length=1)]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application whose database is opened only during its lifespan.

    Passing settings explicitly supports isolated databases and log directories
    in tests. Callers must enter the application's lifespan before making
    requests.
    """
    config = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(config)
        logger = get_logger(__name__)
        storage = await run_in_threadpool(Storage, settings=config)
        application.state.storage = storage
        logger.info(
            "core_service_started",
            host=CORE_HOST,
            port=CORE_PORT,
            version=__version__,
        )
        try:
            yield
        finally:
            application.state.storage = None
            await run_in_threadpool(storage.close)
            logger.info("core_service_stopped")

    application = FastAPI(
        title="Agent QA",
        description="Persistent local memory for test data and execution history.",
        version=__version__,
        lifespan=lifespan,
        redirect_slashes=False,
    )
    application.state.settings = config
    application.state.storage = None

    @application.exception_handler(MemoryNotFoundError)
    async def handle_not_found(
        request: Request, exc: MemoryNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _validation_response(exc)

    @application.exception_handler(ValidationError)
    async def handle_model_validation(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        return _validation_response(exc)

    @application.exception_handler(ValueError)
    async def handle_invalid_value(
        request: Request, exc: ValueError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @application.exception_handler(IntegrityError)
    async def handle_integrity_error(
        request: Request, exc: IntegrityError
    ) -> JSONResponse:
        get_logger(__name__).warning("database_integrity_conflict")
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "The operation conflicts with stored data."},
        )

    @application.exception_handler(OperationalError)
    async def handle_operational_error(
        request: Request, exc: OperationalError
    ) -> JSONResponse:
        error_code = getattr(exc.orig, "sqlite_errorcode", None)
        if isinstance(error_code, int) and (error_code & 0xFF) in (
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        ):
            get_logger(__name__).warning("database_busy")
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "The database is busy. Try again shortly."},
                headers={"Retry-After": "1"},
            )
        get_logger(__name__).error("database_operation_failed")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "The database operation could not be completed."},
        )

    @application.exception_handler(SQLAlchemyError)
    async def handle_database_error(
        request: Request, exc: SQLAlchemyError
    ) -> JSONResponse:
        get_logger(__name__).error("database_error")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "The database operation could not be completed."},
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request, exc: Exception
    ) -> JSONResponse:
        get_logger(__name__).error(
            "unhandled_request_error",
            exception_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal server error occurred."},
        )

    @application.get(
        "/health",
        response_model=HealthResponse,
        include_in_schema=False,
    )
    @application.get(
        "/v1/health",
        response_model=HealthResponse,
        operation_id="health",
        tags=["Health"],
    )
    def health(storage: StorageDependency) -> HealthResponse:
        """Verify database connectivity and report the service version."""
        return HealthResponse.model_validate(storage.health())

    @application.get(
        "/v1/fixtures",
        response_model=list[MemoryRecord],
        operation_id="list_fixtures",
        tags=["Fixtures"],
    )
    def list_fixtures(
        storage: StorageDependency,
        tag: OptionalFilter = None,
        limit: ResultLimit = 50,
    ) -> list[MemoryRecord]:
        """List recent fixtures, optionally filtering by an exact tag."""
        return storage.list_fixtures(tag=tag, limit=limit)

    @application.get(
        "/v1/fixtures/{name:path}",
        response_model=MemoryRecord,
        operation_id="get_fixture",
        tags=["Fixtures"],
    )
    def get_fixture(
        name: RequiredPath,
        storage: StorageDependency,
        type: OptionalFilter = None,
    ) -> MemoryRecord:
        """Retrieve a named fixture and optionally require its exact type."""
        return storage.get_fixture(name=name, type=type)

    @application.post(
        "/v1/fixtures",
        response_model=MemoryRecord,
        status_code=status.HTTP_201_CREATED,
        operation_id="remember_fixture",
        tags=["Fixtures"],
    )
    def remember_fixture(
        body: FixtureCreate, storage: StorageDependency
    ) -> MemoryRecord:
        """Remember a fixture, replacing the contents of an existing name."""
        return storage.remember_fixture(
            name=body.name,
            type=body.type,
            content=body.content,
            tags=body.tags,
        )

    @application.get(
        "/v1/contracts/{endpoint:path}/{method}",
        response_model=MemoryRecord,
        operation_id="get_api_contract",
        tags=["Contracts"],
    )
    def get_api_contract(
        endpoint: RequiredPath,
        method: RequiredPath,
        storage: StorageDependency,
        status_code: Annotated[int | None, Query(ge=100, le=599)] = None,
    ) -> MemoryRecord:
        """Retrieve a contract using the endpoint exactly as it was stored.

        The endpoint path parameter may itself contain slashes. Clients should
        percent-encode the endpoint as one parameter, including its leading
        slash. Without a status code, the lowest stored status code is selected.
        """
        return storage.get_api_contract(
            endpoint=endpoint,
            method=method,
            status_code=status_code,
        )

    @application.post(
        "/v1/contracts",
        response_model=MemoryRecord,
        status_code=status.HTTP_201_CREATED,
        operation_id="remember_api_contract",
        tags=["Contracts"],
    )
    def remember_api_contract(
        body: ContractCreate, storage: StorageDependency
    ) -> MemoryRecord:
        """Remember a validated Draft 2020-12 request and response contract."""
        return storage.remember_api_contract(
            endpoint=body.endpoint,
            method=body.method,
            request_schema=body.request_schema,
            response_schema=body.response_schema,
            status_code=body.status_code,
            tags=body.tags,
        )

    @application.post(
        "/v1/failures",
        response_model=MemoryRecord,
        status_code=status.HTTP_201_CREATED,
        operation_id="remember_failure",
        tags=["Failures"],
    )
    def remember_failure(
        body: FailureCreate, storage: StorageDependency
    ) -> MemoryRecord:
        """Record a distinct failure and any available diagnostic information."""
        return storage.remember_failure(
            test_name=body.test_name,
            error_message=body.error_message,
            stack_trace=body.stack_trace,
            root_cause=body.root_cause,
            fix=body.fix,
            tags=body.tags,
        )

    @application.post(
        "/v1/failures/{failure_id}/resolve",
        response_model=MemoryRecord,
        operation_id="resolve_failure",
        tags=["Failures"],
    )
    def resolve_failure(
        failure_id: Annotated[int, Path(gt=0)],
        body: FailureResolve,
        storage: StorageDependency,
    ) -> MemoryRecord:
        """Resolve a recorded failure with a nonempty diagnosis and fix."""
        return storage.resolve_failure(
            failure_id=failure_id,
            root_cause=body.root_cause,
            fix=body.fix,
        )

    @application.get(
        "/v1/failures/{test_name:path}",
        response_model=list[MemoryRecord],
        operation_id="get_failure",
        tags=["Failures"],
    )
    def get_failure(
        test_name: RequiredPath,
        storage: StorageDependency,
        error_pattern: Annotated[str | None, Query()] = None,
    ) -> list[MemoryRecord]:
        """List failures, optionally matching a literal error-message substring."""
        return storage.get_failure(test_name=test_name, error_pattern=error_pattern)

    @application.get(
        "/v1/suggest-test",
        response_model=list[MemoryRecord],
        operation_id="suggest_test",
        tags=["Search"],
    )
    def suggest_test(
        description: Annotated[str, Query(min_length=1, max_length=4096)],
        storage: StorageDependency,
        limit: ResultLimit = 10,
    ) -> list[MemoryRecord]:
        """Find relevant stored memories for a proposed test description."""
        return storage.suggest_test(description=description, limit=limit)

    @application.post(
        "/v1/test-runs",
        response_model=MemoryRecord,
        status_code=status.HTTP_201_CREATED,
        operation_id="record_test_run",
        tags=["Test runs"],
    )
    def record_test_run(
        body: TestRunCreate, storage: StorageDependency
    ) -> MemoryRecord:
        """Record a test execution and atomically refresh its failure ratio."""
        return storage.record_test_run(
            test_name=body.test_name,
            framework=body.framework,
            status=body.status,
            duration=body.duration,
            error_message=body.error_message,
        )

    @application.get(
        "/v1/flaky",
        response_model=list[MemoryRecord],
        operation_id="get_flaky_tests",
        tags=["Flaky tests"],
    )
    def get_flaky_tests(
        storage: StorageDependency,
        threshold: Annotated[
            float, Query(ge=0.0, le=1.0, allow_inf_nan=False)
        ] = config.flaky_threshold,
    ) -> list[MemoryRecord]:
        """List histories meeting the supplied or configured failure threshold."""
        return storage.get_flaky_tests(threshold=threshold)

    @application.post(
        "/v1/quarantine",
        response_model=MemoryRecord,
        operation_id="quarantine_test",
        tags=["Flaky tests"],
    )
    def quarantine_test(
        body: QuarantineRequest, storage: StorageDependency
    ) -> MemoryRecord:
        """Persist quarantine metadata without automatically skipping tests."""
        return storage.quarantine_test(
            test_name=body.test_name,
            framework=body.framework,
            reason=body.reason,
        )

    @application.get(
        "/v1/search",
        response_model=list[MemoryRecord],
        operation_id="search_memory",
        tags=["Search"],
    )
    def search_memory(
        query: Annotated[str, Query(min_length=1, max_length=4096)],
        storage: StorageDependency,
        limit: ResultLimit = 20,
    ) -> list[MemoryRecord]:
        """Search indexed memories using ranked, plain-text matching."""
        return storage.search_memory(query=query, limit=limit)

    return application


def _validation_response(
    exc: RequestValidationError | ValidationError,
) -> JSONResponse:
    details = [
        {
            "loc": list(error["loc"]),
            "type": error["type"],
            "msg": error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": details},
    )


app = create_app()


def main() -> None:
    """Run the core service exclusively on 127.0.0.1:8765."""
    configure_logging(get_settings())
    uvicorn.run(
        app,
        host=CORE_HOST,
        port=CORE_PORT,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        workers=1,
    )


if __name__ == "__main__":
    main()