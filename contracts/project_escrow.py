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

        self.allowed_sources = allowed_sources.strip().lower()
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
    def get_allowed_sources(self) -> str:
        return self.allowed_sources

    @gl.public.view
    def get_render_mode(self) -> str:
        return self.render_mode
