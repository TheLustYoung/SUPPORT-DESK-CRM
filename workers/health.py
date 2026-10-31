"""Docker healthcheck of the scheduler: healthy while the every-minute job (sent by beat, run by a worker) keeps
running. Exit code 0 = healthy. Usage: python -m workers.health"""
import sys
from backend.database import Session
from backend.services import options


def main() -> int:
    try:
        with Session() as db:
            alive, _ = options.scheduler_alive(db)
    except Exception:
        return 1
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
