"""Windows compatibility for gltest Direct Mode stdin injection.

gltest 0.29.2 injects the message context by writing it to a temp file,
duplicating that file onto fd 0, and then immediately unlinking it
(`gltest/direct/loader.py::_inject_message_to_fd0`). POSIX allows unlinking a
file that is still open; Windows does not, so every deploy raises

    PermissionError: [WinError 32] The process cannot access the file
    because it is being used by another process

and no Direct Mode test reaches its contract assertions.

What this module does, only on Windows:

1. It defers the delete instead of performing it while fd 0 still holds the
   file open. gltest's own function is called unchanged; only `os.unlink` is
   intercepted for the duration of that call, so no upstream logic is copied
   and a future gltest change is inherited rather than shadowed.
2. It deletes the file after gltest restores stdin in
   `VMContext._cleanup_after_deactivate`, which is the first moment the file
   is genuinely closed.
3. Anything still undeletable is retried once at the end of the session and,
   if it survives that, reported as a warning naming each path.

The error is therefore resolved by deleting at a safe time, not by swallowing
PermissionError: a leftover file is surfaced, never hidden.

Set ESCROW_FORCE_WINDOWS_STDIN_COMPAT=1 to exercise this path on a
non-Windows machine. That is for testing the shim itself; it is not needed,
and not used, in normal Linux or CI runs.
"""

import os
import warnings

_ACTIVE = False
_PENDING: list = []
_LEFTOVERS: list = []


def should_activate() -> bool:
    if os.environ.get("ESCROW_FORCE_WINDOWS_STDIN_COMPAT") == "1":
        return True

    return os.name == "nt"


def _delete_pending() -> None:
    """Delete deferred temp files; keep any that are still locked."""
    still_locked = []

    for path in _PENDING:
        try:
            os.unlink(path)
        except FileNotFoundError:
            continue
        except OSError:
            still_locked.append(path)

    _PENDING.clear()
    _PENDING.extend(still_locked)


def install() -> bool:
    """Patch gltest in-process. Returns True when the shim is active."""
    global _ACTIVE

    if _ACTIVE or not should_activate():
        return _ACTIVE

    from gltest.direct import loader as _loader
    from gltest.direct.vm import VMContext

    original_inject = _loader._inject_message_to_fd0
    original_cleanup = VMContext._cleanup_after_deactivate

    def inject_with_deferred_unlink(vm):
        """Run gltest's injector, capturing the unlink instead of doing it."""
        captured = []
        real_unlink = os.unlink

        def capture_unlink(path, *args, **kwargs):
            captured.append(os.fspath(path))

        os.unlink = capture_unlink
        try:
            original_inject(vm)
        finally:
            os.unlink = real_unlink

        _PENDING.extend(captured)

    def cleanup_then_delete(self):
        # gltest restores fd 0 here, so the temp file is only truly closed
        # once this has run. Deleting earlier is what fails on Windows.
        try:
            original_cleanup(self)
        finally:
            _delete_pending()

    _loader._inject_message_to_fd0 = inject_with_deferred_unlink
    VMContext._cleanup_after_deactivate = cleanup_then_delete

    _ACTIVE = True

    return True


def finalize() -> list:
    """End-of-session sweep. Returns paths that could not be deleted."""
    if not _ACTIVE:
        return []

    _delete_pending()

    if _PENDING:
        _LEFTOVERS.extend(_PENDING)
        warnings.warn(
            "gltest Direct Mode temp files could not be deleted: "
            + ", ".join(_PENDING),
            RuntimeWarning,
        )
        _PENDING.clear()

    return list(_LEFTOVERS)


def pending_count() -> int:
    return len(_PENDING)


def is_active() -> bool:
    return _ACTIVE
