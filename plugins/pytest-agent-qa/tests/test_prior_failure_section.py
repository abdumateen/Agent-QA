"""Terminal block shown when a previously diagnosed failure repeats."""

from pytest_agent_qa.plugin import _prior_failure_section


def test_no_section_without_a_real_diagnosis():
    assert _prior_failure_section([]) is None
    assert (
        _prior_failure_section(
            [
                {
                    "root_cause": "Unclassified test failure",
                    "fix": "Fix not recorded",
                }
            ]
        )
        is None
    )


def test_section_reports_the_diagnosis_and_repeat_count():
    section = _prior_failure_section(
        [
            {
                "root_cause": "Off-by-one in the page limit",
                "fix": "Use >=",
                "confidence": 1.0,
            },
            {"root_cause": "Unclassified test failure", "fix": "Fix not recorded"},
        ]
    )
    assert section is not None
    assert "PREVIOUS FAILURE MATCH" in section
    assert "Seen before (1x)" in section
    assert "Root Cause: Off-by-one in the page limit" in section
    assert "Suggested Fix: Use >=" in section
    assert "Confidence: 100%" in section
    assert section.endswith("=" * 42 + "\n")


def test_section_reports_a_partly_decayed_confidence():
    section = _prior_failure_section(
        [{"root_cause": "Off-by-one", "fix": "Use >=", "confidence": 0.6667}]
    )
    assert section is not None
    assert "Confidence: 67%" in section


def test_section_without_a_confidence_reports_none_measured():
    section = _prior_failure_section([{"root_cause": "Off-by-one", "fix": "Use >="}])
    assert section is not None
    assert "Confidence: 0%" in section
