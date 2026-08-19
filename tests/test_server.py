"""Tests for stream framing and server trust boundaries."""

from __future__ import annotations

import asyncio
import time
import unittest

import pytest

from tests._loader import protocol, server
from tests.helpers import (
    ChunkReader,
    FakeWriter,
    WaitingReader,
    firmware_frame,
    status_frame,
)

SERIAL = "aabbccddeeff"
OTHER_SERIAL = "112233445566"
FIRMWARE_PROFILE = "0.0.28|0.1.22|2.1.0.0"


def device_payload(serial: str = SERIAL, *, status_length: int = 21) -> bytes:
    """Return the required firmware handshake followed by one status frame."""
    return firmware_frame(serial) + status_frame(serial, length=status_length)


def connected_state(
    instance: server.AmbientikaServer,
    serial: str = SERIAL,
    writer: FakeWriter | None = None,
) -> tuple[server._ConnectionState, FakeWriter]:
    """Register a test connection with an observed firmware handshake."""
    writer = writer or FakeWriter()
    state = server._ConnectionState(writer.ip, server.redact_ip(writer.ip), writer)
    instance._register(serial, state)
    state.firmware = protocol.parse_message(firmware_frame(serial))
    return state, writer


class FrameDecoderTests(unittest.TestCase):
    def test_reassembles_split_current_frame(self) -> None:
        decoder = server.FrameDecoder()
        frame = status_frame()
        decoder.feed(frame[:10])
        self.assertIsNone(decoder.pop_frame())
        decoder.feed(frame[10:])
        self.assertEqual(decoder.pop_frame(), frame)

    def test_reassembles_split_legacy_frame_on_flush(self) -> None:
        decoder = server.FrameDecoder()
        frame = status_frame(length=19)
        decoder.feed(frame[:10])
        self.assertIsNone(decoder.pop_frame())
        decoder.feed(frame[10:])
        self.assertTrue(decoder.needs_frame_grace)
        self.assertIsNone(decoder.pop_frame())
        self.assertEqual(decoder.pop_frame(flush_legacy=True), frame)

    def test_decodes_two_coalesced_current_frames(self) -> None:
        decoder = server.FrameDecoder()
        first = status_frame(SERIAL)
        second = status_frame(OTHER_SERIAL)
        decoder.feed(first + second)
        self.assertEqual(decoder.pop_frame(), first)
        self.assertEqual(decoder.pop_frame(), second)

    def test_decodes_two_coalesced_legacy_frames(self) -> None:
        decoder = server.FrameDecoder()
        first = status_frame(SERIAL, length=19)
        second = status_frame(OTHER_SERIAL, length=19)
        decoder.feed(first + second)
        self.assertEqual(decoder.pop_frame(), first)
        self.assertEqual(decoder.pop_frame(), second)

    def test_resynchronises_after_junk(self) -> None:
        decoder = server.FrameDecoder()
        frame = firmware_frame()
        decoder.feed(b"\xff\x00junk" + frame)
        self.assertEqual(decoder.pop_frame(), frame)
        self.assertEqual(decoder.discarded_bytes, 6)

    def test_enforces_buffer_limit(self) -> None:
        decoder = server.FrameDecoder(max_buffer_size=21)
        with self.assertRaises(BufferError):
            decoder.feed(b"x" * 22)


