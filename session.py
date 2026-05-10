import asyncio
from dataclasses import dataclass, field


@dataclass
class Session:
    call_sid: str
    history: list[dict] = field(default_factory=list)
    collected: dict = field(
        default_factory=lambda: {"name": None, "phone": None, "date": None, "time": None}
    )
    booking_status: str = "pending"
    silence_timer: asyncio.Task | None = None
    greeting_played: bool = False  # set True after first call.playback.ended
    audio_files: list[str] = field(default_factory=list)  # filenames generated for this call


sessions: dict[str, Session] = {}


def create_session(call_sid: str) -> Session:
    session = Session(call_sid=call_sid)
    sessions[call_sid] = session
    return session


def get_session(call_sid: str) -> Session | None:
    return sessions.get(call_sid)


def delete_session(call_sid: str) -> None:
    sessions.pop(call_sid, None)
