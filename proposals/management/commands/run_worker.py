"""manage.py run_worker [--once] [--poll 2]: process queued proposals."""

from django.core.management.base import BaseCommand

from proposals.worker import Worker, requeue_stale


class Command(BaseCommand):
    help = "Process queued proposals (scale with: docker compose up --scale worker=N)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="process at most one job, then exit")
        parser.add_argument("--poll", type=float, default=2.0, help="seconds between polls of an empty queue")

    def handle(self, *args, **options):
        worker = Worker(poll=options["poll"])
        if options["once"]:
            requeue_stale()
            status = worker.run_once()
            self.stdout.write(f"processed: {status}" if status else "queue empty")
            return
        worker.install_signal_handlers()
        self.stdout.write("worker started; waiting for proposals")
        worker.run_forever()
        self.stdout.write("worker stopped")
