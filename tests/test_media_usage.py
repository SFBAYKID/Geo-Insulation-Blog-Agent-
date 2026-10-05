"""Photo cooldown protects different articles while preserving reviewed draft identity."""

from geo_blog.media_usage import PhotoUsage


def test_source_reuse_cooldown_and_same_article(tmp_path):
    usage = PhotoUsage(tmp_path)
    assert usage.reserve("drive-photo", "article-one", now=1000)
    assert usage.reserve("drive-photo", "article-one", now=2000)
    assert not usage.reserve("drive-photo", "article-two", now=2000)
    assert usage.reserve("drive-photo", "article-two", now=1000 + 91 * 86400)
    assert usage.rank("unused-photo", "article-three") < usage.rank("drive-photo", "article-three")
    assert usage.rank("drive-photo", "article-one")[0] == -1


def test_history_survives_restart(tmp_path):
    assert PhotoUsage(tmp_path).reserve("original", "one", now=100)
    assert not PhotoUsage(tmp_path).reserve("original", "two", now=101)
