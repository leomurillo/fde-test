"""Guards on the real item bank (bank/items), independent of the fixture bank used elsewhere."""

import os

import pytest

from app import bank
from app.taxonomy import load_taxonomy


@pytest.fixture()
def real_bank(monkeypatch):
    monkeypatch.delenv("FDE_BANK_DIR", raising=False)
    monkeypatch.setattr(bank, "ITEMS_DIR", bank.BANK_DIR / "items")
    items, report = bank.load_valid_bank(run_executables=False)
    return items, report


def test_real_bank_is_valid_and_locked(real_bank):
    items, report = real_bank
    assert report.errors == []
    assert len(items) >= 300
    lock = bank.json.loads(bank.LOCK_PATH.read_text())
    unlocked = [i.key for i in items if i.key not in lock]
    assert unlocked == [], "run `python -m app.bank --lock` after adding item versions"


def test_every_subtopic_is_covered(real_bank):
    items, _ = real_bank
    tagged = {t for i in items for t in i.tags}
    missing = [s.tag for s in load_taxonomy().subtopics if s.tag not in tagged]
    assert missing == []
