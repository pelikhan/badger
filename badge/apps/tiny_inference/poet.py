"""Endless, bounded couplets from a trained int8 recurrent word model."""

from array import array
import binascii
import gc
import hashlib
import json

from runtime import Linear, require_heap, signed, softmax_q15


ADJECTIVES = (
    "silver", "distant", "quiet", "violet",
    "mossy", "green", "patient", "golden",
    "tiny", "bright", "hidden", "gentle",
    "blue", "salt", "drifting", "amber",
)
NOUNS = (
    "stars", "moons", "comets", "clouds", "skies", "sparks",
    "leaves", "roots", "ferns", "seeds", "pines", "petals",
    "circuits", "signals", "pixels", "codes", "wires", "lights",
    "tides", "shells", "waves", "sails", "reefs", "harbors",
)
VERBS = (
    "glimmer", "wander", "linger", "wake",
    "bloom", "breathe", "listen", "grow",
    "hum", "dream", "pulse", "sing",
    "rise", "drift", "gather", "sleep",
)
PREPOSITIONS = ("beneath", "beyond", "beside", "within", "above", "under", "among", "across")
WORDS = ADJECTIVES + NOUNS + VERBS + PREPOSITIONS + ("\n", "<end>", "<start>", "<pad>")
LINE, END, START, PAD = 64, 65, 66, 67
VOCABULARY = 68
HIDDEN = 64
COLUMNS = VOCABULARY + HIDDEN + 4
OUTPUT_COLUMNS = HIDDEN + 4
MODEL_HEADER = b"TP01\x40\x44\x06\x07"
WEIGHT_BYTES = HIDDEN * COLUMNS + VOCABULARY * OUTPUT_COLUMNS
ALLOCATION = 64 * 1024
MAX_WORDS = 8


def hex_digest(digest):
    return binascii.hexlify(digest.digest()).decode()


def vocabulary_digest():
    return hex_digest(hashlib.sha256(b"\0".join(word.encode() for word in WORDS)))


def wrap_lines(paragraphs, width, measure):
    """Word-wrap the current couplet using actual font measurements."""
    if width <= 0:
        raise ValueError("poem viewport width must be positive")
    rows = []
    for paragraph in paragraphs:
        if rows:
            rows.append("")
        line = ""
        for word in paragraph.split():
            if measure(word)[0] > width:
                raise ValueError("poem viewport cannot fit one word")
            candidate = line + " " + word if line else word
            if measure(candidate)[0] > width:
                rows.append(line)
                line = word
            else:
                line = candidate
        if line:
            rows.append(line)
    return rows


