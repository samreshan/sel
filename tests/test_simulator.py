import json
import math
import struct
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

from sel.events import EventBus
from sel.live import RuleAnalyzer
from sel.service import CallService
from sel.simulator import PcmSegmenter, RoomStateError, SimulatorHub

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000


def tone(seconds, amp=8000, freq=220):
    n = int(seconds * RATE)
    return struct.pack(f"<{n}h", *(int(amp * math.sin(2 * math.pi * freq * i / RATE)) for i in range(n)))


def silence(seconds):
    return b"\x00\x00" * int(seconds * RATE)


def rules_only(call, speakers):
    return None, RuleAnalyzer(), None


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeSpeech:
    def __init__(self):
        self.calls = []

    def transcribe_pcm(self, pcm, sample_rate=16000):
        self.calls.append(len(pcm))
        return [(0.1, len(pcm) / 2 / RATE, "I'll email you the quote tomorrow.", 0.9)]


class SegmenterTests(unittest.TestCase):
    def test_cuts_at_pauses_and_keeps_call_time(self):
        seg = PcmSegmenter()
        audio = silence(1.0) + tone(1.2) + silence(1.0) + tone(0.8) + silence(1.0)
        windows = []
        for i in range(0, len(audio), 8000 * 2):  # half-second chunks, as the browser sends them
            windows += seg.feed(audio[i:i + 16000], 5.0 + i / 2 / RATE)
        windows += seg.flush()
        self.assertEqual(len(windows), 2)
        (s1, w1), (s2, w2) = windows
        self.assertAlmostEqual(s1, 5.0 + 1.0 - 0.3, delta=0.05)  # 0.3 s of lead-in kept
        self.assertAlmostEqual(s2, 5.0 + 3.2 - 0.3, delta=0.05)
        self.assertAlmostEqual(len(w1) / 2 / RATE, 0.3 + 1.2 + 0.3, delta=0.08)

    def test_long_speech_is_capped_and_noise_is_dropped(self):
        seg = PcmSegmenter(max_len=4.0)
        out = seg.feed(tone(9.0), 0.0) + seg.flush()
        self.assertEqual(len(out), 3)
        self.assertTrue(all(len(w) / 2 / RATE <= 4.01 for _, w in out))
        self.assertEqual(PcmSegmenter().feed(tone(0.1) + silence(1.5), 0.0), [])  # a click

    def test_gap_in_the_stream_closes_the_utterance(self):
        seg = PcmSegmenter()
        first = seg.feed(tone(1.0), 0.0)
        later = seg.feed(silence(0.5), 10.0)  # chunks dropped in between
        self.assertEqual(first, [])
        self.assertEqual(len(later), 1)
        self.assertAlmostEqual(later[0][0], 0.0, delta=0.05)


class HubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bus = EventBus()
        self.service = CallService(self.tmp.name, ROOT / "packs", analysis_factory=rules_only, bus=self.bus)
        self.clock = Clock()
        self.speech = FakeSpeech()
        self.hub = SimulatorHub(self.service, speech_factory=lambda: self.speech, speech_status=lambda: (True, None),
                                ice_servers=[], clock=self.clock)
        self.addCleanup(self.hub.close)
        self.addCleanup(self.service.wait_idle)

    def room(self):
        room, rep = self.hub.create("demo-solar", "rep", "Asha", None, "+05:45")
        _, cust = self.hub.join(room.code, None, "Ramesh", "ramesh@example.com")
        return room, rep, cust

    def wait_for(self, cond, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.02)
        self.fail("timed out")

    def test_lobby_join_and_permissions(self):
        room, rep = self.hub.create("demo-solar", "rep", "Asha", None, "+05:45")
        self.assertEqual([r["code"] for r in self.hub.list_open()], [room.code])
        with self.assertRaises(RoomStateError):
            self.hub.start(room.code, rep)  # nobody to talk to yet
        _, cust = self.hub.join(room.code, None, "Ramesh", None)
        self.assertEqual(self.hub.role_of(room.code, cust), "customer")
        self.assertEqual(self.hub.list_open(), [])  # full rooms leave the lobby
        with self.assertRaises(RoomStateError):
            self.hub.join(room.code, None, "Third", None)
        with self.assertRaises(PermissionError):
            self.hub.add_line(room.code, "not-a-client", "hello")
        with self.assertRaises(ValueError):
            self.hub.create("demo-solar", "manager", "X", None, None)
        with self.assertRaises(KeyError):
            self.hub.create("no-such-pack", "rep", "X", None, None)
        person = self.service.people.add("Nima Gurung", "rep")
        room2, token = self.hub.create("demo-solar", "rep", person["name"], None, "+05:45", person_id=person["id"])
        shown = self.hub.view(room2, you=token, lines=False)["participants"]
        self.assertEqual(shown[0]["person_id"], person["id"])

    def test_signals_reach_only_the_other_side(self):
        room, rep, cust = self.room()
        to_cust = self.bus.subscribe({f"room:{room.code}"}, client=cust)
        to_rep = self.bus.subscribe({f"room:{room.code}"}, client=rep)
        self.hub.signal(room.code, rep, {"type": "offer", "sdp": "v=0"})
        ev = to_cust.get(timeout=1)
        while ev.type != "signal":
            ev = to_cust.get(timeout=1)
        self.assertEqual(ev.data, {"from": "rep", "data": {"type": "offer", "sdp": "v=0"}})
        self.assertTrue(all(e.type != "signal" for e in iter_events(to_rep)))

    def test_lines_are_scored_live_and_the_call_is_filed(self):
        room, rep, cust = self.room()
        self.hub.start(room.code, rep)
        with self.assertRaises(RoomStateError):
            self.hub.start(room.code, rep)
        self.assertTrue(room.started_at.endswith("+05:45"))
        script = json.loads((ROOT / "examples/calls/solar-01/transcript.json").read_text(encoding="utf-8"))["segments"]
        for s in script:
            self.clock.t = 100.0 + s["end"]
            self.hub.add_line(room.code, rep if s["speaker"] == "S1" else cust, s["text"], s["start"], s["end"])
        live = room.live
        self.assertEqual(len(room.lines), len(script))
        self.assertIn("objection", [a["kind"] for a in live["actions"]])
        self.assertIsInstance(live["score"]["overall"], int)
        self.assertEqual({e["kind"] for e in live["events"]} >= {"question", "objection", "claim", "commitment"}, True)

        with self.assertRaises(PermissionError):
            self.hub.finish(room.code, cust, b"webm", "recording.webm")  # only the rep records
        view = self.hub.finish(room.code, rep, b"\x1aE\xdf\xa3fake-webm", "recording.webm")
        self.assertEqual(view["state"], "saved")
        with self.assertRaises(RoomStateError):
            self.hub.add_line(room.code, rep, "too late")
        call_id = view["call_id"]
        self.wait_for(lambda: self.service.view(call_id)["status"] in ("done", "error"))
        call = self.service.view(call_id)
        self.assertEqual(call["status"], "done", call.get("error"))
        self.assertEqual((call["source"], call["audio_file"], call["transcribe"]), ("simulator", "audio.webm", "live"))
        self.assertEqual(call["report"]["meta"]["analyzer"], "rules (no language model)")
        self.assertEqual(len(call["report"]["transcript"]["segments"]), len(script))
        self.assertEqual(call["live_score"]["final"], True)
        drafts = {d["kind"]: d for d in call["report"]["drafts"]}
        self.assertEqual(drafts["email"]["fields"]["to"], "ramesh@example.com")
        # "bholi beluka samma" said on the call: the day after the call, in the room's time zone.
        call_day = date.fromisoformat(room.started_at[:10])
        self.assertEqual(drafts["email"]["fields"]["send_by"], (call_day + timedelta(days=1)).isoformat())
        self.assertIn("score", call["report"])

    def test_speech_windows_become_lines_before_filing(self):
        room, rep, cust = self.room()
        self.hub.start(room.code, rep)
        pcm = silence(0.5) + tone(1.0) + silence(1.0)
        for i in range(0, len(pcm), 16000):
            self.hub.add_audio(room.code, cust, pcm[i:i + 16000], 2.0 + i / 2 / RATE)
        # An utterance still in progress at hang-up is flushed and transcribed before the call is filed.
        self.hub.add_audio(room.code, rep, tone(0.8), 6.0)
        view = self.hub.finish(room.code, rep, None, None)
        lines = sorted((x["role"], x["source"]) for x in room.lines)
        self.assertEqual(lines, [("customer", "speech"), ("rep", "speech")])
        self.assertAlmostEqual(min(x["start"] for x in room.lines), 2.0 + 0.5 - 0.3 + 0.1, delta=0.1)
        self.wait_for(lambda: self.service.view(view["call_id"])["status"] == "done")
        self.assertIsNone(self.service.view(view["call_id"])["audio_file"])

    def test_leaving_before_the_start_reopens_the_room(self):
        room, rep, cust = self.room()
        self.hub.leave(room.code, cust)
        self.assertEqual(room.state, "waiting")
        self.assertEqual([r["code"] for r in self.hub.list_open()], [room.code])
        self.hub.leave(room.code, rep)
        self.assertNotIn(room.code, self.hub.rooms)


def iter_events(sub):
    import queue
    while True:
        try:
            ev = sub.get(timeout=0.05)
        except queue.Empty:
            return
        if ev is None:
            return
        yield ev


class ConcurrencyTests(unittest.TestCase):
    def test_many_lines_from_two_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = CallService(tmp, ROOT / "packs", analysis_factory=rules_only)
            hub = SimulatorHub(service, speech_status=lambda: (False, "off"))
            room, rep = hub.create("demo-pos", "rep", "Bikash", None, "+05:45")
            _, cust = hub.join(room.code, None, "Mina", None)
            hub.start(room.code, rep)

            def talk(client, word):
                for i in range(40):
                    hub.add_line(room.code, client, f"{word} {i}?", float(i), float(i) + 0.5)

            threads = [threading.Thread(target=talk, args=(c, w)) for c, w in ((rep, "rep"), (cust, "customer"))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(room.lines), 80)
            self.assertEqual(len({x["id"] for x in room.lines}), 80)
            self.assertEqual([x["start"] for x in room.lines], sorted(x["start"] for x in room.lines))
            with self.assertRaises(RoomStateError):
                hub.add_audio(room.code, rep, silence(0.5), 1.0)  # speech is off on this server


if __name__ == "__main__":
    unittest.main()


class LateAudioTests(unittest.TestCase):
    def test_the_last_chunk_after_hang_up_is_dropped_not_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = CallService(tmp, ROOT / "packs")
            hub = SimulatorHub(service, speech_factory=lambda: None, speech_status=lambda: (True, None))
            room, rep = hub.create("demo-solar", "rep", "Asha", None, "+05:45")
            with self.assertRaises(RoomStateError):  # before the call starts: refused
                hub.add_audio(room.code, rep, b"\0\0" * 800, 0.0)
            room.state = "saving"  # hang-up happened; the flushed chunk arrives late
            self.assertEqual(hub.add_audio(room.code, rep, b"\0\0" * 800, 9.0), 0)
            hub.close()
