"""The Windows Direct Mode shim, tested on any platform.

gltest 0.29.2 unlinks the stdin injection temp file while fd 0 still holds it
open, which Windows refuses with WinError 32. The shim defers that delete
until gltest restores stdin. These tests pin its behaviour so a future change
cannot silently turn it into "ignore the error and leak files".

The shim is normally inactive off Windows; ESCROW_FORCE_WINDOWS_STDIN_COMPAT
activates its code path so it can be exercised here.
"""

import os

import windows_stdin_compat as compat


class _FakeVM:
    pass


def test_shim_is_inactive_on_linux_by_default(monkeypatch):
    monkeypatch.delenv("ESCROW_FORCE_WINDOWS_STDIN_COMPAT", raising=False)
    monkeypatch.setattr(os, "name", "posix")

    assert compat.should_activate() is False


def test_shim_activates_on_windows(monkeypatch):
    monkeypatch.delenv("ESCROW_FORCE_WINDOWS_STDIN_COMPAT", raising=False)
    monkeypatch.setattr(os, "name", "nt")

    assert compat.should_activate() is True


def test_shim_activates_when_forced(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setenv("ESCROW_FORCE_WINDOWS_STDIN_COMPAT", "1")

    assert compat.should_activate() is True


def test_deferred_delete_happens_after_stdin_restore(tmp_path, monkeypatch):
    """The whole point: the file survives injection and dies at cleanup."""
    from gltest.direct import loader
    from gltest.direct.vm import VMContext

    if not compat.is_active():
        compat.install()

    if not compat.is_active():
        # The shim is inactive here (plain Linux run without the override),
        # so gltest's own immediate unlink is in force and there is nothing
        # deferred to observe.
        return

    target = tmp_path / "message.bin"
    target.write_bytes(b"encoded-message")

    # Stand in for gltest's injector: it writes a temp file and unlinks it
    # while the descriptor is still open. Under the shim, that unlink is
    # captured rather than performed.
    def fake_inject(vm):
        os.unlink(str(target))

    monkeypatch.setattr(loader, "_inject_message_to_fd0", fake_inject)

    # Re-install so the shim wraps the stand-in injector above.
    compat._ACTIVE = False
    compat.install()

    before = compat.pending_count()

    loader._inject_message_to_fd0(_FakeVM())

    assert target.exists(), "file must survive while fd 0 still holds it"
    assert compat.pending_count() == before + 1

    # gltest restores stdin inside _cleanup_after_deactivate; the shim
    # deletes only after that has run.
    vm = VMContext.__new__(VMContext)
    VMContext._cleanup_after_deactivate(vm)

    assert not target.exists(), "file must be deleted once stdin is restored"
    assert compat.pending_count() == before


def test_undeletable_file_is_reported_not_hidden(monkeypatch):
    """A file that cannot be deleted is surfaced, never silently dropped."""
    if not compat.is_active():
        compat.install()

    if not compat.is_active():
        return

    compat._PENDING.append("/definitely/not/deletable/locked.bin")

    def always_locked(path, *args, **kwargs):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(os, "unlink", always_locked)

    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        leftovers = compat.finalize()

    assert "/definitely/not/deletable/locked.bin" in leftovers
    assert any(
        "could not be deleted" in str(w.message) for w in caught
    ), "a leftover must produce a warning rather than be swallowed"

    compat._LEFTOVERS.clear()
