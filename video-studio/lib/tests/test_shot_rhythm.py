"""Unit tests for POC6 shot-rhythm timing (pace weights + dwell distribution)."""
import pytest

from lib import ffcmd
from lib import storyboard


def test_pace_weights_ordered_hold_normal_quick():
    assert storyboard.pace_weight("hold") > storyboard.pace_weight("normal")
    assert storyboard.pace_weight("normal") > storyboard.pace_weight("quick")
    assert storyboard.pace_weight("garbage") == storyboard.pace_weight("normal")


def test_max_shots_floors_on_min_dwell():
    assert ffcmd.MIN_DWELL == 0.8
    assert ffcmd.max_shots(4.0) == 5            # exact multiple (eps guards float)
    assert ffcmd.max_shots(2.4) == 3            # exact multiple
    assert ffcmd.max_shots(2.5) == 3            # 2.5/0.8 = 3.125
    assert ffcmd.max_shots(0.5) == 1            # never below 1


def test_shot_durations_single_shot_is_whole_clip():
    assert ffcmd.shot_durations(3.7, [1.0]) == [3.7]


def test_shot_durations_weighted_split_sums_exactly():
    durs = ffcmd.shot_durations(6.0, [2.0, 1.0, 1.0])  # hold + 2 normal
    assert abs(sum(durs) - 6.0) < 1e-9
    # the hold shot gets the most time
    assert durs[0] > durs[1] and durs[0] > durs[2]
    assert durs[0] == pytest.approx(3.0)


def test_shot_durations_falls_back_to_equal_when_below_min_dwell():
    # 2.0s across 2 shots weighted 0.6/2.0 → quick slice ≈0.46s < MIN_DWELL,
    # so it must fall back to an equal 1.0/1.0 split (no subliminal flash).
    durs = ffcmd.shot_durations(2.0, [0.6, 2.0])
    assert durs == pytest.approx([1.0, 1.0])
    assert min(durs) >= ffcmd.MIN_DWELL


def test_shot_durations_sum_exact_even_on_fallback():
    durs = ffcmd.shot_durations(2.5, [0.6, 0.6, 2.0])
    assert abs(sum(durs) - 2.5) < 1e-9
