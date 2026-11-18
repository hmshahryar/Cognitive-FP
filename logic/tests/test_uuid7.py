from cognitive_mesh.db.base import uuid7


def test_uuid7_is_version_7_rfc_variant() -> None:
    u = uuid7()
    assert u.version == 7 and u.variant == "specified in RFC 4122"


def test_uuid7_strictly_increases_even_within_one_millisecond() -> None:
    ids = [uuid7() for _ in range(10_000)]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    # Same ordering when stored as hex text (how SQLite stores Uuid columns).
    hexes = [u.hex for u in ids]
    assert hexes == sorted(hexes)
