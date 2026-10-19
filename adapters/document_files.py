"""Uploaded files in a local folder, one subfolder per workspace (decision D-010).

    <root>/<workspace>/<document id>/v<version>.<pdf|docx|xlsx>

File names typed by users are never used as paths; keys are built by the
ingestion service from generated IDs and checked again here. Swap this
class for a cloud storage adapter without changing anything else.
"""
import hashlib
import os
import re
import tempfile
from pathlib import Path

_KEY = re.compile(r"^[A-Za-z0-9_-]{1,80}/v[0-9]{1,6}\.(pdf|docx|xlsx)$")
_PLAIN_TENANT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class LocalFolderFileStore:
    def __init__(self, root: str | Path):
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, tenant_id: str, key: str, data: bytes) -> None:
        path = self._path(tenant_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file, then rename: never a half-written file.
        handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".part")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def get(self, tenant_id: str, key: str) -> bytes:
        return self._path(tenant_id, key).read_bytes()

    def delete(self, tenant_id: str, key: str) -> None:
        path = self._path(tenant_id, key)
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()   # only succeeds when the document folder is empty
        except OSError:
            pass

    def _path(self, tenant_id: str, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ValueError("Invalid storage key.")
        path = (self._root / _tenant_folder(tenant_id) / key).resolve()
        if self._root not in path.parents:
            raise ValueError("Invalid storage key.")
        return path


def _tenant_folder(tenant_id: str) -> str:
    # Readable when safe; otherwise a fixed-length fingerprint.
    if _PLAIN_TENANT.fullmatch(tenant_id):
        return tenant_id
    return "t_" + hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:32]
