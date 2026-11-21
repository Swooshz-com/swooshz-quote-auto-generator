"""A fake Neon API client for the production-branch guard and the scripts that run it.

No network: it answers from canned values and records each call. Ids and names are fakes.
"""

from __future__ import annotations

from typing import Any, Mapping

from scripts import neon_branch_guard as guard

PROJECT_NAME = "swooshz-sqag"
PROJECT_ID = "fake-project-000001"
REHEARSAL_BRANCH = "br-fake-rehearsal-000001"
COPY_BRANCH = "rehearsal-copy-abc1234"
KEY = "fake-neon-key-planted"


def api_error(status: int | None) -> guard.NeonApiError:
    """A client error with an HTTP status, as NeonClient raises for an HTTP error answer."""
    error = guard.NeonApiError("Neon API call failed")
    error.status = status
    return error


# What a project-scoped key gets when it asks Neon to list projects.
def list_refused() -> guard.NeonApiError:
    return api_error(404)


class FakeNeon:
    """Stands in for NeonClient: the same methods, canned answers, and a record of calls."""

    def __init__(
        self,
        *,
        project_ids: list[str] | None = None,
        list_error: BaseException | None = None,
        project_name: str = PROJECT_NAME,
        branch_id: str = REHEARSAL_BRANCH,
        name: str = COPY_BRANCH,
        default: bool = False,
        error: BaseException | None = None,
    ) -> None:
        self.project_ids = [PROJECT_ID] if project_ids is None else project_ids
        self.list_error = list_error
        self.name_of_project = project_name
        self.branch_id = branch_id
        self.name = name
        self.default = default
        self.error = error
        self.calls: list[str] = []
        self.project_ids_used: list[str] = []

    def _answer(self, call: str) -> None:
        self.calls.append(call)
        if self.error is not None:
            raise self.error

    def project_ids_named(self, name: str) -> list[str]:
        self._answer("projects")
        if self.list_error is not None:
            raise self.list_error
        return list(self.project_ids)

    def project_name(self, project_id: str) -> str:
        self._answer("project")
        self.project_ids_used.append(project_id)
        return self.name_of_project

    def endpoint_branch_id(self, project_id: str, endpoint_id: str) -> str:
        self._answer("endpoint")
        self.project_ids_used.append(project_id)
        return self.branch_id

    def branch(self, project_id: str, branch_id: str) -> tuple[str, bool]:
        self._answer("branch")
        self.project_ids_used.append(project_id)
        return self.name, self.default


def fake_client_from_env(neon: FakeNeon) -> Any:
    """A stand-in for guard.neon_client_from_env: the fake when the key's name is set, else None."""

    def factory(env: Mapping[str, str]) -> FakeNeon | None:
        return neon if guard.NEON_KEY_ENV in env else None

    return factory
