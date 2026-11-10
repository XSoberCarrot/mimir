#!/usr/bin/env python3
"""Robot microphone -> Google STT -> text for the server's speak_assistant.py --mic.

The recognized text is offered on ZMQ port 5561 of the robot (PUSH bind); speak_assistant.py --mic connects
to it at its --robot-ip, so this script does not need the server's address.

Push-to-talk: PageUp on the presenter remote starts recording, PageUp again stops. The recognized
sentence is sent to the agent as if it had been typed. Same method as ~/Man/nutriatlas_voice/voice_echo.py
(hardware/microphone.py), copied here so nothing in that folder is changed; only its Google credentials
file is read.

Run with mic.sh (sudo for the PageUp hook, go2-agent-demo env):
    ./mic.sh                                  # PageUp push-to-talk, text offered on port 5561
    ./mic.sh --seconds 4                      # one fixed 4 s recording, no PageUp (test)
    ./mic.sh --print-only                     # recognize and print, send nothing (test)
"""
import argparse
import os
import sys
import threading
import time

DEFAULT_CREDENTIALS = "/home/unitree/Man/nutriatlas_voice/silent-scanner-477920-h2-b01d1c1e2270.json"


def find_usb_input_device(sd):
    for index, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] > 0 and "USB" in info["name"]:
            return index
    return None


def _quiet_stop_exception(args, _default=threading.excepthook):
    """pynput ends a listener by raising StopException in its thread: normal, do not print it."""
    from pynput._util import AbstractListener
    if issubclass(args.exc_type, AbstractListener.StopException):
        return
    _default(args)


def wait_for_pageup():
    from pynput import keyboard
    threading.excepthook = _quiet_stop_exception
    pressed = threading.Event()

    def on_press(key):
        if key == keyboard.Key.page_up:
            pressed.set()
            return False

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    pressed.wait()
    listener.stop()


def record_push_to_talk(sd, np, rate, device):
    print("Press PageUp to START recording...", flush=True)
    wait_for_pageup()
    print("Recording... (PageUp again to stop)", flush=True)
    buf, stop = [], threading.Event()

    def callback(indata, frames, t, status):
        if status:
            print(status, file=sys.stderr)
        buf.append(indata.copy())

    threading.Thread(target=lambda: (wait_for_pageup(), stop.set()), daemon=True).start()
    with sd.InputStream(samplerate=rate, channels=1, dtype="int16", device=device, callback=callback):
        while not stop.is_set():
            sd.sleep(50)
    print("Stopped recording.", flush=True)
    return np.concatenate(buf, axis=0).tobytes() if buf else b""


def record_fixed(sd, rate, device, seconds):
    print(f"Recording {seconds}s...", flush=True)
    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16", device=device)
    sd.wait()
    return audio.tobytes()


def transcribe(client, speech, audio, rate, language):
    if not audio:
        return ""
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=rate,
        language_code=language,
        enable_automatic_punctuation=True,
    )
    response = client.recognize(config=config, audio=speech.RecognitionAudio(content=audio))
    if not response.results:
        return ""
    return response.results[0].alternatives[0].transcript


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("MIC_PORT", 5561)),
                    help="port speak_assistant.py --mic connects to (its --listen-port)")
    ap.add_argument("--language", default="en-US", help='STT language, e.g. "zh-CN"')
    ap.add_argument("--rate", type=int, default=48000)
    ap.add_argument("--device", type=int, help="input device index (default: first device with USB in its name)")
    ap.add_argument("--credentials", default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", DEFAULT_CREDENTIALS))
    ap.add_argument("--seconds", type=float, help="one fixed recording of N s instead of PageUp, then exit")
    ap.add_argument("--print-only", action="store_true", help="do not send, only print the recognized text")
    ap.add_argument("--exit-with", type=int,
                    help="exit when this process ends (start_robot_side.sh: it cannot kill a sudo process)")
    args = ap.parse_args()

    if args.exit_with:
        def watch(pid=args.exit_with):
            while True:
                time.sleep(1)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    os._exit(0)  # no print: Ctrl+C also ended the [mic] prefixer, writing would fail first
        threading.Thread(target=watch, daemon=True).start()

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = args.credentials
    import numpy as np
    import sounddevice as sd
    import zmq
    from google.cloud import speech_v1 as speech

    device = args.device if args.device is not None else find_usb_input_device(sd)
    print(f"mic device index: {device}", flush=True)
    if device is None:
        print("WARNING: no USB microphone found (lsusb should list 4c4a:4155, the Jieli receiver). "
              "Recording from the default device instead.", flush=True)
    client = speech.SpeechClient()
    out = None
    if not args.print_only:
        out = zmq.Context.instance().socket(zmq.PUSH)
        try:
            out.bind(f"tcp://*:{args.port}")
        except zmq.ZMQError as e:
            print(f"ERROR: port {args.port}: {e}. An earlier microphone is probably still running; stop it with\n"
                  f"  sudo pkill -f mic_input.py", flush=True)
            sys.exit(1)
        print(f"recognized text offered on port {args.port} (server: speak_assistant.py --mic)", flush=True)

    try:
        while True:
            audio = (record_fixed(sd, args.rate, device, args.seconds) if args.seconds
                     else record_push_to_talk(sd, np, args.rate, device))
            t0 = time.time()
            text = transcribe(client, speech, audio, args.rate, args.language)
            print(f"You said: {text!r}  (STT {time.time() - t0:.2f}s, audio {len(audio) / 2 / args.rate:.1f}s)",
                  flush=True)
            if text and out:
                try:  # no server connected: drop the sentence instead of blocking
                    out.send_string(text, flags=zmq.NOBLOCK)
                except zmq.Again:
                    print("  not sent: no server connected (start speak_assistant.py --mic)", flush=True)
            if args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if out:
            out.close(linger=2000)  # let the last sentence leave


if __name__ == "__main__":
    main()