@pytest.mark.usefixtures("socket_enabled")
class ServerPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_allowlisted_device_from_bound_ip(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices="aa:bb:cc:dd:ee:ff",
            device_ip_bindings=f"{SERIAL}=192.0.2.10",
        )
        statuses = []
        instance.on_status(statuses.append)
        writer = FakeWriter("192.0.2.10")

        await instance._handle_connection(ChunkReader(device_payload()), writer)

        self.assertEqual(len(statuses), 1)
        self.assertTrue(writer.closed)
        self.assertEqual(instance.connected_devices, [])

    async def test_rejects_unknown_device_and_wrong_bound_ip(self) -> None:
        unknown = server.AmbientikaServer(allowed_devices=SERIAL)
        unknown_statuses = []
        unknown.on_status(unknown_statuses.append)
        await unknown._handle_connection(
            ChunkReader(device_payload(OTHER_SERIAL)), FakeWriter()
        )
        self.assertEqual(unknown_statuses, [])

        wrong_ip = server.AmbientikaServer(device_ip_bindings=f"{SERIAL}=192.0.2.99")
        wrong_statuses = []
        wrong_ip.on_status(wrong_statuses.append)
        await wrong_ip._handle_connection(
            ChunkReader(device_payload()), FakeWriter("192.0.2.10")
        )
        self.assertEqual(wrong_statuses, [])

    async def test_enrollment_records_candidate_without_admitting_it(self) -> None:
        instance = server.AmbientikaServer(enrollment_expires_at=time.time() + 30)
        statuses = []
        connections = []
        instance.on_status(statuses.append)
        instance.on_connect(connections.append)

        await instance._handle_connection(
            ChunkReader(device_payload()), FakeWriter("192.0.2.10")
        )

        candidate = instance.enrollment_candidates[SERIAL]
        self.assertEqual(candidate.firmware, FIRMWARE_PROFILE)
        self.assertEqual(statuses, [])
        self.assertEqual(connections, [])
        self.assertEqual(instance.connected_devices, [])

    async def test_enrollment_candidate_limit_is_bounded(self) -> None:
        instance = server.AmbientikaServer(
            enrollment_expires_at=time.time() + 30,
            max_enrollment_candidates=1,
        )
        await instance._handle_connection(
            ChunkReader(firmware_frame(SERIAL)), FakeWriter("192.0.2.10")
        )
        await instance._handle_connection(
            ChunkReader(firmware_frame(OTHER_SERIAL)), FakeWriter("192.0.2.11")
        )
        self.assertEqual(set(instance.enrollment_candidates), {SERIAL})

    async def test_one_socket_cannot_claim_two_devices(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=f"{SERIAL},{OTHER_SERIAL}")
        statuses = []
        instance.on_status(statuses.append)
        payload = device_payload(SERIAL) + firmware_frame(OTHER_SERIAL)

        await instance._handle_connection(ChunkReader(payload), FakeWriter())

        self.assertEqual([item.serial_number for item in statuses], [SERIAL])
        self.assertEqual(instance.connected_devices, [])

    async def test_active_device_identity_cannot_be_hijacked(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=SERIAL)
        first = server._ConnectionState("192.0.2.10", "peer-a", FakeWriter())
        second = server._ConnectionState("192.0.2.11", "peer-b", FakeWriter())
        instance._register(SERIAL, first)

        with self.assertRaises(server.ConnectionRejected):
            instance._register(SERIAL, second)

        self.assertIs(instance._connections[SERIAL], first)
        instance._unregister(first)

    async def test_reassembles_split_frame_inside_connection(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=SERIAL)
        statuses = []
        instance.on_status(statuses.append)
        frame = status_frame()

        await instance._handle_connection(
            ChunkReader(firmware_frame(), frame[:10], frame[10:]), FakeWriter()
        )

        self.assertEqual(len(statuses), 1)

    async def test_processes_legacy_frame_at_eof(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=SERIAL)
        statuses = []
        instance.on_status(statuses.append)

        await instance._handle_connection(
            ChunkReader(device_payload(status_length=19)), FakeWriter()
        )

        self.assertEqual(len(statuses), 1)

    async def test_status_before_firmware_is_rejected_by_default(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=SERIAL)
        statuses = []
        connections = []
        instance.on_status(statuses.append)
        instance.on_connect(connections.append)

        await instance._handle_connection(ChunkReader(status_frame()), FakeWriter())

        self.assertEqual(statuses, [])
        self.assertEqual(connections, [])

    async def test_firmware_requirement_can_be_disabled_for_legacy_devices(
        self,
    ) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL, require_firmware=False
        )
        statuses = []
        instance.on_status(statuses.append)

        await instance._handle_connection(ChunkReader(status_frame()), FakeWriter())

        self.assertEqual(len(statuses), 1)

    async def test_firmware_identity_cannot_change_on_socket(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=SERIAL)
        changed = bytearray(firmware_frame())
        changed[10] = 99

        await instance._handle_connection(
            ChunkReader(firmware_frame() + bytes(changed)), FakeWriter()
        )

        diagnostics = instance.security_diagnostics
        self.assertEqual(diagnostics["counters"]["firmware_frames_accepted"], 1)

    async def test_malformed_frame_does_not_kill_following_valid_frame(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=SERIAL)
        statuses = []
        instance.on_status(statuses.append)
        malformed = bytearray(status_frame())
        malformed[8] = 255

        await instance._handle_connection(
            ChunkReader(firmware_frame() + bytes(malformed) + status_frame()),
            FakeWriter(),
        )

        self.assertEqual(len(statuses), 1)

    async def test_excessive_junk_before_valid_frame_is_rejected(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL, max_discarded_bytes=8
        )
        statuses = []
        instance.on_status(statuses.append)

        await instance._handle_connection(
            ChunkReader(b"x" * 9 + device_payload()), FakeWriter()
        )

        self.assertEqual(statuses, [])

    async def test_rate_limit_stops_frame_flood(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL, max_frames_per_second=2
        )
        statuses = []
        instance.on_status(statuses.append)

        await instance._handle_connection(
            ChunkReader(firmware_frame() + status_frame() + status_frame()),
            FakeWriter(),
        )

        self.assertEqual(len(statuses), 1)

    async def test_connection_limit_rejects_new_socket(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL,
            max_connections=1,
            max_connections_per_ip=1,
            max_unidentified_connections=1,
        )
        occupied = FakeWriter("192.0.2.1")
        instance._active_writers.add(occupied)
        rejected = FakeWriter("192.0.2.2")

        await instance._handle_connection(ChunkReader(device_payload()), rejected)

        self.assertTrue(rejected.closed)
        self.assertEqual(instance.connected_devices, [])

    async def test_connection_attempt_rate_is_limited_per_ip(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL,
            max_connection_attempts_per_minute=1,
        )
        first = server._ConnectionState("192.0.2.10", "peer", FakeWriter())
        second = server._ConnectionState("192.0.2.10", "peer", FakeWriter())
        instance._admit_connection_attempt(first)
        with self.assertRaises(server.ConnectionRejected):
            instance._admit_connection_attempt(second)

    async def test_writer_aware_cleanup_cannot_remove_replacement(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=SERIAL)
        first_writer = FakeWriter()
        first = server._ConnectionState("192.0.2.10", "peer", first_writer)
        instance._register(SERIAL, first)
        first_writer.close()

        second_writer = FakeWriter()
        second = server._ConnectionState("192.0.2.10", "peer", second_writer)
        instance._register(SERIAL, second)
        instance._unregister(first)

        self.assertIs(instance._connections[SERIAL], second)
        instance._unregister(second)

    async def test_command_device_identifier_must_match_target(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=SERIAL)
        state, writer = connected_state(instance)
        wrong_command = protocol.build_filter_reset(OTHER_SERIAL)

        self.assertFalse(await instance.send_command(SERIAL, wrong_command))
        self.assertEqual(writer.writes, [])
        instance._unregister(state)

    async def test_malformed_command_is_not_written(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=SERIAL)
        state, writer = connected_state(instance)

        self.assertFalse(await instance.send_command(SERIAL, b"not-a-frame"))
        self.assertEqual(writer.writes, [])
        instance._unregister(state)

    async def test_commands_are_read_only_until_firmware_approval(self) -> None:
        instance = server.AmbientikaServer(allowed_devices=SERIAL)
        state, writer = connected_state(instance)

        self.assertFalse(
            await instance.send_command(SERIAL, protocol.build_filter_reset(SERIAL))
        )
        self.assertEqual(writer.writes, [])
        instance._unregister(state)

    async def test_approved_matching_firmware_can_receive_command(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL,
            approved_firmware=f"{SERIAL}={FIRMWARE_PROFILE}",
        )
        state, writer = connected_state(instance)
        command = protocol.build_filter_reset(SERIAL)

        self.assertTrue(await instance.send_command(SERIAL, command))
        self.assertEqual(writer.writes, [command])
        instance._unregister(state)

    async def test_changed_firmware_disables_commands(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL,
            approved_firmware=f"{SERIAL}=9.9.9|9.9.9|9.9.9.9",
        )
        state, writer = connected_state(instance)

        self.assertFalse(
            await instance.send_command(SERIAL, protocol.build_filter_reset(SERIAL))
        )
        self.assertEqual(writer.writes, [])
        instance._unregister(state)

    async def test_command_rate_limit_stops_burst(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL,
            approved_firmware=f"{SERIAL}={FIRMWARE_PROFILE}",
            max_commands_per_minute=1,
        )
        state, writer = connected_state(instance)
        command = protocol.build_filter_reset(SERIAL)

        self.assertTrue(await instance.send_command(SERIAL, command))
        self.assertFalse(await instance.send_command(SERIAL, command))
        self.assertEqual(writer.writes, [command])
        instance._unregister(state)

    async def test_duplicate_mode_command_is_coalesced(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL,
            approved_firmware=f"{SERIAL}={FIRMWARE_PROFILE}",
        )
        state, writer = connected_state(instance)
        command = protocol.build_mode_command(
            SERIAL,
            protocol.OperatingMode.AUTO,
            protocol.FanSpeed.LOW,
            protocol.HumidityLevel.NORMAL,
            protocol.LightSensitivity.LOW,
        )

        self.assertTrue(await instance.send_command(SERIAL, command))
        self.assertTrue(await instance.send_command(SERIAL, command))
        self.assertEqual(writer.writes, [command])
        instance._unregister(state)

    async def test_first_frame_timeout_closes_silent_socket(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL, first_frame_timeout=0.01
        )
        writer = FakeWriter()

        await instance._handle_connection(WaitingReader(), writer)

        self.assertTrue(writer.closed)
        self.assertEqual(instance.connected_devices, [])

    async def test_forced_current_format_rejects_short_partial_frame(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL,
            status_frame_length="21",
            first_frame_timeout=0.01,
            frame_assembly_timeout=0.01,
        )
        reader = asyncio.StreamReader()
        reader.feed_data(firmware_frame() + status_frame(length=19))
        writer = FakeWriter()

        await asyncio.wait_for(instance._handle_connection(reader, writer), timeout=0.1)

        self.assertTrue(writer.closed)
        self.assertEqual(instance.connected_devices, [])

    async def test_no_policy_keeps_listener_closed(self) -> None:
        instance = server.AmbientikaServer(port=0, host="127.0.0.1")
        await instance.start()
        self.assertEqual(instance.bound_ports, [])
        self.assertTrue(instance.security_diagnostics["listener"]["fail_closed"])
        await instance.stop()

    async def test_approved_policy_opens_only_primary_listener(self) -> None:
        instance = server.AmbientikaServer(
            port=0,
            host="127.0.0.1",
            allowed_devices=SERIAL,
        )
        await instance.start()
        try:
            self.assertEqual(len(instance.bound_ports), 1)
            self.assertNotIn(4521, instance.bound_ports)
        finally:
            await instance.stop()

    async def test_enrollment_only_listener_closes_at_deadline(self) -> None:
        instance = server.AmbientikaServer(
            port=0,
            host="127.0.0.1",
            enrollment_expires_at=time.time() + 0.03,
        )
        await instance.start()
        self.assertEqual(len(instance.bound_ports), 1)
        await asyncio.sleep(0.06)
        self.assertEqual(instance.bound_ports, [])
        await instance.stop()

    async def test_real_socket_reassembles_frame_and_stops_cleanly(self) -> None:
        instance = server.AmbientikaServer(
            port=0, host="127.0.0.1", allowed_devices=SERIAL
        )
        statuses = []
        received = asyncio.Event()

        def _record_status(status) -> None:
            statuses.append(status)
            received.set()

        instance.on_status(_record_status)
        await instance.start()
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", instance.bound_ports[0]
        )
        frame = status_frame()
        writer.write(firmware_frame() + frame[:7])
        await writer.drain()
        writer.write(frame[7:])
        await writer.drain()

        await asyncio.wait_for(received.wait(), timeout=1)
        self.assertEqual(len(statuses), 1)

        await instance.stop()
        self.assertEqual(await asyncio.wait_for(reader.read(), timeout=1), b"")
        self.assertEqual(instance.connected_devices, [])
        writer.close()
        await writer.wait_closed()

    async def test_diagnostics_do_not_disclose_identifiers_or_exact_ips(self) -> None:
        instance = server.AmbientikaServer(
            allowed_devices=SERIAL,
            device_ip_bindings=f"{SERIAL}=192.0.2.10",
            enrollment_expires_at=time.time() + 30,
        )
        await instance._handle_connection(
            ChunkReader(firmware_frame(OTHER_SERIAL)), FakeWriter("192.0.2.44")
        )

        diagnostics = repr(instance.security_diagnostics)
        self.assertNotIn(SERIAL, diagnostics)
        self.assertNotIn(OTHER_SERIAL, diagnostics)
        self.assertNotIn("192.0.2.10", diagnostics)
        self.assertNotIn("192.0.2.44", diagnostics)
        self.assertIn("********eeff", diagnostics)
        self.assertIn("192.0.2.x", diagnostics)


class PolicyParserTests(unittest.TestCase):
    def test_firmware_approvals_are_canonical_and_strict(self) -> None:
        parsed = server.parse_approved_firmware(f"AA:BB:CC:DD:EE:FF={FIRMWARE_PROFILE}")
        self.assertEqual(parsed, {SERIAL: FIRMWARE_PROFILE})
        with self.assertRaises(ValueError):
            server.parse_approved_firmware(f"{SERIAL}=not-a-profile")

    def test_ip_bindings_canonicalise_address_and_identifier(self) -> None:
        parsed = server.parse_device_ip_bindings(
            "AA-BB-CC-DD-EE-FF=2001:0db8:0:0:0:0:0:1"
        )
        self.assertEqual(parsed, {SERIAL: "2001:db8::1"})


if __name__ == "__main__":
    unittest.main()
