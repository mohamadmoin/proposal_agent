"""Placeholder worker (replaced in milestone M6)."""

import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Process queued proposals."

    def handle(self, *args, **options):
        while True:
            time.sleep(5)
