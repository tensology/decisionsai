"""Property 6: Ping-pong frame index progression.

For any sequence of N frames (N >= 1), advancing the ping-pong player should
cycle the frame index forward from 0 to N-1, then backward from N-1 to 0,
and repeat.  The frame index should never go below 0 or above N-1.

**Validates: Requirements 4.4**
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.gui.oracle.webm_player import advance_pingpong


@given(
    num_frames=st.integers(min_value=1, max_value=200),
    num_steps=st.integers(min_value=1, max_value=1000),
)
@settings(max_examples=100)
def test_pingpong_index_always_in_bounds(num_frames: int, num_steps: int) -> None:
    """**Validates: Requirements 4.4**

    The frame index must never go below 0 or above N-1 after any number
    of advance steps.
    """
    index = 0
    forward = True
    for _ in range(num_steps):
        index, forward = advance_pingpong(index, num_frames, forward)
        assert 0 <= index < num_frames, (
            f"Index {index} out of bounds for {num_frames} frames"
        )


@given(num_frames=st.integers(min_value=2, max_value=100))
@settings(max_examples=100)
def test_pingpong_full_cycle_returns_to_start(num_frames: int) -> None:
    """**Validates: Requirements 4.4**

    A full forward-then-backward cycle visits every frame and returns
    to index 0.  One full cycle takes 2*(N-1) steps.
    """
    index = 0
    forward = True
    visited: list[int] = [0]

    cycle_length = 2 * (num_frames - 1)
    for _ in range(cycle_length):
        index, forward = advance_pingpong(index, num_frames, forward)
        visited.append(index)

    # After a full cycle we should be back at 0
    assert index == 0, f"Expected index 0 after full cycle, got {index}"
    # Every frame index 0..N-1 should have been visited
    assert set(range(num_frames)).issubset(set(visited)), (
        f"Not all frames visited: missing {set(range(num_frames)) - set(visited)}"
    )
