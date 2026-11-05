#!/usr/bin/env python3
"""Pipelined TTS node: Chang's tts_node with the next piece prepared while the current one plays.

Experimental replacement for the original tts_node (demo version: versions/demo_v1, original nodes).
It imports the original classes from Chang's build (read-only) and changes only the scheduling:

    original : one text at a time: generate -> convert -> upload -> play (sleep duration + 1.0 s) -> next
    this node: /tts callback only queues the text
               prepare thread: ElevenLabs (or cache) -> wav -> chunks, for the next text while one plays
               play thread   : START, chunks, sleep duration + PLAYBACK_PAD (default 1.0 s, as the original), STOP

Log lines keep the original wording plus a request number ("#3"), so latency_report.py can follow
requests that now overlap.

Run with ROS 2 (foxy) and Chang's build sourced, see tts_stream_nodes.sh.
"""
import base64
import json
import os
import queue
import threading
import time

import rclpy

from speech_processor.tts_node import AudioFormat, EnhancedTTSNode

PLAYBACK_PAD = float(os.environ.get("PLAYBACK_PAD", 1.0))  # wait after the audio length, as the original (0.2 s dropped the next sentence)


class StreamTTSNode(EnhancedTTSNode):
    def __init__(self):
        self._texts = queue.Queue()            # (n, text) from /tts
        self._ready = queue.Queue(maxsize=2)   # (n, chunks, duration) prepared, waiting to play
        self._n = 0
        super().__init__()                     # creates the /tts subscription bound to tts_callback
        if not self.tts_provider:
            return
        threading.Thread(target=self._prepare_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()
        self.get_logger().info(f"   Pipelined: next text prepared during playback, pad {PLAYBACK_PAD:.1f}s")

    def tts_callback(self, msg):
        text = msg.data.strip()
        if not text:
            self.get_logger().warn("Received empty TTS request")
            return
        self._n += 1
        self.get_logger().info(f'🎤 TTS Request #{self._n}: "{text}" (voice: {self.config.voice_name})')
        self._texts.put((self._n, text))

    def _prepare_loop(self):
        log = self.get_logger()
        while True:
            n, text = self._texts.get()
            try:
                provider = self.config.provider.value
                audio = self.cache.get(text, self.config.voice_name, provider)
                if audio:
                    log.info(f"💾 Cache hit #{n} - using cached audio")
                else:
                    log.info(f"🔊 Generating new speech #{n}...")
                    audio = self.tts_provider.synthesize(text)
                    if not audio:
                        log.error(f"❌ Failed to generate speech #{n}")
                        continue
                    self.cache.put(text, self.config.voice_name, provider, audio)
                    log.info(f"💾 Audio generated #{n}")
                wav = self.audio_processor.convert_to_wav(audio, AudioFormat.MP3)
                if not wav:
                    log.error(f"❌ Failed to convert audio to WAV #{n}")
                    continue
                duration = self.audio_processor.get_duration(wav, AudioFormat.WAV)
                b64 = base64.b64encode(wav)
                chunks = self.audio_processor.split_into_chunks(b64, self.config.chunk_size)
                self._ready.put((n, chunks, duration))
            except Exception as e:
                log.error(f"❌ TTS processing error #{n}: {e}")

    def _play_loop(self):
        log = self.get_logger()
        cmd = self.AUDIO_HUB_COMMANDS
        while True:
            n, chunks, duration = self._ready.get()
            try:
                log.info(f"📤 Sending audio to robot #{n}: {len(chunks)} chunks, {duration:.1f}s duration")
                self._send_audio_command(cmd.get("START_AUDIO", 4001), "")
                time.sleep(0.1)
                for i, chunk in enumerate(chunks, 1):
                    block = {"current_block_index": i, "total_block_number": len(chunks),
                             "block_content": chunk.decode()}
                    self._send_audio_command(cmd.get("SEND_AUDIO_BLOCK", 4003), json.dumps(block))
                    time.sleep(0.05)
                log.info(f"⏳ Waiting for playback completion #{n} ({duration:.1f}s)...")
                time.sleep(duration + PLAYBACK_PAD)
                self._send_audio_command(cmd.get("STOP_AUDIO", 4002), "")
                log.info(f"🎵 Robot playback completed #{n}")
            except Exception as e:
                log.error(f"❌ Robot playback error #{n}: {e}")


def main():
    rclpy.init()
    try:
        rclpy.spin(StreamTTSNode())
    except KeyboardInterrupt:
        pass
    finally:
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
