"""lib/neon/replay.py — the cell content a daemon write leaves behind."""
from neon import replay


def test_apply_mirrors_the_daemon_setters():
    assert replay.apply("='0n'!AZ306", "append", "+25") == "='0n'!AZ306+25"
    assert replay.apply("", "append", "+1") == "=1"
    assert replay.apply("0", "append", "+119") == "=119"
    assert replay.apply("2", "append", "+2") == "=2+2"            # bare number → formula (2026-07-14)
    assert replay.apply("", "append", ", 2 tacos") == "2 tacos"
    assert replay.apply("eggs", "append", ", toast") == "eggs, toast"
    assert replay.apply("=IF(A1,20,0)", "write", "15") == "15"


def test_same_tolerates_only_what_excel_normalises():
    assert replay.same("1", "1.0") and replay.same(" =1+2 ", "=1+2")
    assert replay.same("='0n'!BA314+10+15.0", "='0n'!BA314+10+15")
    assert not replay.same("=1+1.05", "=1+1.5") and not replay.same("=1+15", "=1+16")


def test_add_arithmetic():
    assert replay.is_add("30", "15", "45") and not replay.is_add("", "15", "15")
    assert replay.added("30", 15, "45") and replay.added("", 15, "15") and replay.added("0", 15, "15.0")
    assert not replay.added("30", 15, "52") and not replay.added("30", "x", "45")
