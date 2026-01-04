import settings
from erieiron_common.templatetags import erieiron_common_tags


def test_timestamp_static_copies_latest_file_into_static_root(monkeypatch, tmp_path):
    compiled_dir = tmp_path / "compiled"
    static_root = tmp_path / "staticfiles"
    compiled_dir.mkdir()
    static_root.mkdir()

    older = compiled_dir / "style-20240101000000.css"
    older.write_text("old")
    newer = compiled_dir / "style-20260202020202.css"
    newer.write_text("new")

    monkeypatch.setattr(settings, "STATIC_COMPILED_DIR", str(compiled_dir))
    monkeypatch.setattr(settings, "STATIC_ROOT", str(static_root))

    asset_path = erieiron_common_tags.timestamp_static("style.css")

    assert asset_path.endswith(newer.name)
    assert (static_root / compiled_dir.name / newer.name).read_text() == "new"
    assert not older.exists()
    assert not (static_root / compiled_dir.name / older.name).exists()
