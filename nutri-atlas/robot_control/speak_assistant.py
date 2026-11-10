"""robot_assistant.py with spoken replies: every answer is also sent to the robot speaker.

robot_assistant.py is used unmodified (same arguments, same keyboard input, same prompt). Each reply is
cleaned for speech (no markdown, tags, emojis; "kcal" -> "kilocalorie") and pushed over ZMQ to the robot's
speech_bridge.py (--speak-port, default 5562) -> /tts -> tts_stream_node.py -> Go2 speaker.

  SPEAK_STREAM=first (default) the first finished sentence is spoken while the LLM is still writing,
                               the rest when the answer is done
  SPEAK_STREAM=each            every sentence as soon as it is finished
  SPEAK_STREAM=off             the whole answer at the end

Speech code taken from nutriatlas_voice/voice_assistant.py (speech_text, speak, streaming).

--mic: questions come from the robot microphone instead of the keyboard. The robot's mic.sh (PageUp
push-to-talk -> Google STT) offers each recognized sentence on robot port --listen-port (default 5561), and
this script connects to it at --robot-ip (the robot does not need this machine's address).

Robot side: robot_voice/server_version/robot/ (speech nodes on port 5562, mic.sh), see its README.md.

Usage (all other arguments are passed to robot_assistant.py):
    python speak_assistant.py --robot-ip 192.168.0.164 --detection-mode real --detector vlm
    python speak_assistant.py --robot-ip 192.168.0.164 --detection-mode real --detector vlm --mic
    python speak_assistant.py --robot-ip 192.168.0.164 --detection-mode real --detector vlm --no-voice
"""
import argparse
import builtins
import os
import re
import sys
import unicodedata

import zmq

HERE = os.path.dirname(os.path.abspath(__file__))
RC = HERE   # nutri-atlas/robot_control (next to robot_assistant.py)
SPEAK_STREAM = os.environ.get("SPEAK_STREAM", "first")
# end of a sentence: . ! ? (not "1." of a list) and closing quotes/brackets, followed by a space; or a newline
SENTENCE_END = re.compile(r'(?<!\d)[.!?]+["\')\]]*(?=\s)|\n')
# units after a number, as the TTS should say them (it reads "kcal" letter by letter)
UNITS = {"kcal": "kilocalorie", "cal": "calorie", "g": "gram", "mg": "milligram", "mcg": "microgram",
         "µg": "microgram", "kg": "kilogram", "ml": "milliliter", "mL": "milliliter", "oz": "ounce", "lb": "pound",
         "lbs": "pound", "tbsp": "tablespoon", "tsp": "teaspoon"}
UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(" + "|".join(sorted(map(re.escape, UNITS), key=len, reverse=True))
                     + r")\b")


