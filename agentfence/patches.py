"""Capture bounded file changes and store optional, private review patches."""

import difflib
import io
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from agentfence.contracts import MAX_BYTES


_PATCH_BYTES = 32 * 1024
_PATCH_LINES = 2048
_STORE_BYTES = 20 * 1024 * 1024
_MAX_AGE = 7 * 24 * 60 * 60
_ARTIFACT = re.compile(r"[0-9a-f]{32}\.(diff|tmp)")
_MISSING = (None, None)


def _snapshot(fd):
    data = bytearray()
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        while len(data) <= MAX_BYTES:
            chunk = os.read(fd, min(8192, MAX_BYTES + 1 - len(data)))
            if not chunk:
                return bytes(data), None
            data.extend(chunk)
    except OSError:
        return None, "capture_failed"
    return None, "omitted_size"


class FileChange:
    def __init__(self, path):
        self.path = path
        self.before = self.after = None
        self.mode = b"100644"

    @contextmanager
    def observe_write(self, fd, *, creating):
        self.before = _MISSING if creating else _snapshot(fd)
        try:
            # The snapshot advances the file position. Rewind before writing.
            if not creating:
                os.lseek(fd, 0, os.SEEK_SET)
            yield
        finally:
            self.after = _snapshot(fd)

    @contextmanager
    def observe_delete(self, fd):
        self.before = _snapshot(fd)
        if os.name == "posix":
            try:
                if os.fstat(fd).st_mode & stat.S_IXUSR:
                    self.mode = b"100755"
            except OSError:
                self.before = (None, "capture_failed")
        yield
        self.after = _MISSING

    def summarize(self, store):
        if self.before is None or self.after is None:
            return ""
        before, before_error = self.before
        after, after_error = self.after
        kind = "created" if self.before == _MISSING else "deleted" if self.after == _MISSING else "modified"
        if error := before_error or after_error:
            kind = "unknown" if kind == "modified" else kind
            return f"change={kind} diff={error}"
        if before == after:
            return "change=unchanged"
        patch, error = _render(self.path, before, after, self.mode)
        if error:
            return f"change={kind} diff={error}"
        try:
            patch_id = store.save(patch)
        except OSError:
            return f"change={kind} diff=save_failed"
        return f"change={kind} patch_id={patch_id}"


def _render(path, before, after, mode):
    old_data, new_data = before or b"", after or b""
    try:
        old_data.decode("utf-8")
        new_data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "omitted_format"
    if b"\0" in old_data or b"\0" in new_data:
        return None, "omitted_format"
    old_lines = io.BytesIO(old_data).readlines()
    new_lines = io.BytesIO(new_data).readlines()
    if max(len(old_lines), len(new_lines)) > _PATCH_LINES:
        return None, "omitted_size"
    old, new = f"a/{path}".encode("ascii"), f"b/{path}".encode("ascii")
    patch = bytearray(b"diff --git " + old + b" " + new + b"\n")
    if before is None:
        patch.extend(b"new file mode " + mode + b"\n")
        old = b"/dev/null"
    if after is None:
        patch.extend(b"deleted file mode " + mode + b"\n")
        new = b"/dev/null"
    for line in difflib.diff_bytes(difflib.unified_diff, old_lines, new_lines, old, new, n=1):
        patch.extend(line)
        if not line.endswith(b"\n"):
            patch.extend(b"\n\\ No newline at end of file\n")
        if len(patch) > _PATCH_BYTES:
            return None, "omitted_size"
    return patch, None


def _require_private_directory(path):
    info = path.lstat()
    reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    if not stat.S_ISDIR(info.st_mode) or reparse:
        raise OSError("A regular audit directory is required")
    if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise OSError("Audit directory must be private and owned by the current user")


@contextmanager
def _lock_store(directory):
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(directory / ".lock", flags, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            # Windows byte range locks may extend past the end of the file.
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


class PatchStore:
    def __init__(self, root, *, workspace, repository):
        root = Path(root).resolve(strict=True)
        workspace, repository = Path(workspace).resolve(), Path(repository).resolve()
        directory = root / "patches"
        if (any(root.is_relative_to(base) for base in (workspace, repository))
                or any(base.is_relative_to(directory) for base in (workspace, repository))):
            raise ValueError("Audit diff directory must be outside the workspace and repository")
        _require_private_directory(root)
        self.directory = directory

    def save(self, patch):
        _require_private_directory(self.directory.parent)
        self.directory.mkdir(mode=0o700, exist_ok=True)
        _require_private_directory(self.directory)
        with _lock_store(self.directory):
            self._prune(len(patch))
            patch_id = uuid4().hex
            temp = self.directory / f"{patch_id}.tmp"
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(patch)
                temp.replace(self.directory / f"{patch_id}.diff")
            finally:
                temp.unlink(missing_ok=True)
        return patch_id

    def _prune(self, incoming_bytes):
        cutoff = time.time() - _MAX_AGE
        remaining = []
        total = incoming_bytes
        for path in self.directory.iterdir():
            artifact = _ARTIFACT.fullmatch(path.name)
            if not artifact:
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            if artifact[1] == "tmp" or info.st_mtime < cutoff:
                path.unlink()
            else:
                remaining.append((info.st_mtime, path, info.st_size))
                total += info.st_size
        for _, path, size in sorted(remaining):
            if total <= _STORE_BYTES:
                break
            path.unlink()
            total -= size
