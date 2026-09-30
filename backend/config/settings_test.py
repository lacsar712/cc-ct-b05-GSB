"""本地无 Postgres 也能跑：DJANGO_SETTINGS_MODULE=config.settings_test。"""

from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
