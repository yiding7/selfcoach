"""确定性分析引擎。

这里的每一个数字都不依赖模型。同样的数据，接任何模型跑出来的吨位、组数、
估算 1RM、对比结论完全一致。模型的职责只有一个：把这些结论讲得好听。
"""

from .compare import (Delta, GroupComparison, MovementDelta, compare_group,
                      compare_session, find_anchor)
from .findings import Finding, check_invariants, evaluate, split
from .metrics import (MovementStats, SessionStats, e1rm, rolling_weight,
                      session_stats, set_volume_kg, weight_at)
from .prescribe import (Prescription, prescribe_group, volume_status,
                        weight_trend_pct_per_week)

__all__ = [
    "Delta", "GroupComparison", "MovementDelta", "compare_group", "compare_session",
    "find_anchor", "Finding", "check_invariants", "evaluate", "split",
    "MovementStats", "SessionStats", "e1rm", "rolling_weight", "session_stats",
    "set_volume_kg", "weight_at", "Prescription", "prescribe_group",
    "volume_status", "weight_trend_pct_per_week",
]
