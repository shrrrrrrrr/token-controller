"""Bounded execution deadlines, independent of the workflow heartbeat lease."""

GENERATION_SECONDS = 600
# Includes up to 25 seconds of synchronous queueing plus transport margin.
CLIENT_SECONDS = 660
