import pytest

from aplist_gen.io_utils import EmptyResultError, write_output


def test_write_output_writes_content(tmp_path):
    target = tmp_path / "aplist.xml"

    write_output(target, "some content\n", record_count=3)

    assert target.read_text() == "some content\n"


def test_write_output_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / "aplist.xml"

    write_output(target, "some content\n", record_count=3)

    leftovers = [p for p in tmp_path.iterdir() if p != target]
    assert leftovers == []


def test_write_output_refuses_empty_record_count(tmp_path):
    target = tmp_path / "aplist.xml"
    target.write_text("last-good content\n")

    with pytest.raises(EmptyResultError):
        write_output(target, "", record_count=0)

    # Refusing to write must not touch the existing last-good file.
    assert target.read_text() == "last-good content\n"
