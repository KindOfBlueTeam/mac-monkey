from mm_checks import build_payload


def test_payload_serializes():
    p = build_payload()
    d = p.to_dict()
    assert isinstance(d, dict)
    assert "sections" in d
    assert "overall" in d
