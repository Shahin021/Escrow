# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""
ProjectEscrow V3
================

Phase 1 core: immutable multi-milestone project definitions and explicit
internal accounting.

The accepted V2 contract remains untouched in escrow_contract.py.

Important accounting rule established by Bradbury Phase 0:
native contract balance is NOT the authoritative escrow ledger.
All protocol obligations are tracked explicitly in persistent state.
"""

from genlayer import *
import json


MAX_MILESTONES = 16
MAX_SPEC_CHARS = 4000
MAX_MILESTONES_JSON_CHARS = 32000
MAX_SOURCES_CHARS = 2000
MAX_SOURCES = 8
MAX_URL_CHARS = 400
MAX_NOTES_CHARS = 500

_HTTPS = "https://"
_BLOCKED_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


def _split_sources(raw):
    out = []
    for part in raw.split(","):
        entry = part.strip().lower().rstrip("/")
        if entry.startswith(_HTTPS):
            entry = entry[len(_HTTPS):]
        if entry:
            out.append(entry)
    return tuple(out)


def _check_url(url, allowed_raw):
    u = url.strip()

    if not u.startswith(_HTTPS):
        raise gl.vm.UserError("artifact source must be https")

    if len(u) > MAX_URL_CHARS:
        raise gl.vm.UserError("artifact url is too long")

    if " " in u or "\n" in u or "\t" in u:
        raise gl.vm.UserError("artifact url contains whitespace")

    rest = u[len(_HTTPS):]
    rest = rest.split("#", 1)[0]
    authority = rest.split("/", 1)[0].split("?", 1)[0].lower()

    if not authority:
        raise gl.vm.UserError("artifact url has no host")

    if "@" in authority:
        raise gl.vm.UserError("credentials are not allowed in the url")

    if ":" in authority:
        raise gl.vm.UserError("explicit ports are not allowed")

    if authority in _BLOCKED_HOSTS or authority.endswith(".local"):
        raise gl.vm.UserError("local addresses are not allowed")

    if authority.replace(".", "").isdigit():
        raise gl.vm.UserError("bare ip addresses are not allowed")

    if ".." in rest:
        raise gl.vm.UserError("path traversal is not allowed")

    tail = rest[len(rest.split("/", 1)[0]):]
    canonical = _HTTPS + authority + tail
    lowered = canonical.lower()

    for entry in _split_sources(allowed_raw):
        prefix = _HTTPS + entry
        if lowered == prefix:
            return canonical
        if lowered.startswith(prefix + "/") or lowered.startswith(prefix + "?"):
            return canonical

    raise gl.vm.UserError("artifact source is not in the agreed allowlist")


class ProjectEscrow(gl.Contract):
    client: Address
    worker: Address

    allowed_sources: str
    render_mode: str
    max_revisions: u32
    continue_after_refund: bool

    project_status: str

    milestone_specs: DynArray[str]
    milestone_amounts: DynArray[u256]
    milestone_statuses: DynArray[str]
    milestone_revisions: DynArray[u32]
    milestone_current_attempt: DynArray[u32]
    milestone_attempt_counts: DynArray[u32]
    milestone_stall_free_used: DynArray[u32]

    attempt_milestones: DynArray[u32]
    attempt_urls: DynArray[str]
    attempt_notes: DynArray[str]
    attempt_kinds: DynArray[str]
    attempt_revision_after: DynArray[u32]

    total_required: u256
    total_funded: u256
    total_released: u256
    total_refunded: u256

    active_milestone: u32

    def __init__(
        self,
        worker: str,
        milestones_json: str,
        allowed_sources: str,
        render_mode: str = "text",
        max_revisions: int = 3,
        continue_after_refund: bool = False,
    ):
        if len(milestones_json) == 0:
            raise gl.vm.UserError("milestones are required")
        if len(milestones_json) > MAX_MILESTONES_JSON_CHARS:
            raise gl.vm.UserError("milestones definition is too large")

        if len(allowed_sources.strip()) == 0:
            raise gl.vm.UserError("allowed sources are required")
        if len(allowed_sources) > MAX_SOURCES_CHARS:
            raise gl.vm.UserError("allowed sources definition is too large")

        sources = _split_sources(allowed_sources)

        if len(sources) == 0:
            raise gl.vm.UserError("at least one allowed source is required")

        if len(sources) > MAX_SOURCES:
            raise gl.vm.UserError("too many allowed sources")

        if render_mode not in ("text", "html"):
            raise gl.vm.UserError("render_mode must be text or html")

        if max_revisions < 1:
            raise gl.vm.UserError("max_revisions must be at least 1")

        try:
            raw_milestones = json.loads(milestones_json)
        except Exception:
            raise gl.vm.UserError("milestones_json must be valid JSON")

        if not isinstance(raw_milestones, list):
            raise gl.vm.UserError("milestones_json must contain a JSON array")

        if len(raw_milestones) == 0:
            raise gl.vm.UserError("at least one milestone is required")

        if len(raw_milestones) > MAX_MILESTONES:
            raise gl.vm.UserError("too many milestones")

        self.client = gl.message.sender_address
        self.worker = Address(worker)

        self.allowed_sources = ",".join(sources)
        self.render_mode = render_mode
        self.max_revisions = u32(max_revisions)
        self.continue_after_refund = continue_after_refund

        self.project_status = "AWAITING_DEPOSIT"

        self.total_required = u256(0)
        self.total_funded = u256(0)
        self.total_released = u256(0)
        self.total_refunded = u256(0)

        self.active_milestone = u32(0)

        for raw in raw_milestones:
            if not isinstance(raw, dict):
                raise gl.vm.UserError("each milestone must be a JSON object")

            spec_raw = raw.get("spec")
            amount_raw = raw.get("amount")

            if not isinstance(spec_raw, str):
                raise gl.vm.UserError("milestone spec must be a string")

            spec = spec_raw.strip()

            if len(spec) == 0:
                raise gl.vm.UserError("milestone spec cannot be empty")

            if len(spec) > MAX_SPEC_CHARS:
                raise gl.vm.UserError("milestone spec is too long")

            if not isinstance(amount_raw, str):
                raise gl.vm.UserError("milestone amount must be a decimal string")

            if len(amount_raw) == 0 or not amount_raw.isdigit():
                raise gl.vm.UserError("milestone amount must be a decimal string")

            amount = u256(int(amount_raw))

            if amount == u256(0):
                raise gl.vm.UserError("milestone amount must be positive")

            self.milestone_specs.append(spec)
            self.milestone_amounts.append(amount)
            self.milestone_statuses.append("LOCKED")
            self.milestone_revisions.append(u32(0))
            self.milestone_current_attempt.append(u32(0))
            self.milestone_attempt_counts.append(u32(0))
            self.milestone_stall_free_used.append(u32(0))

            self.total_required = u256(self.total_required + amount)

    @gl.public.write.payable
    def fund(self) -> None:
        if gl.message.sender_address != self.client:
            raise gl.vm.UserError("only the client can fund this project")

        if self.project_status != "AWAITING_DEPOSIT":
            raise gl.vm.UserError(
                f"cannot fund from status {self.project_status}"
            )

        if gl.message.value != self.total_required:
            raise gl.vm.UserError(
                "sent value does not match the total required amount"
            )

        self.total_funded = u256(self.total_required)
        self.project_status = "ACTIVE"
        self.active_milestone = u32(0)
        self.milestone_statuses[0] = "AWAITING_DELIVERY"

    def _require_active_milestone(self, index):
        if self.project_status != "ACTIVE":
            raise gl.vm.UserError(
                f"project is not active: {self.project_status}"
            )

        if index < 0 or index >= len(self.milestone_statuses):
            raise gl.vm.UserError("milestone index out of range")

        if index != int(self.active_milestone):
            raise gl.vm.UserError("milestone is not active")

    def _append_attempt(self, index, url, notes, kind):
        attempt_index = u32(len(self.attempt_urls))

        self.attempt_milestones.append(u32(index))
        self.attempt_urls.append(url)
        self.attempt_notes.append(notes)
        self.attempt_kinds.append(kind)
        self.attempt_revision_after.append(
            self.milestone_revisions[index]
        )

        self.milestone_current_attempt[index] = attempt_index
        self.milestone_attempt_counts[index] = u32(
            self.milestone_attempt_counts[index] + u32(1)
        )

    @gl.public.write
    def submit_deliverable(
        self,
        milestone_index: int,
        artifact_url: str,
        notes: str = "",
    ) -> None:
        if gl.message.sender_address != self.worker:
            raise gl.vm.UserError("only the worker can submit")

        self._require_active_milestone(milestone_index)

        status = self.milestone_statuses[milestone_index]

        if status not in ("AWAITING_DELIVERY", "REVISION_REQUIRED"):
            raise gl.vm.UserError(
                f"cannot submit from milestone status {status}"
            )

        canonical = _check_url(
            artifact_url,
            self.allowed_sources,
        )

        kind = (
            "INITIAL_SUBMISSION"
            if status == "AWAITING_DELIVERY"
            else "REVISION_SUBMISSION"
        )

        self._append_attempt(
            milestone_index,
            canonical,
            notes[:MAX_NOTES_CHARS],
            kind,
        )

        self.milestone_statuses[milestone_index] = "UNDER_REVIEW"

    @gl.public.write
    def replace_evidence(
        self,
        milestone_index: int,
        artifact_url: str,
        notes: str = "",
    ) -> None:
        if gl.message.sender_address != self.worker:
            raise gl.vm.UserError("only the worker can replace evidence")

        self._require_active_milestone(milestone_index)

        status = self.milestone_statuses[milestone_index]

        if status not in (
            "UNDER_REVIEW",
            "EVIDENCE_UNAVAILABLE",
            "REVIEW_STALLED",
        ):
            raise gl.vm.UserError(
                f"cannot replace evidence from milestone status {status}"
            )

        # Validate the replacement BEFORE burning a revision. A malformed
        # reference must never consume the worker's revision budget.
        canonical = _check_url(
            artifact_url,
            self.allowed_sources,
        )

        costs_revision = True
        kind = "REPLACE_UNDER_REVIEW"

        if status == "EVIDENCE_UNAVAILABLE":
            kind = "REPLACE_UNAVAILABLE"

        elif status == "REVIEW_STALLED":
            if self.milestone_stall_free_used[milestone_index] == u32(0):
                costs_revision = False
                kind = "REPLACE_STALLED_FREE"
                self.milestone_stall_free_used[milestone_index] = u32(1)
            else:
                kind = "REPLACE_STALLED"

        final_rejection = False

        if costs_revision:
            next_revision = u32(
                self.milestone_revisions[milestone_index] + u32(1)
            )

            self.milestone_revisions[milestone_index] = next_revision

            if next_revision >= self.max_revisions:
                final_rejection = True

        self._append_attempt(
            milestone_index,
            canonical,
            notes[:MAX_NOTES_CHARS],
            kind,
        )

        if final_rejection:
            self.milestone_statuses[milestone_index] = "REJECTED_FINAL"
        else:
            self.milestone_statuses[milestone_index] = "UNDER_REVIEW"

    @gl.public.view
    def get_project_status(self) -> str:
        return self.project_status

    @gl.public.view
    def get_milestone_count(self) -> u32:
        return u32(len(self.milestone_specs))

    @gl.public.view
    def get_active_milestone(self) -> u32:
        return self.active_milestone

    @gl.public.view
    def get_total_required(self) -> str:
        return str(int(self.total_required))

    @gl.public.view
    def get_total_funded(self) -> str:
        return str(int(self.total_funded))

    @gl.public.view
    def get_total_released(self) -> str:
        return str(int(self.total_released))

    @gl.public.view
    def get_total_refunded(self) -> str:
        return str(int(self.total_refunded))

    @gl.public.view
    def get_milestone_spec(self, index: int) -> str:
        if index < 0 or index >= len(self.milestone_specs):
            raise gl.vm.UserError("milestone index out of range")
        return self.milestone_specs[index]

    @gl.public.view
    def get_milestone_amount(self, index: int) -> str:
        if index < 0 or index >= len(self.milestone_amounts):
            raise gl.vm.UserError("milestone index out of range")
        return str(int(self.milestone_amounts[index]))

    @gl.public.view
    def get_milestone_status(self, index: int) -> str:
        if index < 0 or index >= len(self.milestone_statuses):
            raise gl.vm.UserError("milestone index out of range")
        return self.milestone_statuses[index]

    @gl.public.view
    def get_milestone_revision_count(self, index: int) -> u32:
        if index < 0 or index >= len(self.milestone_revisions):
            raise gl.vm.UserError("milestone index out of range")
        return self.milestone_revisions[index]

    @gl.public.view
    def get_attempt_count(self, milestone_index: int) -> u32:
        if milestone_index < 0 or milestone_index >= len(
            self.milestone_attempt_counts
        ):
            raise gl.vm.UserError("milestone index out of range")

        return self.milestone_attempt_counts[milestone_index]

    @gl.public.view
    def get_current_attempt_id(self, milestone_index: int) -> u32:
        if milestone_index < 0 or milestone_index >= len(
            self.milestone_attempt_counts
        ):
            raise gl.vm.UserError("milestone index out of range")

        if self.milestone_attempt_counts[milestone_index] == u32(0):
            raise gl.vm.UserError("milestone has no submission attempt")

        return self.milestone_current_attempt[milestone_index]

    @gl.public.view
    def get_attempt_url(self, attempt_index: int) -> str:
        if attempt_index < 0 or attempt_index >= len(self.attempt_urls):
            raise gl.vm.UserError("attempt index out of range")
        return self.attempt_urls[attempt_index]

    @gl.public.view
    def get_attempt_notes(self, attempt_index: int) -> str:
        if attempt_index < 0 or attempt_index >= len(self.attempt_notes):
            raise gl.vm.UserError("attempt index out of range")
        return self.attempt_notes[attempt_index]

    @gl.public.view
    def get_attempt_kind(self, attempt_index: int) -> str:
        if attempt_index < 0 or attempt_index >= len(self.attempt_kinds):
            raise gl.vm.UserError("attempt index out of range")
        return self.attempt_kinds[attempt_index]

    @gl.public.view
    def get_attempt_milestone(self, attempt_index: int) -> u32:
        if attempt_index < 0 or attempt_index >= len(
            self.attempt_milestones
        ):
            raise gl.vm.UserError("attempt index out of range")
        return self.attempt_milestones[attempt_index]

    @gl.public.view
    def get_attempt_revision_after(self, attempt_index: int) -> u32:
        if attempt_index < 0 or attempt_index >= len(
            self.attempt_revision_after
        ):
            raise gl.vm.UserError("attempt index out of range")
        return self.attempt_revision_after[attempt_index]

    @gl.public.view
    def get_allowed_sources(self) -> str:
        return self.allowed_sources

    @gl.public.view
    def get_render_mode(self) -> str:
        return self.render_mode
