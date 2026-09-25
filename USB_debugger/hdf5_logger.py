import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, cast

import h5py
import numpy as np

from protocol_codec import LogPayload
from config import LOG_FOLDER

_LOG = logging.getLogger(__name__)


def get_log_filename(log_folder: Path | str | None = None) -> Path:
    """Return a unique HDF5 filename in the configured telemetry folder."""
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S_%f")
    log_dir = Path(LOG_FOLDER if log_folder is None else log_folder)
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"debug_log_{timestamp}.h5"


class HDF5LogLogger:
    def __init__(
        self,
        filename: Path,
        batch_size: int = 100,
        flush_interval: float = 1.0,
        max_queue_size: int = 10000,
    ):
        self.filename = Path(filename)
        self.batch_size = int(batch_size)
        self.flush_interval = float(flush_interval)
        if self.batch_size <= 0 or self.flush_interval <= 0 or max_queue_size <= 0:
            raise ValueError("batch_size, flush_interval, and max_queue_size must be positive")
        self.queue: queue.Queue[LogPayload] = queue.Queue(maxsize=max_queue_size)
        self.dropped_payloads = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.file: Optional[h5py.File] = None
        self.initialized = False
        self.signal_names: list[str] = []

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout=timeout)

    def enqueue(self, payload: LogPayload) -> None:
        """Queue non-empty payloads; count dropped items rather than hiding loss."""
        if payload.sample_count <= 0:
            return
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            self.dropped_payloads += 1
            if self.dropped_payloads == 1 or self.dropped_payloads % 100 == 0:
                _LOG.warning("HDF5 logger queue full; dropped %d payloads", self.dropped_payloads)

    def _open_file(self) -> None:
        if self.file is None:
            self.file = h5py.File(self.filename, "x")

    def _ensure_datasets(self, payload: LogPayload) -> None:
        if self.file is None:
            raise RuntimeError("HDF5 file is not open")

        if not self.initialized:
            self.file.require_group("log")
            self.file.create_dataset(
                "log/time", shape=(0,), maxshape=(None,), dtype=np.uint32, chunks=True
            )
            self.file.create_dataset(
                "log/sample_count", shape=(0,), maxshape=(None,), dtype=np.uint32, chunks=True
            )
            self.file.create_dataset(
                "log/signal_count", shape=(0,), maxshape=(None,), dtype=np.uint32, chunks=True
            )
            self.initialized = True

        time_ds = cast(h5py.Dataset, self.file["log/time"])
        self.signal_names = list(dict.fromkeys(self.signal_names + list(payload.signals)))
        for name in self.signal_names:
            dataset_path = f"log/{name}"
            if dataset_path not in self.file:
                # float64 stores every uint32 value exactly and also permits NaN
                # for samples when a logging mask did not include this signal.
                signal_ds = self.file.create_dataset(
                    dataset_path,
                    shape=(time_ds.shape[0],),
                    maxshape=(None,),
                    dtype=np.float64,
                    chunks=True,
                    fillvalue=np.nan,
                )
                if time_ds.shape[0]:
                    signal_ds[:] = np.nan

        self.file.attrs["signal_names"] = np.array(
            self.signal_names,
            dtype=h5py.string_dtype(encoding="utf-8"),
        )

    def _append_1d(self, ds: h5py.Dataset, values: np.ndarray) -> None:
        old_len = ds.shape[0]
        new_len = old_len + len(values)
        ds.resize((new_len,))
        ds[old_len:new_len] = values

    def _flush_batch(self, batch: list[LogPayload]) -> None:
        if not batch:
            return

        for payload in batch:
            if payload.sample_count < 0:
                raise ValueError("sample_count must be non-negative")
            if payload.signal_count != len(payload.signals):
                raise ValueError(
                    f"signal_count={payload.signal_count} but "
                    f"{len(payload.signals)} signal arrays supplied"
                )
            if not 0 <= payload.timestamp <= 0xFFFFFFFF:
                raise ValueError(f"timestamp is outside uint32 range: {payload.timestamp}")
            for name, values in payload.signals.items():
                if len(values) != payload.sample_count:
                    raise ValueError(
                        f"Signal {name!r} has {len(values)} values; "
                        f"expected {payload.sample_count}"
                    )
            self._ensure_datasets(payload)

        assert self.file is not None
        time_parts = []
        for payload in batch:
            offsets = np.arange(payload.sample_count, dtype=np.uint64)
            time_parts.append(
                ((int(payload.timestamp) + offsets) & 0xFFFFFFFF).astype(np.uint32)
            )
        time_values = np.concatenate(time_parts) if time_parts else np.empty(0, dtype=np.uint32)
        self._append_1d(cast(h5py.Dataset, self.file["log/time"]), time_values)

        self._append_1d(
            cast(h5py.Dataset, self.file["log/sample_count"]),
            np.asarray([payload.sample_count for payload in batch], dtype=np.uint32),
        )
        self._append_1d(
            cast(h5py.Dataset, self.file["log/signal_count"]),
            np.asarray([payload.signal_count for payload in batch], dtype=np.uint32),
        )

        for name in self.signal_names:
            value_parts = []
            for payload in batch:
                if name in payload.signals:
                    value_parts.append(np.asarray(payload.signals[name], dtype=np.float64))
                else:
                    value_parts.append(np.full(payload.sample_count, np.nan, dtype=np.float64))
            values = np.concatenate(value_parts) if value_parts else np.empty(0, dtype=np.float64)
            self._append_1d(cast(h5py.Dataset, self.file[f"log/{name}"]), values)

        self.file.flush()

    def _run(self) -> None:
        batch: list[LogPayload] = []
        last_flush = time.monotonic()

        try:
            while not self.stop_event.is_set() or not self.queue.empty():
                timeout = max(
                    0.0,
                    self.flush_interval - (time.monotonic() - last_flush),
                )

                try:
                    payload = self.queue.get(timeout=timeout)
                    batch.append(payload)
                    self.queue.task_done()
                except queue.Empty:
                    pass

                now = time.monotonic()
                should_flush = bool(batch) and (
                    len(batch) >= self.batch_size
                    or (now - last_flush) >= self.flush_interval
                    or (self.stop_event.is_set() and batch)
                )
                if should_flush:
                    try:
                        self._open_file()
                        self._flush_batch(batch)
                    except Exception:
                        # Retain the payloads and retry after the next flush
                        # interval, allowing transient file contention to pass.
                        if not self.stop_event.is_set():
                            _LOG.exception("Unable to flush HDF5 log batch; will retry")
                            time.sleep(min(self.flush_interval, 0.1))
                            continue
                        raise
                    batch.clear()
                    last_flush = now

            if batch:
                self._open_file()
                self._flush_batch(batch)
        except Exception:
            _LOG.exception("HDF5 logging worker failed")
        finally:
            if self.file is not None:
                self.file.flush()
                self.file.close()
                self.file = None