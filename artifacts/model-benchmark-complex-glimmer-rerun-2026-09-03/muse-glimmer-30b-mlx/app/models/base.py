from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Entity:
    id: int
    created_at: datetime

    def age_seconds(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.created_at).total_seconds())
