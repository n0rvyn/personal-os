"""Angle slot rotation per design D-006.

Per Phase-4 D-006 (Adam crystal, supersedes original 5-angle quota): 反对意见 dropped
from forced rotation — earned via contrarian_source, not a mandatory slot.
"""
DEFAULT_ANGLES = ["技术内核", "商业影响", "用户体验", "历史类比"]

def pick_unused_angle(used_angles: list) -> str:
    """Return the first DEFAULT_ANGLES entry not in used_angles.
    If all angles already used (saturation), rotate back to DEFAULT_ANGLES[0] (oldest-first policy).
    """
    for angle in DEFAULT_ANGLES:
        if angle not in used_angles:
            return angle
    return DEFAULT_ANGLES[0]
