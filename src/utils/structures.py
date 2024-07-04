import dataclasses
from dataclasses import dataclass, field
import datetime


@dataclasses.dataclass
class UserData:
    user_id: int
    username: str
    email: str | None = None
    generations: int = 0
    today_generations: int = 0
    last_generation_date: datetime.datetime | None = None
    messages: list = field(default_factory=lambda: [])
    bot_state: str = field(default_factory=lambda: "default")
    first_message_index: int = 0
    temp_data: dict = field(default_factory=lambda: {})
    stuck_reminder_enabled: bool = field(default_factory=lambda: True)
    bot_role: str = field(default_factory=lambda: 'english tutor')
    user_file_idx: int = field(default_factory=lambda: 0)


    def __getitem__(self, item):
        return getattr(self, item)
