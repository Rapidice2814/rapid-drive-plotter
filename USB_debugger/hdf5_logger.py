import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, cast

import h5py
import numpy as np

from protocol_codec import DecodedLogPayload


def get_log_filename() -> Path:
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"debug_log_{timestamp}.h5"


def _dtype_from_values(values):
    first = values[0]
    if isinstance(first, (float, np.floating)):
        return np.float32
    return np.uint32


@dataclass
class _LogBatch:
    payloads: list[DecodedLogPayload]


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
        self.queue: queue.Queue[DecodedLogPayload] = queue.Queue(maxsize=max_queue_size)
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

    def enqueue(self, payload: DecodedLogPayload) -> None:
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            pass

    def _open_file(self) -> None:
        if self.file is None:
            self.file = h5py.File(self.filename, "a")

    def _ensure_datasets(self, payload: DecodedLogPayload) -> None:
        if self.file is None:
            raise RuntimeError("HDF5 file is not open")

        if self.initialized:
            return

        self.signal_names = list(payload.signals.keys())

        self.file.require_group("log")

        self.file.create_dataset(
            "log/time",
            shape=(0,),
            maxshape=(None,),
            dtype=np.uint32,
            chunks=True,
        )

        self.file.create_dataset(
            "log/sample_count",
            shape=(0,),
            maxshape=(None,),
            dtype=np.uint32,
            chunks=True,
        )

        self.file.create_dataset(
            "log/signal_count",
            shape=(0,),
            maxshape=(None,),
            dtype=np.uint32,
            chunks=True,
        )

        for name in self.signal_names:
            values = payload.signals[name]
            self.file.create_dataset(
                f"log/{name}",
                shape=(0,),
                maxshape=(None,),
                dtype=_dtype_from_values(values),
                chunks=True,
            )

        self.file.attrs["signal_names"] = np.array(
            self.signal_names,
            dtype=h5py.string_dtype(encoding="utf-8"),
        )

        self.initialized = True

    def _append_1d(self, ds: h5py.Dataset, values: np.ndarray) -> None:
        old_len = ds.shape[0]
        new_len = old_len + len(values)
        ds.resize((new_len,))
        ds[old_len:new_len] = values

    def _flush_batch(self, batch: list[DecodedLogPayload]) -> None:
        if not batch:
            return

        self._ensure_datasets(batch[0])
        assert self.file is not None

        time_parts: list[np.ndarray] = []
        signal_parts: dict[str, list[np.ndarray]] = {}

        for payload in batch:
            base_timestamp = int(payload.timestamp)
            sample_count = int(payload.sample_count)

            time_parts.append(
                np.arange(base_timestamp, base_timestamp + sample_count, dtype=np.uint32)
            )

            for name, values in payload.signals.items():
                signal_parts.setdefault(name, []).append(np.asarray(values))

        total_samples = sum(int(p.sample_count) for p in batch)
        time_array = np.concatenate(time_parts) if time_parts else np.empty((0,), dtype=np.uint32)

        time_ds = cast(h5py.Dataset, self.file["log/time"])
        old_len = time_ds.shape[0]
        new_len = old_len + total_samples
        time_ds.resize((new_len,))
        time_ds[old_len:new_len] = time_array

        sc_ds = cast(h5py.Dataset, self.file["log/sample_count"])
        sc_values = np.asarray([int(p.sample_count) for p in batch], dtype=np.uint32)
        self._append_1d(sc_ds, sc_values)

        sigc_ds = cast(h5py.Dataset, self.file["log/signal_count"])
        sigc_values = np.asarray([int(p.signal_count) for p in batch], dtype=np.uint32)
        self._append_1d(sigc_ds, sigc_values)

        for name, parts in signal_parts.items():
            ds = cast(h5py.Dataset, self.file[f"log/{name}"])
            values_np = np.concatenate(parts).astype(ds.dtype, copy=False)

            old_len = ds.shape[0]
            new_len = old_len + len(values_np)
            ds.resize((new_len,))
            ds[old_len:new_len] = values_np

        self.file.flush()

    def _run(self) -> None:
        self._open_file()
        batch: list[DecodedLogPayload] = []
        last_flush = datetime.now().timestamp()

        try:
            while not self.stop_event.is_set() or not self.queue.empty():
                timeout = max(
                    0.0,
                    self.flush_interval - (datetime.now().timestamp() - last_flush),
                )

                try:
                    payload = self.queue.get(timeout=timeout)
                    batch.append(payload)
                    self.queue.task_done()
                except queue.Empty:
                    pass

                now = datetime.now().timestamp()
                if batch and (
                    len(batch) >= self.batch_size
                    or (now - last_flush) >= self.flush_interval
                    or (self.stop_event.is_set() and batch)
                ):
                    self._flush_batch(batch)
                    batch.clear()
                    last_flush = now

            if batch:
                self._flush_batch(batch)

        finally:
            if self.file is not None:
                self.file.flush()
                self.file.close()
                self.file = None