class Poet:
    """Q6 hard-tanh recurrent state and Q4 logits; no float or grammar templates."""

    def __init__(self, kernel, random_bits, assets="assets"):
        require_heap(ALLOCATION, gc.mem_free())
        self.random_bits = random_bits
        self.assets = assets
        self.recurrent = Linear(
            bytearray(HIDDEN * COLUMNS), COLUMNS, HIDDEN,
            bytearray([6] * HIDDEN), kernel)
        self.readout = Linear(
            bytearray(VOCABULARY * OUTPUT_COLUMNS), OUTPUT_COLUMNS, VOCABULARY,
            bytearray([7] * VOCABULARY), kernel)
        self.inputs = bytearray(COLUMNS)
        self.hidden = bytearray(HIDDEN)
        self.next_hidden = bytearray(HIDDEN)
        self.output_inputs = bytearray(OUTPUT_COLUMNS)
        self.logits = bytearray(VOCABULARY)
        self.scores = array("i", [0] * VOCABULARY)
        self.probabilities = array("H", [0] * VOCABULARY)
        self.ready = False
        self.busy = False
        self.number = 0
        self.restart()

    def draw(self):
        value = self.random_bits(15)
        if type(value) is not int or not 0 <= value < 32768:
            raise ValueError("poetry random source must return an unsigned 15-bit integer")
        return value

    def prepare(self):
        if self.busy or self.ready:
            raise RuntimeError("word model is already active or prepared")
        self.busy = True
        try:
            with open(self.assets + "/poet.json", "r") as stream:
                data = stream.read(4097)
            if len(data) > 4096:
                raise ValueError("poetry model manifest is too large")
            metadata = json.loads(data)
            if (not isinstance(metadata, dict)
                    or metadata.get("format") != "tinypoet-v1"
                    or metadata.get("hidden") != HIDDEN
                    or metadata.get("vocabulary") != VOCABULARY
                    or metadata.get("vocabulary_sha256") != vocabulary_digest()
                    or metadata.get("weight_bytes") != WEIGHT_BYTES):
                raise ValueError("incompatible poetry model manifest")
            digest = hashlib.sha256()
            with open(self.assets + "/poet.bin", "rb") as stream:
                header = stream.read(len(MODEL_HEADER))
                if header != MODEL_HEADER:
                    raise ValueError("incompatible poetry model header")
                digest.update(header)
                for weights in (self.recurrent.weights, self.readout.weights):
                    offset = 0
                    while offset < len(weights):
                        chunk = stream.read(min(512, len(weights) - offset))
                        if not chunk:
                            raise ValueError("truncated poetry model")
                        weights[offset:offset + len(chunk)] = chunk
                        digest.update(chunk)
                        offset += len(chunk)
                        yield
                if stream.read(1):
                    raise ValueError("unexpected bytes after poetry model")
            if hex_digest(digest) != metadata.get("model_sha256"):
                raise ValueError("poetry model checksum mismatch")
            self.ready = True
        finally:
            self.busy = False

    def restart(self):
        if self.busy:
            raise RuntimeError("close the active poem operation before restarting")
        self.number = self.number % 999999 + 1
        self.inputs[:] = bytearray(COLUMNS)
        self.inputs[START] = 64
        self.inputs[VOCABULARY + HIDDEN] = 64
        self.hidden[:] = bytearray(HIDDEN)
        self.output_inputs[:] = bytearray(OUTPUT_COLUMNS)
        self.output_inputs[HIDDEN] = 64
        self.previous = START
        self.words = []
        self.lines = []
        self.selected = []
        self.text = ()
        self.complete = False
        self.forced_boundaries = 0

    def step(self):
        """Publish recurrent state and one token only after both native projections."""
        if not self.ready or self.complete or self.busy:
            raise RuntimeError("poem is unprepared, complete, or already advancing")
        self.busy = True
        try:
            yield from self.recurrent.run(self.inputs, self.next_hidden)
            for index in range(HIDDEN):
                value = max(-64, min(64, signed(self.next_hidden[index])))
                self.next_hidden[index] = value & 255
                self.output_inputs[index] = value & 255
            yield from self.readout.run(self.output_inputs, self.logits)
            for token in range(VOCABULARY):
                allowed = token < LINE
                if len(self.words) >= 3:
                    allowed |= token == (LINE if not self.lines else END)
                self.scores[token] = signed(self.logits[token]) * 24 if allowed else -8192
                if token in self.selected:
                    self.scores[token] -= 128
            if len(self.words) == MAX_WORDS:
                chosen = LINE if not self.lines else END
                self.forced_boundaries += 1
            else:
                softmax_q15(self.scores, VOCABULARY, self.probabilities)
                remaining = self.draw()
                chosen = -1
                for token in range(VOCABULARY):
                    remaining -= self.probabilities[token]
                    if remaining < 0:
                        chosen = token
                        break
                if chosen < 0:
                    raise RuntimeError("poetry sampling probabilities do not sum to Q15 unity")
            self.hidden[:] = self.next_hidden
            self.inputs[VOCABULARY:VOCABULARY + HIDDEN] = self.hidden
            self.inputs[self.previous] = 0
            self.inputs[chosen] = 64
            self.previous = chosen
            if chosen in (LINE, END):
                self.lines.append(" ".join(self.words) + ".")
                self.words.clear()
                self.complete = chosen == END
            else:
                word = WORDS[chosen]
                if not self.words:
                    word = word[0].upper() + word[1:]
                self.words.append(word)
                self.selected.append(chosen)
            self.text = tuple(self.lines)
            if self.words:
                self.text += (" ".join(self.words),)
        finally:
            self.busy = False
