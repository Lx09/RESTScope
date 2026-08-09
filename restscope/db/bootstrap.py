"""Fresh SQLite database preparation for the default App runtime."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import sqlite3
import stat

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from restscope.db.migrations import MIGRATIONS_DIR
from restscope.config import DBConfig


class DatabaseBootstrapError(RuntimeError):
    """Stable error raised while preparing the App-owned database."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DatabaseAlreadyExistsError(DatabaseBootstrapError):
    """The configured one-shot SQLite database path is already occupied."""

    def __init__(self) -> None:
        super().__init__(
            "database_already_exists",
            "Configured SQLite database already exists",
        )


class UnsupportedDatabaseURLError(DatabaseBootstrapError):
    """The configured URL is not a supported local file SQLite database."""

    def __init__(self) -> None:
        super().__init__(
            "database_url_unsupported",
            "Default RESTScope runtime requires a local file SQLite database",
        )


@dataclass(frozen=True, slots=True)
class _FreshSQLiteDatabase:
    """A database file exclusively claimed by the current App construction."""

    path: Path
    removable_sidecars: tuple[Path, ...]
    device: int
    inode: int

    def cleanup(self) -> None:
        """Best-effort removal of files that this construction could have created."""

        for path in self.removable_sidecars:
            if not self._primary_matches_claim():
                return
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # Preserve the original construction failure if cleanup itself fails.
                pass
        if not self._primary_matches_claim():
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # Preserve the original construction failure if cleanup itself fails.
            pass

    def _primary_matches_claim(self) -> bool:
        try:
            current = self.path.lstat()
        except OSError:
            return False
        return (
            stat.S_ISREG(current.st_mode)
            and current.st_dev == self.device
            and current.st_ino == self.inode
        )


def prepare_fresh_sqlite(config: DBConfig) -> tuple[DBConfig, _FreshSQLiteDatabase]:
    """Claim a new SQLite file, migrate it to head, and return normalized config."""

    normalized_config, database_path = _normalize_sqlite_config(config)
    sidecars = tuple(
        database_path.with_name(f"{database_path.name}{suffix}")
        for suffix in ("-journal", "-wal", "-shm")
    )
    removable_sidecars = tuple(
        path for path in sidecars if not os.path.lexists(path)
    )
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        raise DatabaseBootstrapError(
            "database_bootstrap_failed",
            "Failed to prepare configured SQLite database",
        ) from exc

    try:
        descriptor = os.open(
            database_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise DatabaseAlreadyExistsError() from exc
    except (OSError, ValueError) as exc:
        raise DatabaseBootstrapError(
            "database_bootstrap_failed",
            "Failed to prepare configured SQLite database",
        ) from exc

    database: _FreshSQLiteDatabase | None = None
    descriptor_owned = True
    try:
        try:
            claimed = os.fstat(descriptor)
        except BaseException:
            # Preserve enough identity for safe cleanup even when the public
            # fstat call itself fails or is interrupted.
            try:
                claimed = os.stat(descriptor)
                database = _FreshSQLiteDatabase(
                    path=database_path,
                    removable_sidecars=removable_sidecars,
                    device=claimed.st_dev,
                    inode=claimed.st_ino,
                )
            except BaseException:
                pass
            raise
        database = _FreshSQLiteDatabase(
            path=database_path,
            removable_sidecars=removable_sidecars,
            device=claimed.st_dev,
            inode=claimed.st_ino,
        )
        # Relinquish descriptor ownership before close: if close raises, its
        # platform state is ambiguous and retrying could close a reused fd.
        descriptor_owned = False
        os.close(descriptor)
    except BaseException as exc:
        if descriptor_owned:
            descriptor_owned = False
            try:
                os.close(descriptor)
            except BaseException:
                pass
        if database is not None:
            database.cleanup()
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, (OSError, ValueError)):
            raise DatabaseBootstrapError(
                "database_bootstrap_failed",
                "Failed to prepare configured SQLite database",
            ) from exc
        raise

    assert database is not None
    try:
        alembic_config = Config()
        alembic_config.set_main_option("script_location", str(MIGRATIONS_DIR))
        alembic_config.set_main_option(
            "sqlalchemy.url",
            normalized_config.url.replace("%", "%%"),
        )
        command.upgrade(alembic_config, "head")
        _verify_sqlite_database(database_path)
    except BaseException as exc:
        database.cleanup()
        if not isinstance(exc, Exception):
            raise
        raise DatabaseBootstrapError(
            "database_bootstrap_failed",
            "Failed to prepare configured SQLite database",
        ) from exc

    return normalized_config, database


def _verify_sqlite_database(database_path: Path) -> None:
    """Reject a freshly migrated file with integrity or foreign-key failures.

    The database is still owned by the current bootstrap attempt when this
    check runs, so any failure follows the existing safe cleanup path and cannot
    alter a pre-existing user file.
    """

    connection = sqlite3.connect(database_path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    if integrity != ("ok",) or foreign_key_issues:
        raise RuntimeError("Fresh SQLite database failed integrity verification")


def _normalize_sqlite_config(config: DBConfig) -> tuple[DBConfig, Path]:
    """Resolve the claimed SQLite file to an absolute URL while preserving other database settings."""
    try:
        url = make_url(config.url)
    except (ArgumentError, TypeError, ValueError) as exc:
        raise UnsupportedDatabaseURLError() from exc

    database = url.database
    has_authority = any(
        value is not None
        for value in (url.host, url.username, url.password, url.port)
    )
    raw_uri_values = url.query.get("uri")
    uri_repeated = isinstance(raw_uri_values, tuple)
    uri_values = (
        raw_uri_values
        if uri_repeated
        else (() if raw_uri_values is None else (raw_uri_values,))
    )
    uri_requested = any(
        str(value).strip().lower() in {"1", "true", "yes", "on"}
        for value in uri_values
    )
    if (
        url.get_backend_name() != "sqlite"
        or has_authority
        or not database
        or database == ":memory:"
        or database.startswith("file:")
        or uri_repeated
        or uri_requested
    ):
        raise UnsupportedDatabaseURLError()

    database_path = Path(database).expanduser()
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    # Make the location absolute without following a final symlink: the
    # exclusive open below must reject the link itself, including broken links.
    database_path = Path(os.path.abspath(database_path))
    normalized_url = url.set(database=str(database_path)).render_as_string(
        hide_password=False
    )
    return replace(config, url=normalized_url), database_path
