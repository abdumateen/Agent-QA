"""Cross-repository isolation and sharing for recorded failures."""

from collections.abc import Iterator

import pytest

from agent_qa.config import Settings
from agent_qa.storage import MemoryRecord, Storage
from agent_qa.utils import generate_error_signature, normalize_remote_url

_ERROR_MESSAGE = "AssertionError: expected 200 but received 500"
_STACK_TRACE = 'File "tests/test_orders.py", line 42, in test_create\nAssertionError'
_SIGNATURE = generate_error_signature(_ERROR_MESSAGE, _STACK_TRACE)


@pytest.fixture
def repo_a(settings: Settings) -> Iterator[Storage]:
    """Open the isolated database under one repository identity."""
    with Storage(settings=settings, repo_id="aaaaaaaabbbb") as instance:
        yield instance


@pytest.fixture
def repo_b(settings: Settings) -> Iterator[Storage]:
    """Open the same database under a second repository identity."""
    with Storage(settings=settings, repo_id="ccccccccdddd") as instance:
        yield instance


def _record(storage: Storage) -> MemoryRecord:
    """Record one failure sharing the signature every test searches for."""
    return storage.remember_failure(
        test_name="test_create",
        error_message=_ERROR_MESSAGE,
        stack_trace=_STACK_TRACE,
        root_cause="Order service rejected the payload.",
        fix="Send the currency field.",
    )


def test_repository_failure_stays_private(repo_a: Storage, repo_b: Storage) -> None:
    """A failure recorded in one repository is invisible from another."""
    _record(repo_a)

    assert repo_a.find_similar_failures(_SIGNATURE) != []
    assert repo_b.find_similar_failures(_SIGNATURE) == []


def test_global_failure_is_visible_everywhere(
    repo_a: Storage, repo_b: Storage
) -> None:
    """Sharing a failure exposes it to other repositories, labelled global."""
    failure = _record(repo_a)
    stored = repo_a.set_failure_resolution(int(failure["id"]), global_scope=True)

    assert stored["resolution"] == "global"
    matches = repo_b.find_similar_failures(_SIGNATURE)
    assert [match["scope"] for match in matches] == ["global"]
    assert matches[0]["fix"] == "Send the currency field."


def test_unsharing_hides_the_failure_again(
    repo_a: Storage, repo_b: Storage
) -> None:
    """Returning a failure to repository scope removes it from other searches."""
    failure = _record(repo_a)
    repo_a.set_failure_resolution(int(failure["id"]), global_scope=True)
    repo_a.set_failure_resolution(int(failure["id"]), global_scope=False)

    assert repo_b.find_similar_failures(_SIGNATURE) == []


def test_repository_matches_outrank_global_matches(
    repo_a: Storage, repo_b: Storage
) -> None:
    """Local evidence is returned before a fix shared by another repository."""
    shared = _record(repo_a)
    repo_a.set_failure_resolution(int(shared["id"]), global_scope=True)
    _record(repo_b)

    matches = repo_b.find_similar_failures(_SIGNATURE)

    assert [match["scope"] for match in matches] == ["repo", "global"]


def test_limit_applies_across_both_passes(
    repo_a: Storage, repo_b: Storage
) -> None:
    """A full local page is never padded with global records."""
    for _ in range(2):
        _record(repo_b)
    shared = _record(repo_a)
    repo_a.set_failure_resolution(int(shared["id"]), global_scope=True)

    matches = repo_b.find_similar_failures(_SIGNATURE, limit=2)

    assert [match["scope"] for match in matches] == ["repo", "repo"]


def test_remote_urls_share_one_identity() -> None:
    """Two clones of one repository normalize to the same identity."""
    https = normalize_remote_url("https://github.com/abdumateen/Agent-QA.git")
    ssh = normalize_remote_url("git@github.com:abdumateen/Agent-QA.git")

    assert https == ssh == "github.com/abdumateen/Agent-QA"