def speech_text(text):
    """Text as the TTS should read it: the LLM's markdown, tags and symbols removed (the prompt is unchanged).

    "**Meal Suggestion:**\\n- ~13g protein – lean" -> "Meal Suggestion: about 13 grams protein, lean"
    """
    text = re.sub(r"<(tool_call|think)>.*?(</\1>|$)", " ", text, flags=re.S)  # never speak a tool call
    text = re.sub(r"<[^>\n]*>", " ", text)                       # stray tags
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(#+|>+|[-*+•]|\|)\s*", "", line)     # heading, quote, bullet, table start
        line = re.sub(r"^\s*\|?[\s:|-]+\|?\s*$", "", line)       # table separator row |---|---|
        line = line.replace("|", ", ")
        line = re.sub(r"[*_`]+", " ", line)                       # bold, italics, code
        line = re.sub(r"~\s*(?=\d)", "about ", line)              # ~13g -> about 13g
        line = UNIT_RE.sub(lambda m: f"{m[1]} {UNITS[m[2]]}{'' if m[1] == '1' else 's'}", line)
        line = re.sub(r"\s+[–—-]\s+", ", ", line)                 # "Playground – A large area"
        line = re.sub(r"[{}\[\]~^=\\]", " ", line)
        line = "".join(c for c in line if unicodedata.category(c) not in ("So", "Sk", "Cs", "Co"))  # emojis
        line = re.sub(r"\s+", " ", line).strip(" ,")
        if line:
            lines.append(line if line[-1] in ".!?:;," else line + ".")  # a pause between list items
    text = " ".join(lines)
    return re.sub(r"\s+([,.!?:;])", r"\1", text).strip()


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--robot-ip", default=os.environ.get("ROBOT_IP", "127.0.0.1"))
    ap.add_argument("--speak-port", type=int, default=int(os.environ.get("SPEAK_PORT", 5562)))
    ap.add_argument("--no-voice", action="store_true", help="print replies only, like robot_assistant.py")
    ap.add_argument("--mic", action="store_true",
                    help="questions come from the robot microphone (robot: mic.sh) instead of the keyboard")
    ap.add_argument("--listen-port", type=int, default=int(os.environ.get("LISTEN_PORT", 5561)))
    args, rest = ap.parse_known_args()
    sys.argv = [sys.argv[0], "--robot-ip", args.robot_ip] + rest

    os.chdir(RC)
    sys.path.insert(0, RC)
    import robot_assistant as ra

    if args.mic:  # every input('User: ') in robot_assistant waits for a sentence from the robot instead
        pull = zmq.Context.instance().socket(zmq.PULL)
        pull.connect(f"tcp://{args.robot_ip}:{args.listen_port}")   # mic.sh listens on the robot

        def mic_input(prompt=""):
            print(f"{prompt}(waiting for the robot microphone, {args.robot_ip}:{args.listen_port})", flush=True)
            text = pull.recv_string().strip()
            print(f"[mic] {text}", flush=True)
            return text
        builtins.input = mic_input

    if args.no_voice:
        print("[speak] voice off (--no-voice): replies are only printed", flush=True)
        ra.main()
        return

    push = zmq.Context.instance().socket(zmq.PUSH)
    push.setsockopt(zmq.SNDHWM, 50)
    push.connect(f"tcp://{args.robot_ip}:{args.speak_port}")
    print(f"[speak] replies spoken on {args.robot_ip}:{args.speak_port} (SPEAK_STREAM={SPEAK_STREAM})", flush=True)

    turn = {}  # state of the request in progress: "spoken" (any text sent), "call_spoken" (this LLM call)

    def speak(text):
        text = speech_text(text)
        if not text:  # e.g. a "---" line or a heading made only of symbols
            return
        push.send_string(text)
        turn["spoken"] = True
        print(f"[speak] {text}", flush=True)

    def stream_sentences(content):
        """Speak finished sentences of the streamed content (cumulative text of this LLM call)."""
        if SPEAK_STREAM == "off" or (SPEAK_STREAM == "first" and turn.get("spoken")):
            return
        text = content.split("<", 1)[0]  # stop at <tool_call> / <think>: never speak a tool call
        done = turn["call_spoken"]
        ends = [m.end() for m in SENTENCE_END.finditer(text, len(done))]
        if not ends:
            return
        end = ends[0] if SPEAK_STREAM == "first" else ends[-1]
        sentence = text[len(done):end].strip()
        turn["call_spoken"] = text[:end]
        if sentence:
            speak(sentence)

    def streaming_chat(orig_chat):
        def chat(*a, **kw):
            turn["call_spoken"] = ""  # text of this LLM call already sent to the speaker
            for chunks in orig_chat(*a, **kw):
                for chunk in chunks:
                    if chunk.get("role") == "assistant" and isinstance(chunk.get("content"), str):
                        stream_sentences(chunk["content"])
                yield chunks
        return chat

    run_turn = ra._run_turn

    def run_turn_and_speak(messages, llm, tool_definitions, tools, budget=None):
        if not getattr(llm, "_speak_wrapped", False):
            llm.chat = streaming_chat(llm.chat)
            llm._speak_wrapped = True
        turn.clear()
        turn["call_spoken"] = ""
        reply = run_turn(messages, llm, tool_definitions, tools, budget)
        # speak what the streaming has not spoken yet (the reply is the last LLM call's text)
        said = turn.get("call_spoken", "").strip()
        rest = reply[len(said):].strip() if said and reply.startswith(said) else reply
        if rest:
            speak(rest)
        return reply

    ra._run_turn = run_turn_and_speak
    try:
        ra.main()
    finally:
        push.close(linger=1000)  # let the last reply leave


if __name__ == "__main__":
    main()
