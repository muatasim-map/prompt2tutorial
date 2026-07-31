"""Quality-neutral performance contracts for final video assembly."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ffmpeg_utils


class VideoAssemblyPerformanceTests(unittest.TestCase):
    def test_primary_concat_command_stream_copies_rendered_frames(self):
        command = ffmpeg_utils.build_concat_video_cmd("scenes.txt", "silent.mp4")

        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertIn("+faststart", command)
        self.assertNotIn("libx264", command)

    def test_concat_falls_back_to_uniform_transcode_when_copy_is_incompatible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "scene_001.mp4"
            second = root / "scene_002.mp4"
            output = root / "silent.mp4"
            concat_list = root / "scenes.txt"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            commands = []

            def fake_run(command, timeout=ffmpeg_utils.DEFAULT_TIMEOUT):
                commands.append(list(command))
                if len(commands) == 1:
                    return subprocess.CompletedProcess(command, 1, "", "incompatible streams")
                output.write_bytes(b"fallback output")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(ffmpeg_utils, "_run", side_effect=fake_run):
                ok = ffmpeg_utils.concat_video(
                    [str(first), str(second)],
                    output,
                    concat_list,
                )

            self.assertTrue(ok)
            self.assertEqual(len(commands), 2)
            self.assertEqual(commands[0][commands[0].index("-c:v") + 1], "copy")
            self.assertIn("libx264", commands[1])
            self.assertIn("yuv420p", commands[1])

    def test_primary_mux_command_copies_normalized_aac_without_reencoding(self):
        command = ffmpeg_utils.build_mux_cmd("silent.mp4", "narration.m4a", "final.mp4")

        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertEqual(command[command.index("-c:a") + 1], "copy")
        self.assertNotIn("192k", command)

    def test_mux_falls_back_to_aac_encode_when_audio_copy_is_incompatible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "silent.mp4"
            audio = root / "narration.m4a"
            output = root / "final.mp4"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            commands = []

            def fake_run(command, timeout=ffmpeg_utils.DEFAULT_TIMEOUT):
                commands.append(list(command))
                if len(commands) == 1:
                    return subprocess.CompletedProcess(command, 1, "", "bad audio stream")
                output.write_bytes(b"fallback output")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(ffmpeg_utils, "_run", side_effect=fake_run):
                ok = ffmpeg_utils.mux_video_audio(video, audio, output)

            self.assertTrue(ok)
            self.assertEqual(len(commands), 2)
            self.assertEqual(commands[0][commands[0].index("-c:a") + 1], "copy")
            self.assertEqual(commands[1][commands[1].index("-c:a") + 1], "aac")
            self.assertEqual(commands[1][commands[1].index("-c:v") + 1], "copy")


if __name__ == "__main__":
    unittest.main()
