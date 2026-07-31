import video_generator as vg


def test_compile_error_signature_ignores_unstable_traceback_context():
    first = "Traceback line 10\nIndentationError: unexpected indent"
    second = "Traceback line 99\nIndentationError: unexpected indent"

    assert vg._compile_error_signature(first) == vg._compile_error_signature(second)


def test_compile_error_signature_distinguishes_different_failures():
    indentation = "IndentationError: unexpected indent"
    missing_name = "NameError: name 'CYAN' is not defined"

    assert vg._compile_error_signature(indentation) != vg._compile_error_signature(missing_name)


def test_timeout_never_degrades_to_static_fallback_card():
    assert vg._should_use_fallback_card("Timeout: compilation exceeded 120 seconds") is False


def test_non_timeout_compile_failure_can_use_emergency_fallback():
    assert vg._should_use_fallback_card("NameError: name 'Circle' is not defined") is True
