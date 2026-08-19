"""Property-based checks for hostile stream and protocol input."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from tests._loader import protocol, server
from tests.helpers import firmware_frame, status_frame


@given(st.binary(max_size=512))
@settings(max_examples=500, deadline=None)
def test_arbitrary_protocol_input_has_only_documented_outcomes(data: bytes) -> None:
    """Malformed network input must never escape as indexing/struct errors."""
    try:
        result = protocol.parse_message(data)
    except protocol.ProtocolError:
        return
    assert result is None or isinstance(
        result,
        (protocol.DeviceStatus, protocol.FirmwareInfo, protocol.DeviceSetup),
    )


@given(st.binary(max_size=512))
@settings(max_examples=500, deadline=None)
def test_decoder_remains_bounded_for_arbitrary_bytes(data: bytes) -> None:
    """Junk and fake headers cannot make decoder state exceed its limit."""
    decoder = server.FrameDecoder(max_buffer_size=512)
    decoder.feed(data)
    for _ in range(len(data) + 1):
        if decoder.pop_frame(flush_legacy=True) is None:
            break
    else:  # pragma: no cover - defensive assertion for the loop bound itself
        raise AssertionError("decoder did not converge")
    assert len(decoder) <= 512
    assert decoder.discarded_bytes <= len(data)


@given(
    status_length=st.sampled_from((19, 21)),
    chunk_sizes=st.lists(
        st.integers(min_value=1, max_value=23), min_size=1, max_size=20
    ),
)
@settings(max_examples=300, deadline=None)
def test_valid_stream_survives_every_generated_chunking(
    status_length: int, chunk_sizes: list[int]
) -> None:
    """TCP packet boundaries must not alter the two decoded frames."""
    payload = firmware_frame() + status_frame(length=status_length)
    decoder = server.FrameDecoder()
    decoded: list[bytes] = []
    offset = 0
    for size in chunk_sizes:
        if offset >= len(payload):
            break
        decoder.feed(payload[offset : offset + size])
        offset += size
        while frame := decoder.pop_frame():
            decoded.append(frame)
    if offset < len(payload):
        decoder.feed(payload[offset:])
    while frame := decoder.pop_frame(flush_legacy=True):
        decoded.append(frame)
    assert decoded == [firmware_frame(), status_frame(length=status_length)]


@given(st.binary(min_size=6, max_size=6))
def test_every_six_byte_identifier_round_trips(raw: bytes) -> None:
    """All binary identifiers have one unambiguous canonical representation."""
    canonical = raw.hex()
    assert protocol.normalize_serial(canonical.upper()) == canonical
    assert (
        protocol.normalize_serial(
            ":".join(canonical[index : index + 2] for index in range(0, 12, 2))
        )
        == canonical
    )
