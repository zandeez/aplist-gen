import os
from pathlib import Path


class EmptyResultError(Exception):
    pass


def write_output(target: Path, content: str, record_count: int) -> None:
    if record_count == 0:
        raise EmptyResultError(
            f"refusing to write {target}: poll returned zero records "
            "(leaving last-good file in place)"
        )

    tmp_path = target.with_name(f".{target.name}.tmp")
    tmp_path.write_text(content)
    os.replace(tmp_path, target)
