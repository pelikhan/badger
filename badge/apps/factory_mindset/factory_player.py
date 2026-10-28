"""Frame-rate-independent slideshow state, with no hardware dependencies."""

DURATIONS = (5400, 5400, 4600, 5200, 4600, 6000, 4800, 6500,
             6000, 5200, 5600, 6000, 7200, 6000, 8000)
TRANSITION_MS = 420

REVEAL_MS = 480


class Player:
    def __init__(self):
        self.scene = 0
        self.previous = 0
        self.elapsed = 0
        self.transition = TRANSITION_MS
        self.direction = 1
        self.paused = False
        self.reveal_all = False

    def navigate(self, direction):
        self.previous = self.scene
        self.scene = (self.scene + direction) % len(DURATIONS)
        self.direction = direction
        self.elapsed = 0
        self.reveal_all = self.paused
        # A paused reader sees the whole selected scene immediately.
        self.transition = TRANSITION_MS if self.paused else 0

    def advance(self, delta_ms, left=False, right=False, toggle=False):
        if toggle:
            self.paused = not self.paused
        if left != right:
            self.navigate(-1 if left else 1)
            return
        if toggle or self.paused:
            return

        # Do not fast-forward the story after a long USB/debugger stall.
        delta_ms = min(100, max(0, delta_ms))
        if self.transition < TRANSITION_MS:
            step = min(delta_ms, TRANSITION_MS - self.transition)
            self.transition += step
            delta_ms -= step
        self.elapsed += delta_ms
        if self.elapsed >= DURATIONS[self.scene]:
            remaining = self.elapsed - DURATIONS[self.scene]
            self.navigate(1)
            self.transition = remaining

    def offset(self, width):
        progress = self.transition / TRANSITION_MS
        return int(width * (1 - progress) ** 3) * self.direction
