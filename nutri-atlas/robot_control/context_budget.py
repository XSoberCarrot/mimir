"""Keep the chat history inside the llama-server context window, counting image tokens.

qwen-agent's built-in trimming counts only the text of each message (images are
ignored) and cannot shorten image messages, so a history with camera images can
exceed the server's context and the request is rejected. This module replaces it:

* text tokens are counted exactly with the server's own tokenizer (/tokenize);
* image tokens are computed from the image size with the Qwen3.5 vision rule
  (verified against llama-server on 15 sizes from 50x40 to 4000x3000);
* when the history is too long, it is cut chronologically from the oldest end:
  whole turns (question + tool calls + tool results + camera images + answer) are
  dropped, oldest first, until it fits. Images are treated like any other content:
  an image is removed only when the cut reaches its turn, and then whole (never
  half an image). The system message and the current turn are never touched.
* trimming has hysteresis: it starts above HIGH and trims down to LOW, so the
  start of the prompt then stays unchanged for several turns and the server can
  keep reusing its cache (Qwen3.5 must re-read everything when the prefix changes).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os

import requests

# Qwen3.5 vision encoder (llama.cpp mmproj `qwen3vl_merger`): patch 16 x merge 2 =
# one token per 32x32 pixels, plus 2 marker tokens; images are resized to a
# multiple of 32 within [image_min_pixels, image_max_pixels] (values from the log).
_FACTOR = 32
_MIN_PIXELS = 8192
_MAX_PIXELS = 4194304
_IMAGE_MARKERS = 2

_MSG_OVERHEAD = 6        # <|im_start|>role\n ... <|im_end|>\n
_FUNC_OVERHEAD = 20      # function-call wrapping added by qwen-agent

# Message tags (Message.extra['kind']) for user-role messages that are not new questions.
KIND_CAMERA = 'camera_image'
KIND_NUDGE = 'nudge'


def image_tokens(width: int, height: int) -> int:
    """Number of context tokens one image of this size takes (Qwen3.5 / llama.cpp)."""
    hb = max(_FACTOR, round(height / _FACTOR) * _FACTOR)
    wb = max(_FACTOR, round(width / _FACTOR) * _FACTOR)
    if hb * wb > _MAX_PIXELS:
        beta = math.sqrt(height * width / _MAX_PIXELS)
        hb = math.floor(height / beta / _FACTOR) * _FACTOR
        wb = math.floor(width / beta / _FACTOR) * _FACTOR
    elif hb * wb < _MIN_PIXELS:
        beta = math.sqrt(_MIN_PIXELS / (height * width))
        hb = math.ceil(height * beta / _FACTOR) * _FACTOR
        wb = math.ceil(width * beta / _FACTOR) * _FACTOR
    return (hb // _FACTOR) * (wb // _FACTOR) + _IMAGE_MARKERS


def _get(item, key):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _kind(msg) -> str | None:
    extra = _get(msg, 'extra') or {}
    return extra.get('kind')


def _image_size(src: str) -> tuple[int, int]:
    from PIL import Image
    if src.startswith('data:'):
        data = base64.b64decode(src.split(',', 1)[1])
        return Image.open(io.BytesIO(data)).size
    return Image.open(os.path.expanduser(src.replace('file://', ''))).size


class ContextBudget:
    def __init__(self, server_url: str, n_ctx: int | None = None, reserve_output: int = 2048,
                 high: float = 0.90, low: float = 0.70, fixed_overhead: int = 0, verbose: bool = True):
        """server_url: llama-server base URL, e.g. http://localhost:8080 (no /v1).
        n_ctx: context size; read from the server's /props when None.
        reserve_output: tokens kept free for the reply (also pass it as max_tokens).
        fixed_overhead: tokens added outside `messages`, e.g. tool definitions.
        """
        self.url = server_url.rstrip('/').removesuffix('/v1')
        self.n_ctx = n_ctx or self._server_ctx()
        self.reserve_output = reserve_output
        self.limit = self.n_ctx - reserve_output
        self.high, self.low = high, low
        self.fixed_overhead = fixed_overhead
        self.verbose = verbose
        self._text_cache: dict[str, int] = {}
        self._image_cache: dict[str, int] = {}

    # ------------------------------------------------------------------ counting
    def _server_ctx(self) -> int:
        try:
            props = requests.get(self.url + '/props', timeout=5).json()
            return int(props['default_generation_settings']['n_ctx'])
        except Exception:
            return int(os.environ.get('LLM_CTX', 32768))

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        key = hashlib.sha1(text.encode()).hexdigest()
        if key not in self._text_cache:
            try:
                r = requests.post(self.url + '/tokenize', json={'content': text}, timeout=10)
                self._text_cache[key] = len(r.json()['tokens'])
            except Exception:
                self._text_cache[key] = len(text) // 3 + 1      # conservative fallback
        return self._text_cache[key]

    def count_image(self, src: str) -> int:
        key = hashlib.sha1(src[:256].encode() + str(len(src)).encode()).hexdigest()
        if key not in self._image_cache:
            try:
                self._image_cache[key] = image_tokens(*_image_size(src))
            except Exception:
                self._image_cache[key] = image_tokens(1280, 960)  # conservative fallback
        return self._image_cache[key]

    def count_message(self, msg) -> int:
        content = _get(msg, 'content')
        n = _MSG_OVERHEAD
        if isinstance(content, str):
            n += self.count_text(content)
        elif content:
            for item in content:
                if _get(item, 'image'):
                    n += self.count_image(_get(item, 'image'))
                elif _get(item, 'text'):
                    n += self.count_text(_get(item, 'text'))
        if _get(msg, 'role') == 'function' or _get(msg, 'function_call'):
            n += _FUNC_OVERHEAD
            fc = _get(msg, 'function_call')
            if fc:
                n += self.count_text(json.dumps(fc if isinstance(fc, dict) else fc.model_dump()))
        return n

    def count(self, messages: list) -> int:
        return self.fixed_overhead + sum(self.count_message(m) for m in messages)

    # ------------------------------------------------------------------ trimming
    @staticmethod
    def _turn_starts(messages: list) -> list[int]:
        """Indices of real user questions (not system, tool results, camera images or nudges)."""
        return [i for i, m in enumerate(messages)
                if _get(m, 'role') == 'user' and _kind(m) not in (KIND_CAMERA, KIND_NUDGE)]

    @staticmethod
    def _has_image(msg) -> bool:
        content = _get(msg, 'content')
        return isinstance(content, list) and any(_get(it, 'image') for it in content)

    def fit(self, messages: list) -> list:
        """Trim `messages` in place so the prompt fits. Returns `messages`.

        Raises ContextOverflow if even the system message + current turn do not fit.
        """
        total = self.count(messages)
        if total <= self.high * self.limit:
            return messages
        target = self.low * self.limit
        before = total
        stats = {'turns': 0, 'images': 0}

        # drop whole old turns, oldest first (system message stays at index 0)
        while total > target:
            starts = self._turn_starts(messages)
            if len(starts) < 2:
                break
            first = 1 if _get(messages[0], 'role') == 'system' else 0   # oldest turn starts here
            nxt = starts[1]                                              # ... and ends before the 2nd question
            total -= sum(self.count_message(m) for m in messages[first:nxt])
            stats['images'] += sum(self._has_image(m) for m in messages[first:nxt])
            del messages[first:nxt]
            stats['turns'] += 1

        if total > self.limit:
            raise ContextOverflow(
                f'the current turn alone needs about {total} tokens, more than the {self.limit} available '
                f'(context {self.n_ctx} minus {self.reserve_output} for the reply)')
        if self.verbose:
            print(f"[context] trimmed {before} -> {total} tokens (limit {self.limit}): "
                  f"dropped the {stats['turns']} oldest turn(s), including {stats['images']} image(s)")
        return messages


class ContextOverflow(RuntimeError):
    pass
