from celery import Celery
from celery.signals import after_setup_logger
from backend import observability
from backend.config import settings

cfg = settings()
celery = Celery("support", broker=cfg.redis_url, backend=cfg.redis_url, include=["workers.tasks"])
celery.conf.update(
    task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
    task_soft_time_limit=600, task_time_limit=660, broker_transport_options={"visibility_timeout": 900},
    task_serializer="json", accept_content=["json"], result_serializer="json", result_expires=3600, task_ignore_result=True,
    beat_schedule={
        # Redis is not the source of truth: lost tasks are found in the database and started again.
        "reconcile": {"task": "workers.tasks.reconcile", "schedule": 60},
        # SLA breaches and warnings, reminders to users, auto-close, the Monday summary
        "tickets": {"task": "workers.tasks.check_tickets", "schedule": 60},
        "campaigns": {"task": "workers.tasks.start_scheduled_campaigns", "schedule": 60},
        "retention": {"task": "workers.tasks.retention", "schedule": 6 * 3600},
    },
)
observability.setup("worker")  # error tracking when SENTRY_DSN is set


@after_setup_logger.connect
def json_logs(logger, *_args, **_kwargs):
    for handler in logger.handlers:
        handler.setFormatter(observability.JsonFormatter("worker"))
