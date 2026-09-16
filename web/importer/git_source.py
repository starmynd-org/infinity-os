"""Read immutable blobs from a local git repository without touching its working tree."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import ImportRefusal


@dataclass(frozen=True)
class BlobRef:
    path: str
    oid: str


class GitTree:
    def __init__(self, repo: Path, revision: str):
        self.repo = repo.resolve()
        self.revision = self._resolve_revision(revision)

    def _run(self, *args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo), *args],
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise ImportRefusal(
                "EXPORT-INCOMPLETE",
                str(self.repo),
                f"git could not read the local export: {exc}",
                "run the importer from Git Bash with Git for Windows available",
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ImportRefusal(
                "EXPORT-INCOMPLETE",
                str(self.repo),
                f"git exited {result.returncode}: {detail}",
                "provide an existing local repository and immutable revision",
            )
        return result

    def _resolve_revision(self, revision: str) -> str:
        result = self._run_unresolved("rev-parse", "--verify", f"{revision}^{{commit}}")
        return result.stdout.decode("ascii").strip()

    def _run_unresolved(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise ImportRefusal(
                "EXPORT-INCOMPLETE",
                str(self.repo),
                f"git could not read the local export: {exc}",
                "run the importer from Git Bash with Git for Windows available",
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ImportRefusal(
                "EXPORT-INCOMPLETE",
                str(self.repo),
                f"revision could not be resolved: {detail}",
                "provide an immutable commit that exists in the local repository",
            )
        return result

    @staticmethod
    def _safe_path(path: str) -> str:
        candidate = PurePosixPath(path.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
            raise ImportRefusal(
                "PATH-ESCAPE",
                path,
                "the requested member is not a confined repository-relative path",
                "use a normalized path below the exported repository root",
            )
        return candidate.as_posix()

    def read(self, path: str) -> bytes:
        safe = self._safe_path(path)
        return self._run("show", f"{self.revision}:{safe}").stdout

    def list_blobs(self, *prefixes: str) -> list[BlobRef]:
        safe_prefixes = [self._safe_path(prefix) for prefix in prefixes]
        result = self._run("ls-tree", "-r", "-z", self.revision, "--", *safe_prefixes)
        refs: list[BlobRef] = []
        for raw in result.stdout.split(b"\x00"):
            if not raw:
                continue
            metadata, raw_path = raw.split(b"\t", 1)
            mode, object_type, oid = metadata.decode("ascii").split(" ")
            if object_type != "blob":
                continue
            path = raw_path.decode("utf-8")
            refs.append(BlobRef(path=path, oid=oid))
        return refs

    def read_many(self, refs: list[BlobRef]):
        if not refs:
            return
        process = subprocess.Popen(
            ["git", "-C", str(self.repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        try:
            for ref in refs:
                process.stdin.write(ref.oid.encode("ascii") + b"\n")
                process.stdin.flush()
                header = process.stdout.readline().decode("ascii").strip().split(" ")
                if len(header) != 3 or header[1] != "blob":
                    raise ImportRefusal(
                        "EXPORT-INCOMPLETE",
                        ref.path,
                        "git did not return the declared blob",
                        "regenerate the export manifest from the frozen revision",
                    )
                size = int(header[2])
                data = process.stdout.read(size)
                separator = process.stdout.read(1)
                if len(data) != size or separator != b"\n":
                    raise ImportRefusal(
                        "EXPORT-INCOMPLETE",
                        ref.path,
                        "git returned a truncated blob",
                        "retry from a healthy local repository",
                    )
                yield ref.path, data
        finally:
            process.stdin.close()
            process.stdout.close()
            return_code = process.wait()
            if return_code != 0:
                raise ImportRefusal(
                    "EXPORT-INCOMPLETE",
                    str(self.repo),
                    f"git cat-file exited {return_code}",
                    "retry from a healthy local repository",
                )
