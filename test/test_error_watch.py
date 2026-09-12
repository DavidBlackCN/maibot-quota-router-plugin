import json
import os
import tempfile
import time
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from modules.error_watch import ErrorSnapshotWatcher


class _Logger:
    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass

    def debug(self, *args, **kwargs) -> None:
        pass


def _snapshot(path: Path, mtime: float) -> None:
    path.write_text(
        json.dumps(
            {
                "attempts": [
                    {
                        "model_name": path.stem,
                        "error": {"type": "timeout", "message": "timed out"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))


class ErrorSnapshotWatcherTests(IsolatedAsyncioTestCase):
    async def test_bootstrap_dedup_and_eviction_do_not_reprocess_old_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "llm_error"
            root.mkdir()
            base = time.time() - 20
            historical = root / "historical.json"
            _snapshot(historical, base)
            handled = []

            async def on_error(**kwargs):
                handled.append(Path(kwargs["source_path"]).name)

            watcher = ErrorSnapshotWatcher(
                roots=[root], interval_seconds=0.5, on_error=on_error, logger=_Logger()
            )
            watcher._SEEN_LIMIT = 3
            watcher._bootstrap()
            await watcher._scan_once()
            self.assertEqual(handled, [])

            for index in range(5):
                _snapshot(root / f"new-{index}.json", base + index + 1)
            await watcher._scan_once()
            self.assertEqual(handled, [f"new-{index}.json" for index in range(5)])
            self.assertLessEqual(len(watcher._seen), 3)
            self.assertNotIn(str(historical.resolve()), watcher._seen)

            await watcher._scan_once()
            self.assertEqual(handled, [f"new-{index}.json" for index in range(5)])
