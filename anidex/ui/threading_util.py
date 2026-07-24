from __future__ import annotations

from PySide6.QtCore import QObject, QThread


def _is_valid(obj: object) -> bool:
    if obj is None:
        return False
    try:
        from shiboken6 import isValid

        return bool(isValid(obj))
    except Exception:
        try:
            # Fallback: touching a Qt method may raise if C++ side is gone
            obj.objectName()  # type: ignore[attr-defined]
            return True
        except RuntimeError:
            return False


def start_worker(worker: QThread, owner: QObject | None = None) -> QThread:
    """Parent worker, track on owner, deleteLater only after finished."""
    if owner is not None:
        worker.setParent(owner)
        alive: list[QThread] = getattr(owner, "_alive_workers", None)  # type: ignore[assignment]
        if alive is None:
            alive = []
            setattr(owner, "_alive_workers", alive)
        alive.append(worker)

        def _cleanup() -> None:
            try:
                alive.remove(worker)
            except ValueError:
                pass

        worker.finished.connect(_cleanup)

    # Avoid double-connecting deleteLater
    if not getattr(worker, "_lf_autodelete", False):
        worker.finished.connect(worker.deleteLater)
        setattr(worker, "_lf_autodelete", True)
    worker.start()
    return worker


def stop_worker(worker: QThread | None, timeout_ms: int = 5000) -> None:
    if not _is_valid(worker):
        return
    assert worker is not None
    try:
        if worker.isRunning():
            worker.requestInterruption()
            if not worker.wait(timeout_ms):
                # Last resort — prefer leak over crash if terminate is unsafe
                worker.terminate()
                worker.wait(1000)
    except RuntimeError:
        # C++ object already gone
        return
