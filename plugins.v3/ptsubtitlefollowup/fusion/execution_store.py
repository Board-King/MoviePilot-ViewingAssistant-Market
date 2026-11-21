"""Durable single-writer subscription journal shared by MP workers on this host."""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid


DATA_KEY = "fusion_execution_journal_v1"
EMPTY_JOURNAL = '{"schema":1,"grant":null,"intents":{},"scan_after":null,"last_reason":"NOT_AUTHORIZED"}'
MAX_JOURNAL_BYTES = 32 * 1024 * 1024
RECOVERY_REQUIRED = "Execution journal requires recovery from a matching backup"


@contextmanager
def _file_lock(path, wait=0):
    deadline = time.monotonic() + wait
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            # Windows byte-range locks may extend past EOF. Pre-writing a sentinel
            # races the other worker's lock when both handles see an empty file.
        else:
            import fcntl
        while True:
            try:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise BlockingIOError("Execution journal is busy") from exc
                time.sleep(0.02)
        yield


class ExecutionStore:
    def __init__(self, directory):
        self._directory = directory
        self._local = threading.local()

    def exists(self):
        directory = Path(self._directory())
        # Missing either member of an initialized pair is a fault, not first use.
        return any(os.path.lexists(directory / name) for name in ("execution.sqlite3", "execution.identity"))

    @contextmanager
    def locked(self):
        # Held across the native call. The short state lock below is deliberately
        # independent so a second worker can revoke authorization during that call.
        directory = Path(self._directory()).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        with _file_lock(directory / "execution.lock"):
            self._local.directory = directory
            try:
                yield
            finally:
                self._local.directory = None

    def _locked_directory(self):
        directory = getattr(self._local, "directory", None)
        if directory is None:
            raise RuntimeError("Journal operations require the coordinator lock")
        return directory

    @staticmethod
    def _publish(db, marker):
        # Seal every mutation so restoring an older database alone cannot revive
        # its grants or erase later UNKNOWN records. A failure between the two
        # durable writes deliberately leaves a mismatching pair, never an old grant.
        revision = uuid.uuid4().hex
        if db.execute("UPDATE store_identity SET revision=? WHERE id=1", (revision,)).rowcount != 1:
            raise RuntimeError(RECOVERY_REQUIRED)
        temp = marker.with_name(f".{marker.name}.{revision}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(revision.encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, marker)
            if os.name != "nt":
                parent = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            db.commit()
        finally:
            temp.unlink(missing_ok=True)

    @contextmanager
    def _database(self, directory, *, write=False, initialize_digest=None):
        # This lock only spans local reads/commits, never host or network calls.
        # It makes first use and the two-file revision change unambiguous across workers.
        if not write and not directory.exists():
            yield None
            return
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "execution.sqlite3"
        marker = directory / "execution.identity"
        with _file_lock(directory / "execution.state.lock", wait=3):
            exists, sealed = os.path.lexists(path), os.path.lexists(marker)
            if not exists and not sealed and initialize_digest is None:
                if write:
                    raise RuntimeError("Configure the execution scope before writing")
                yield None
                return
            first = not exists and not sealed
            if not first:
                if (not (exists and sealed) or path.is_symlink() or marker.is_symlink()
                        or not path.is_file() or not marker.is_file() or marker.stat().st_size != 32):
                    raise RuntimeError(RECOVERY_REQUIRED)
                with path.open("rb") as handle:
                    if handle.read(16) != b"SQLite format 3\x00":
                        raise RuntimeError(RECOVERY_REQUIRED)
            mode = "rwc" if first else "rw" if write else "ro"
            db = sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True, timeout=2, isolation_level=None)
            try:
                # Bound decoding on full journal reads without materializing the
                # journal merely to check a scope fence during native dispatch.
                db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_JOURNAL_BYTES + 2048)
                if write:
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute("BEGIN IMMEDIATE")
                else:
                    db.execute("PRAGMA query_only=ON")
                    db.execute("BEGIN")
                if first:
                    db.execute("CREATE TABLE store_identity (id INTEGER PRIMARY KEY CHECK(id=1), revision TEXT NOT NULL)")
                    db.execute("CREATE TABLE scope_fence (id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL, nonce TEXT NOT NULL)")
                    db.execute("CREATE TABLE journal (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    db.execute("INSERT INTO store_identity VALUES (1,'')")
                    db.execute("INSERT INTO scope_fence VALUES (1,?,?)", (initialize_digest, uuid.uuid4().hex))
                    db.execute("INSERT INTO journal VALUES (?,?)", (DATA_KEY, EMPTY_JOURNAL))
                    self._publish(db, marker)
                else:
                    revision = marker.read_bytes()
                    ident = db.execute("SELECT revision FROM store_identity WHERE id=1").fetchone()
                    fence = db.execute("SELECT digest,nonce FROM scope_fence WHERE id=1").fetchone()
                    payload = db.execute("SELECT typeof(value) FROM journal WHERE key=?", (DATA_KEY,)).fetchone()
                    if (not re.fullmatch(rb"[0-9a-f]{32}", revision) or ident != (revision.decode("ascii"),)
                            or not fence or not all(isinstance(s, str) for s in fence)
                            or not re.fullmatch("[0-9a-f]{64}", fence[0]) or not re.fullmatch("[0-9a-f]{32}", fence[1])
                            or payload != ("text",)):
                        raise RuntimeError(RECOVERY_REQUIRED)
                yield db
            finally:
                db.close()

    def scope_fence(self, config_hash, *, force=False):
        if not isinstance(config_hash, str) or not re.fullmatch("[0-9a-f]{64}", config_hash):
            raise ValueError("Invalid configuration digest")
        directory = Path(self._directory()).resolve()
        with self._database(directory, write=True, initialize_digest=config_hash) as db:
            previous, nonce = db.execute("SELECT digest,nonce FROM scope_fence WHERE id=1").fetchone()
            if previous == config_hash and not force:
                return nonce
            # Initial creation committed the seed and marker as one operation.
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            nonce = uuid.uuid4().hex
            db.execute("UPDATE scope_fence SET digest=?,nonce=? WHERE id=1", (config_hash, nonce))
            self._publish(db, directory / "execution.identity")
            return nonce

    def read_fence(self, config_hash):
        directory = Path(self._directory()).resolve()
        with self._database(directory) as db:
            if db is None:
                return None
            row = db.execute("SELECT nonce FROM scope_fence WHERE id=1 AND digest=?", (config_hash,)).fetchone()
            return row[0] if row else None

    def read(self, key):
        directory = self._locked_directory()
        with self._database(directory) as db:
            if db is None:
                return None
            row = db.execute("SELECT value FROM journal WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def write(self, key, value):
        if key != DATA_KEY:
            raise ValueError("Unrecognized execution journal key")
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(payload.encode()) > MAX_JOURNAL_BYTES:
            raise ValueError("Execution journal exceeds storage limit")
        directory = self._locked_directory()
        with self._database(directory, write=True) as db:
            if db.execute("UPDATE journal SET value=? WHERE key=?", (payload, key)).rowcount != 1:
                raise RuntimeError(RECOVERY_REQUIRED)
            self._publish(db, directory / "execution.identity")
