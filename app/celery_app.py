from celery import Celery
from settings import settings

celery = Celery("rfa")
celery.conf.broker_url = settings.redis_url
celery.conf.result_backend = settings.redis_url
celery.conf.task_routes = {"app.tasks.*": {"queue": "default"}}
