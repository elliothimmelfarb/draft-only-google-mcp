import os

import pytest

from draft_only_google_mcp import server


def test_same_filename_twice_keeps_both_files(google):
    first = server.download_attachment("abc", "abc", "report.pdf")
    second = server.download_attachment("abc", "abc", "report.pdf")
    assert first != second
    assert first.endswith(".pdf") and "report" in first


def test_a_planted_link_at_the_target_is_not_followed(google, tmp_path, monkeypatch, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "target"
    monkeypatch.setattr(server.secrets, "token_hex", lambda n: "fixed")
    os.symlink(outside, tmp_path / "a-fixed.txt")
    with pytest.raises(OSError):
        server.download_attachment("abc", "abc", "a.txt")
    assert not outside.exists()


@pytest.mark.parametrize("name", ["../../evil", "/etc/passwd", "..", "a/..", ".", "", "x\x00y", "a" * 5000 + ".pdf"],
                         ids=range(8))
def test_hostile_names_land_inside_the_attachment_folder(google, tmp_path, name):
    saved = server.download_attachment("abc", "abc", name)
    assert os.path.dirname(saved) == str(tmp_path)
    assert os.stat(saved).st_mode & 0o777 == 0o600
