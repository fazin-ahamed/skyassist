import os

from skyassist.env import load_dotenv, parse


def test_parse_reads_plain_quoted_and_exported_values():
    text = "\n".join([
        "# a comment", "", "DEEPGRAM_API_KEY=abc123", 'QUOTED="two words"', "SINGLE='x y'",
        "export EXPORTED=yes", "INLINE=value # trailing note", "not a pair", "BAD-NAME=1", "EMPTY=",
    ])
    assert parse(text) == {"DEEPGRAM_API_KEY": "abc123", "QUOTED": "two words", "SINGLE": "x y", "EXPORTED": "yes",
                           "INLINE": "value", "EMPTY": ""}


def test_load_dotenv_never_overrides_the_real_environment(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    target.write_text("SA_TEST_KEEP=from-file\nSA_TEST_NEW=from-file\nSA_TEST_BLANK=\n", encoding="utf-8")
    monkeypatch.setenv("SA_TEST_KEEP", "from-env")
    monkeypatch.delenv("SA_TEST_NEW", raising=False)
    monkeypatch.delenv("SA_TEST_BLANK", raising=False)
    assert load_dotenv(target) == ["SA_TEST_NEW"]
    assert os.environ["SA_TEST_KEEP"] == "from-env" and os.environ["SA_TEST_NEW"] == "from-file"
    assert "SA_TEST_BLANK" not in os.environ
    monkeypatch.delenv("SA_TEST_NEW")


def test_a_missing_file_is_fine(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == []
