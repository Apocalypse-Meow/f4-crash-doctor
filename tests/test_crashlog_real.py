"""Pins call-stack parsing against a REAL crash log.

Every other crash-log fixture in this repository is `synthetic/` -- written to
exercise signatures written by the same author. That is circular in the way that
matters: it proves the matcher fires on logs built to make it fire, and it cannot
show what real logs look like.

Running 400 real logs through the parser found the gap these tests now pin. Some
Buffout builds emit disassembly *and* demangled function names in the probable
call stack:

    [16] 0x7FF6CC331432 Fallout4.exe+1B51432\tmov r14d, eax |  BSJobs::JobThread::RunJob(void)_1B51432 -> 194800+0x202

`_MODULE_OFFSET_RE` anchors on `$`, so that whole tail failed to match and fell
through to `module` -- carrying the disassembly and the function name with it,
and leaving `symbol` holding an address-library ID instead of a name. Measured
across the corpus: **649 of 9,748 frames** corrupted, and the names it hid are
the largest identifiable crash family in the data.

The fixture is that log's call stack only, with provenance and a PII check noted
in its header.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from f4_crash_doctor import crashlog

FIXTURE = Path(__file__).parent / "fixtures" / "real" / "crash-symbolicated-callstack.log"


@pytest.fixture(scope="module")
def parsed() -> dict:
    return crashlog.parse_crash_log(FIXTURE.read_text(encoding="utf-8"))


def test_the_real_log_parses_at_all(parsed):
    assert parsed["exception"]["type"] == "EXCEPTION_ACCESS_VIOLATION"
    assert len(parsed["call_stack"]) == 21


def test_module_is_the_module_not_the_whole_line(parsed):
    """The defect: `module` held 'Fallout4.exe+1B51432\\tmov r14d... | BSJobs::...'."""
    frame = parsed["call_stack"][16]
    assert frame["module"] == "Fallout4.exe"
    assert frame["offset"] == "1B51432"


def test_function_name_lands_in_symbol(parsed):
    """The name is the most useful evidence in the log; it must be reachable."""
    assert parsed["call_stack"][16]["symbol"] == "BSJobs::JobThread::RunJob"


def test_no_frame_leaks_disassembly_or_a_name_into_module(parsed):
    for f in parsed["call_stack"]:
        mod = str(f.get("module") or "")
        assert "::" not in mod, f"function name leaked into module: {mod[:60]}"
        assert "|" not in mod, f"disassembly leaked into module: {mod[:60]}"
        assert "\t" not in mod, f"tab leaked into module: {mod[:60]}"


def test_most_frames_recover_a_name(parsed):
    named = [f for f in parsed["call_stack"] if "::" in str(f.get("symbol") or "")]
    assert len(named) >= 18, "the whole point is that these names are now reachable"


def test_exception_module_is_clean_despite_a_disassembly_tail(parsed):
    """The exception line carries the same tail and must not absorb it."""
    assert parsed["exception"]["module"] == "Fallout4.exe"


def test_plain_frames_still_keep_the_address_library_id():
    """Regression: logs WITHOUT names put the `->` tail in symbol. Unchanged."""
    text = (
        'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x7FF6F34995BE '
        "Fallout4.exe+16B95BE\n"
        "PROBABLE CALL STACK:\n"
        "\t[ 0] 0x7FF6F34995BE Fallout4.exe+16B95BE -> 1242880+0x1FE\n"
    )
    frame = crashlog.parse_crash_log(text)["call_stack"][0]
    assert frame["module"] == "Fallout4.exe"
    assert frame["offset"] == "16B95BE"
    assert frame["symbol"] == "1242880+0x1FE"


def test_templated_class_names_are_not_dropped():
    """A real frame, verbatim from the corpus.

    Found by mutation testing: relaxing the "::" requirement changed nothing, so
    the constraint was checked against real data -- and it was silently losing
    **151 names**. The cause was templates, not the "::": a regex that demands
    "::" straight after a plain identifier stops at the "<" of
    `BSPointerHandleManagerInterface<Actor,HandleManager>` and then finds no
    "::" at all, so the whole name is dropped.

    That name appears on **59 frames** in 400 logs and is the known Fallout 4
    handle-exhaustion crash -- the second-largest family in the corpus, and it
    was invisible.
    """
    text = (
        'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x7FF7E6173948 '
        "Fallout4.exe+0023948\n"
        "PROBABLE CALL STACK:\n"
        "\t[  8] 0x7FF7E6173948 Fallout4.exe+0023948\tjmp 0x00007FF7E6173971 |  "
        "BSPointerHandleManagerInterface<Actor,HandleManager>::GetSmartPointer(x)_23948\n"
    )
    frame = crashlog.parse_crash_log(text)["call_stack"][0]
    assert frame["module"] == "Fallout4.exe"
    assert frame["symbol"] is not None, "a templated name must not be dropped"
    assert frame["symbol"].startswith("BSPointerHandleManagerInterface")
    assert "GetSmartPointer" in frame["symbol"]


def test_stray_single_letters_are_not_treated_as_names():
    """The 3-character floor: bare "E" and "F" appear after the pipe in real
    logs and are not function names."""
    text = (
        'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x7FF6F34995BE '
        "Fallout4.exe+16B95BE\n"
        "PROBABLE CALL STACK:\n"
        "\t[ 0] 0x7FF6F34995BE Fallout4.exe+16B95BE\tmov rax, rbx |  E -> 1242880+0x1FE\n"
    )
    frame = crashlog.parse_crash_log(text)["call_stack"][0]
    assert frame["symbol"] == "1242880+0x1FE", "a stray letter must not win over the ID"


def test_address_library_id_is_trimmed_of_a_trailing_disassembly_copy():
    """Some frames repeat the disassembly AFTER the `->` tail and carry no
    function name, so nothing later overwrites `symbol`:

        [00] 0x7FF73D9C76A6  Fallout4.exe+06A76A6 -> 1522464+0x46\\tcall [rax]

    Added after mutation testing: removing the trim passed all eight other
    tests. The shape was then confirmed real rather than imagined -- **23
    frames** across the 400-log corpus match it -- and this line is one of them.
    """
    text = (
        'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x7FF73D9C76A6 '
        "Fallout4.exe+06A76A6\n"
        "PROBABLE CALL STACK:\n"
        "\t[00] 0x7FF73D9C76A6      Fallout4.exe+06A76A6 -> 1522464+0x46\tcall [rax]\n"
    )
    frame = crashlog.parse_crash_log(text)["call_stack"][0]
    assert frame["module"] == "Fallout4.exe"
    assert frame["symbol"] == "1522464+0x46", "the disassembly copy must not ride along"


def test_frame_with_no_tail_at_all_still_parses():
    text = (
        'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x7FF6F34995BE '
        "Fallout4.exe+16B95BE\n"
        "PROBABLE CALL STACK:\n"
        "\t[ 0] 0x7FF6F34995BE Fallout4.exe+16B95BE\n"
    )
    frame = crashlog.parse_crash_log(text)["call_stack"][0]
    assert frame["module"] == "Fallout4.exe"
    assert frame["offset"] == "16B95BE"
    assert frame["symbol"] is None
