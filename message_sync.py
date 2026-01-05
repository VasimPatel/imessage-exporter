#!/usr/bin/env python3
"""
Background service to sync the macOS Messages database to a local directory.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import importlib
import importlib.util
import json
import logging
import logging.handlers
import os
import platform
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

yaml_spec = importlib.util.find_spec("yaml")
yaml = importlib.import_module("yaml") if yaml_spec else None  # type: ignore


DEFAULT_CONFIG_PATHS = (Path("config.yaml"), Path("config.json"))


@dataclasses.dataclass
class SyncConfig:
    sync_interval: float = 6.0
    source_path: Path = Path.home() / "Library" / "Messages" / "chat.db"
    destination_dir: Path = Path("messages")
    keep_backups: int = 0
    log_level: str = "INFO"
    log_dir: Path = Path("logs")
    pid_file: Path = Path("message_sync.pid")
    state_file: Path = Path("message_sync_state.json")
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 3

    def normalize(self) -> "SyncConfig":
        self.source_path = self.source_path.expanduser().resolve()
        self.destination_dir = self.destination_dir.expanduser().resolve()
        self.log_dir = self.log_dir.expanduser().resolve()
        self.pid_file = self.pid_file.expanduser().resolve()
        self.state_file = self.state_file.expanduser().resolve()
        self.log_level = self.log_level.upper()
        self.sync_interval = max(0.1, float(self.sync_interval))
        self.keep_backups = max(0, int(self.keep_backups))
        self.log_max_bytes = max(1024, int(self.log_max_bytes))
        self.log_backup_count = max(1, int(self.log_backup_count))
        return self


def load_config_file(path: Path) -> Dict[str, Any]:
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML is required to load YAML configuration files.")
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    else:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

    if not isinstance(data, dict):
        raise ValueError(f"Configuration file {path} must contain a mapping/object.")
    return data


def load_config(args: argparse.Namespace) -> SyncConfig:
    raw_config: Dict[str, Any] = {}
    config_path: Optional[Path] = Path(args.config).expanduser() if args.config else None
    if not config_path:
        for default_path in DEFAULT_CONFIG_PATHS:
            if default_path.exists():
                config_path = default_path
                break

    if config_path and config_path.exists():
        raw_config.update(load_config_file(config_path))

    overrides = {
        "sync_interval": args.interval,
        "source_path": Path(args.source_path).expanduser() if args.source_path else None,
        "destination_dir": Path(args.destination_dir).expanduser() if args.destination_dir else None,
        "keep_backups": args.keep_backups,
        "log_level": args.log_level.upper() if args.log_level else None,
    }

    for key, value in overrides.items():
        if value is not None:
            raw_config[key] = value

    numeric_fields = ("sync_interval", "keep_backups", "log_max_bytes", "log_backup_count")
    for field_name in numeric_fields:
        if field_name in raw_config and raw_config[field_name] is not None:
            raw_config[field_name] = float(raw_config[field_name]) if field_name == "sync_interval" else int(raw_config[field_name])

    path_fields = ("source_path", "destination_dir", "log_dir", "pid_file", "state_file")
    for field_name in path_fields:
        if field_name in raw_config and not isinstance(raw_config[field_name], Path):
            raw_config[field_name] = Path(raw_config[field_name])

    config = SyncConfig(**raw_config).normalize()
    if args.config and not (config_path and config_path.exists()):
        raise FileNotFoundError(f"Configuration file not found: {args.config}")
    return config


def setup_logging(config: SyncConfig) -> logging.Logger:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("message_sync")
    logger.setLevel(getattr(logging, config.log_level, logging.INFO))
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        config.log_dir / "sync.log",
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(getattr(logging, config.log_level, logging.INFO))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, config.log_level, logging.INFO))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


def is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def write_pid_file(pid_file: Path, logger: logging.Logger) -> None:
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip())
        except ValueError:
            existing_pid = None  # type: ignore
        else:
            if existing_pid and is_process_running(existing_pid):
                raise RuntimeError(f"Service already running with PID {existing_pid}")
        pid_file.unlink(missing_ok=True)

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))
    logger.debug("Wrote PID file to %s", pid_file)


def remove_pid_file(pid_file: Path, logger: logging.Logger) -> None:
    if pid_file.exists():
        pid_file.unlink()
        logger.debug("Removed PID file %s", pid_file)


def daemonize() -> None:
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull, "rb", buffering=0) as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())
    with open(os.devnull, "ab", buffering=0) as devnull:
        os.dup2(devnull.fileno(), sys.stdout.fileno())
        os.dup2(devnull.fileno(), sys.stderr.fileno())


class MessageSyncService:
    def __init__(self, config: SyncConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.stop_event = threading.Event()

    def start(self) -> None:
        self.logger.info("Starting message sync service")
        if platform.system() != "Darwin":
            self.logger.warning("This service is intended for macOS. Current platform: %s", platform.system())

        if not self.config.source_path.exists():
            self.logger.error("Source database not found at %s", self.config.source_path)
            raise FileNotFoundError(f"Source database not found at {self.config.source_path}")

        write_pid_file(self.config.pid_file, self.logger)
        signal.signal(signal.SIGTERM, self._handle_stop_signal)
        signal.signal(signal.SIGINT, self._handle_stop_signal)

        try:
            self._run_loop()
        finally:
            remove_pid_file(self.config.pid_file, self.logger)
            self.logger.info("Message sync service stopped")

    def _run_loop(self) -> None:
        self.logger.info("Initial sync starting")
        self._safe_sync()
        interval_seconds = self.config.sync_interval * 3600
        while not self.stop_event.wait(interval_seconds):
            self.logger.info("Scheduled sync starting")
            self._safe_sync()

    def _safe_sync(self) -> None:
        try:
            success, details = self.perform_sync()
            if success:
                self.logger.info(
                    "Sync completed successfully. Copied files: %s",
                    ", ".join(f"{name} ({size} bytes)" for name, size in details.items()),
                )
            else:
                self.logger.error("Sync encountered errors; see above logs for details.")
        except Exception as exc:  # pragma: no cover - safety net
            self.logger.exception("Unexpected error during sync: %s", exc)

    def perform_sync(self) -> tuple[bool, Dict[str, int]]:
        source_files = [self.config.source_path]
        for suffix in ("-shm", "-wal"):
            candidate = self.config.source_path.with_name(self.config.source_path.name + suffix)
            if candidate.exists():
                source_files.append(candidate)
            else:
                self.logger.debug("Optional source file missing, skipping: %s", candidate)

        self.config.destination_dir.mkdir(parents=True, exist_ok=True)

        success = True
        copied_sizes: Dict[str, int] = {}

        for src in source_files:
            dest = self.config.destination_dir / src.name
            file_success = self._copy_with_retry(src, dest)
            success = success and file_success
            if file_success:
                copied_sizes[src.name] = dest.stat().st_size

        if success:
            self._handle_backups()
            self._record_last_sync()

        return success, copied_sizes

    def _copy_with_retry(self, src: Path, dest: Path, attempts: int = 5, base_delay: float = 1.0) -> bool:
        for attempt in range(1, attempts + 1):
            try:
                shutil.copy2(src, dest)
                if src.stat().st_size != dest.stat().st_size:
                    raise IOError(f"Copied size mismatch for {src.name}")
                return True
            except (PermissionError, OSError, IOError) as exc:
                if attempt == attempts:
                    self.logger.error("Failed to copy %s -> %s: %s", src, dest, exc)
                    return False
                delay = base_delay * (2 ** (attempt - 1))
                self.logger.warning(
                    "Error copying %s (attempt %s/%s): %s. Retrying in %.1f seconds",
                    src,
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                time.sleep(delay)
        return False

    def _handle_backups(self) -> None:
        if self.config.keep_backups <= 0:
            return

        timestamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
        primary = self.config.destination_dir / self.config.source_path.name
        backup_file = self.config.destination_dir / f"{self.config.source_path.name}.backup-{timestamp}"
        try:
            shutil.copy2(primary, backup_file)
            self.logger.info("Created backup %s", backup_file)
        except OSError as exc:
            self.logger.error("Failed to create backup %s: %s", backup_file, exc)
            return

        backups = sorted(
            self.config.destination_dir.glob(f"{self.config.source_path.name}.backup-*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_backup in backups[self.config.keep_backups :]:
            try:
                old_backup.unlink()
                self.logger.info("Removed old backup %s", old_backup)
            except OSError as exc:
                self.logger.warning("Failed to remove old backup %s: %s", old_backup, exc)

    def _record_last_sync(self) -> None:
        state = {"last_sync": dt.datetime.now(dt.timezone.utc).isoformat()}
        try:
            self.config.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.config.state_file.write_text(json.dumps(state, indent=2))
            self.logger.debug("Recorded last sync time to %s", self.config.state_file)
        except OSError as exc:
            self.logger.warning("Failed to write state file %s: %s", self.config.state_file, exc)

    def _handle_stop_signal(self, signum: int, _: Any) -> None:
        self.logger.info("Received signal %s; shutting down gracefully", signum)
        self.stop_event.set()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Background service to sync iMessage chat.db")
    parser.add_argument("command", choices=["start", "stop", "status", "sync-now"], nargs="?", default="start")
    parser.add_argument("--config", help="Path to YAML or JSON configuration file")
    parser.add_argument("--interval", type=float, help="Sync interval in hours")
    parser.add_argument("--source-path", help="Path to chat.db to sync")
    parser.add_argument("--destination-dir", help="Directory to place copied database")
    parser.add_argument("--keep-backups", type=int, help="Number of versioned backups to retain (0 to disable)")
    parser.add_argument("--log-level", help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    parser.add_argument("--daemon", action="store_true", help="Run as a detached daemon")
    return parser.parse_args(argv)


def stop_service(config: SyncConfig) -> int:
    if not config.pid_file.exists():
        print("Service is not running (no PID file found).")
        return 1

    try:
        pid = int(config.pid_file.read_text().strip())
    except ValueError:
        print("PID file is invalid.")
        return 1

    if not is_process_running(pid):
        print(f"No running process found with PID {pid}. Cleaning up PID file.")
        config.pid_file.unlink(missing_ok=True)
        return 1

    os.kill(pid, signal.SIGTERM)
    print(f"Sent SIGTERM to process {pid}.")
    return 0


def service_status(config: SyncConfig) -> int:
    if not config.pid_file.exists():
        print("Service is not running.")
        return 1
    try:
        pid = int(config.pid_file.read_text().strip())
    except ValueError:
        print("PID file is invalid.")
        return 1

    if is_process_running(pid):
        print(f"Service is running with PID {pid}.")
        return 0
    print("Service PID file exists but process is not running.")
    return 1


def run_single_sync(config: SyncConfig) -> int:
    logger = setup_logging(config)
    service = MessageSyncService(config, logger)
    success, details = service.perform_sync()
    if success:
        logger.info(
            "Manual sync completed. Copied files: %s",
            ", ".join(f"{name} ({size} bytes)" for name, size in details.items()),
        )
        return 0
    logger.error("Manual sync failed.")
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args)
    except Exception as exc:
        print(f"Failed to load configuration: {exc}")
        return 1

    if args.command == "stop":
        return stop_service(config)
    if args.command == "status":
        return service_status(config)
    if args.command == "sync-now":
        return run_single_sync(config)

    if args.daemon:
        daemonize()

    logger = setup_logging(config)
    service = MessageSyncService(config, logger)
    try:
        service.start()
    except Exception as exc:
        logger.error("Service failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
