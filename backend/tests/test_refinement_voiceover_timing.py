from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from app.services.refinement_voiceover import align_voiceover_timing, normalize_provider_timing


def sentence(text="你好", start=0.1):
    return {"text":text,"words":[
        {"word":text[0],"startTime":start,"endTime":start+0.2},
        {"word":text[1],"startTime":start+0.2,"endTime":start+0.4},
    ]}


class ProviderTimingTest(unittest.TestCase):
    def test_two_sentences_keep_global_seconds_and_ignore_repeated_metadata(self):
        first, second = sentence(), sentence("测试",1.0)
        partial = deepcopy(first);partial["words"] = partial["words"][:1]
        values = [{"text":"你好","words":[]},partial,first,deepcopy(first),second]
        result = normalize_provider_timing(values,2000)
        self.assertEqual(result["segments"],[
            {"text":"你好","start":0.1,"end":0.5},
            {"text":"测试","start":1.0,"end":1.4},
        ])
        self.assertEqual(len(result["words"]),4)
        self.assertEqual(result["words"][2],{"text":"测","start":1.0,"end":1.2})

    def test_invalid_word_rejects_sentence_instead_of_truncating_its_alignment(self):
        for field, value in (("startTime",True),("startTime",float("nan")),
                             ("endTime",float("inf")),("startTime",-1),
                             ("endTime",3),("endTime",0.1),("word",""),
                             ("startTime","0.1")):
            with self.subTest(field=field,value=value):
                entry = sentence();entry["words"][0][field] = value
                self.assertEqual(normalize_provider_timing([entry],2000),{"segments":[],"words":[]})

    def test_overlapping_words_and_relative_sentence_times_are_rejected(self):
        entry = sentence();entry["words"][1]["startTime"] = 0.2
        self.assertFalse(normalize_provider_timing([entry],2000)["segments"])
        self.assertFalse(normalize_provider_timing([sentence(),sentence("测试")],2000)["segments"])

    def test_incomplete_sentence_is_rejected_but_punctuation_does_not_require_timing(self):
        partial = sentence();partial["words"] = partial["words"][:1]
        self.assertFalse(normalize_provider_timing([partial],1000)["segments"])
        complete = sentence();complete["text"] = "你好."
        self.assertEqual(normalize_provider_timing([complete],1000)["segments"][0]["text"],"你好.")

    def test_legacy_segments_remain_readable_and_duplicates_are_removed(self):
        cue = {"start":0.1,"end":0.8,"text":"已有配音"}
        self.assertEqual(normalize_provider_timing([[cue],{"segments":[cue]}],1000),
                         {"segments":[cue],"words":[]})

    def test_native_timing_skips_asr_and_preserves_provider_payload(self):
        timing = {"provider_timestamps":[sentence()]}
        raw = deepcopy(timing["provider_timestamps"])
        with patch("app.services.refinement_voiceover.transcribe_configured_media") as asr:
            align_voiceover_timing(timing,Path("voice.wav"),"你好",1000)
        asr.assert_not_called()
        self.assertEqual(timing["alignment"],"provider")
        self.assertEqual(timing["provider_timestamps"],raw)
        self.assertEqual(len(timing["words"]),2)

    def test_missing_or_invalid_alignment_uses_configured_asr(self):
        for provider in ([],[{"text":"你好","words":[]}],[sentence(start=2.0)]):
            timing = {"provider_timestamps":provider}
            aligned = {"enabled":True,"segments":[{"start":0.1,"end":0.5,"text":"你好"}],"words":[]}
            with patch("app.services.refinement_voiceover.transcribe_configured_media",return_value=aligned) as asr:
                align_voiceover_timing(timing,Path("voice.wav"),"你好",1000)
            asr.assert_called_once_with(Path("voice.wav"),"你好")
            self.assertEqual(timing["alignment"],"asr")
            self.assertEqual(timing["segments"],aligned["segments"])
