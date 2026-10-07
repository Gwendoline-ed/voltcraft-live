# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Voltcraft Live contributors
#
# This file is part of Voltcraft Live.
# Voltcraft Live is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Voltcraft Live is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Voltcraft Live. If not, see <https://www.gnu.org/licenses/>.

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('voltcraft_live', ROOT / 'voltcraft_live.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
fixture_head = (b'1000000000000000002.5e-3172.5e-3172.5e-3172.5e-317'
                b'10001.000e+09000001+0.00e+00+0.00e+00000000').ljust(99, b'\0')
fixture_frame = fixture_head + bytes([1, 2, 3, 4, 5])*800
fixture_packets = [b'#9000000128000004099000000000'+fixture_frame[:99],
                   b'#9000004029000004099000000099'+fixture_frame[99:]]


def continuation(total, offset, body):
    return f'#9{len(body)+29:09d}{total:09d}{offset:09d}'.encode() + body


def new_packets(frame, lengths):
    header = b'#9000000128' + frame[:2] + f'{len(frame):09d}000000000'.encode() + frame[2:99]
    packets = [header]
    offset = 99
    for length in lengths:
        packets.append(continuation(len(frame), offset, frame[offset:offset+length]))
        offset += length
    assert offset == len(frame)
    return packets


def query_from(packets, mode):
    items = iter(packets)
    def query(command):
        assert command == v.COMMANDS[mode], 'Every packet needs a fresh query'
        return next(items)
    return query


class Protocol(unittest.TestCase):
    def test_public_waveform_packet_layout(self):
        diagnostic = {}
        frame = v.receive_frame(query_from(fixture_packets, 'legacy'), 'legacy', diagnostic)
        self.assertEqual(frame, fixture_frame)
        view = v.view_frame(frame)
        self.assertEqual(view['count_per_channel'], 4000)
        self.assertEqual(view['dt'], 1e-9)
        self.assertEqual(view['enabled'], [1])
        self.assertEqual(view['channels'][0]['minimum'], 1)
        self.assertEqual(view['channels'][0]['maximum'], 5)

    def test_changed_initial_header_and_channel_order(self):
        head = (b'1000000000000000005.0e+005.0e+005.0e+005.0e+00'
                b'11011.000e+06000002+0.00e+00+0.00e+00000000').ljust(99, b'\0')
        frame = head + bytes([255])*32 + bytes([64])*32 + bytes([128])*32
        diagnostic = {}
        actual = v.receive_frame(query_from(new_packets(frame, [32, 32, 32]), 'display'), 'display', diagnostic)
        self.assertEqual(actual, frame)
        view = v.view_frame(actual)
        self.assertEqual(view['enabled'], [1, 2, 4])
        self.assertEqual([c['minimum'] for c in view['channels']], [-1, 64, -128])
        self.assertEqual(view['dt'], 2e-6)
        self.assertTrue(diagnostic['capture_complete'])

    def test_pending_transfer_is_finished_before_new_capture(self):
        diagnostic = {}
        frame = v.receive_frame(query_from([fixture_packets[1]]+fixture_packets, 'legacy'), 'legacy', diagnostic)
        self.assertEqual(frame, fixture_frame)
        self.assertEqual(diagnostic['resynchronization_count'], 1)

    def test_truncated_packet_is_rejected(self):
        diagnostic = {}
        with self.assertRaisesRegex(ValueError, 'unvollständig'):
            v.receive_frame(query_from([fixture_packets[0], fixture_packets[1][:-8]], 'legacy'), 'legacy', diagnostic)
        self.assertFalse(diagnostic.get('capture_complete', False))
        self.assertEqual(len(diagnostic['streams'][0]['packets']), 2)

    def test_out_of_order_packet_is_rejected(self):
        p = fixture_packets[1]
        wrong = p[:20] + b'000000100' + p[29:]
        with self.assertRaisesRegex(ValueError, 'fehlen'):
            v.receive_frame(query_from([fixture_packets[0], wrong], 'legacy'), 'legacy', {})

    def test_no_channels_and_unknown_format_are_rejected(self):
        with self.assertRaisesRegex(v.NoMeasurement, 'momentan keine Messdaten'):
            v.packet_parts(b'#9000000000\n', 'display')
        with self.assertRaisesRegex(ValueError, 'Unbekannter'):
            v.view_frame(b'\0'*99+b'\0')

    def test_empty_response_switches_to_public_waveform_query(self):
        diagnostic = {}
        packets = iter(fixture_packets)
        calls = []
        def query(command):
            calls.append(command)
            if command == v.COMMANDS['display']:
                return bytes.fromhex('2339303030303030303030')
            return next(packets)
        frame, mode = v.receive_auto(query, 'display', diagnostic, True)
        self.assertEqual(mode, 'legacy')
        self.assertEqual(frame, fixture_frame)
        self.assertEqual(calls, [v.COMMANDS['display'], v.COMMANDS['legacy'], v.COMMANDS['legacy']])
        self.assertEqual(diagnostic['display_attempt']['streams'][0]['packets'][0]['hex'], '2339303030303030303030')
        self.assertEqual(diagnostic['query'], v.COMMANDS['legacy'])

    def test_explicit_display_selection_and_corruption_do_not_fall_back(self):
        with self.assertRaises(v.NoMeasurement):
            v.receive_auto(lambda _: b'#9000000000', 'display', {}, False)
        with self.assertRaises(ValueError):
            v.receive_auto(lambda _: b'garbled', 'display', {}, True)

    def test_supported_large_record_keeps_samples_for_mouse_zoom(self):
        data = bytes(range(256))*250
        view = v.view_frame(fixture_frame[:99]+data)
        self.assertEqual(len(view['channels'][0]['points']), 64000)
        self.assertEqual(view['channels'][0]['points'][10001], (10001, 17))

    def test_large_waveform_keeps_narrow_peaks(self):
        data = bytearray(128000)
        data[8021], data[12087] = 127, 128
        frame = fixture_frame[:99] + data
        view = v.view_frame(frame)
        self.assertIn((8021, 127), view['channels'][0]['points'])
        self.assertIn((12087, -128), view['channels'][0]['points'])
        self.assertLess(len(view['channels'][0]['points']), 6000)


if __name__ == '__main__':
    unittest.main()
