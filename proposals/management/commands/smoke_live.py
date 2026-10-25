"""manage.py smoke_live --title "…": one real proposal with real keys, reported as JSON (and .docx) under var/.

Never run in CI: it calls the model provider and PubMed and costs a few cents. It does not touch the database.
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from engine.docx_export import build_docx
from engine.knowledge import get_knowledge
from engine.llm import make_llm
from engine.pipeline import RunOptions, run
from engine.pubmed import make_pubmed
from engine.schemas import SECTION_KEYS, ProposalRequest
from engine.store import MemoryStore


class Command(BaseCommand):
    help = "Run one real proposal end to end and write a JSON report under var/ (never in CI)."

    def add_arguments(self, parser):
        parser.add_argument("--title", required=True)
        parser.add_argument("--purpose", default="")
        parser.add_argument("--design", default="")
        parser.add_argument("--language", default="fa", choices=["fa", "en"])
        parser.add_argument("--sections", default="", help="comma-separated, e.g. background,methods")
        parser.add_argument("--html", default="", help="path to a StatPearls page saved from your browser")
        parser.add_argument("--allow-fake", action="store_true", help="allow LLM_PROVIDER=fake (for a dry run)")

    def handle(self, *args, **options):
        knowledge = get_knowledge()
        llm = make_llm(knowledge)
        if llm.config.provider == "fake" and not options["allow_fake"]:
            raise CommandError("LLM_PROVIDER is 'fake'. Set openai or gemini and LLM_API_KEY (or pass --allow-fake).")
        html = Path(options["html"]).read_text(encoding="utf-8") if options["html"] else ""
        request = ProposalRequest(
            title=options["title"],
            purpose=options["purpose"],
            design=options["design"],
            language=options["language"],
            sections=[s.strip() for s in options["sections"].split(",") if s.strip()] or list(SECTION_KEYS),
            statpearls_html=html,
        )
        store = MemoryStore()
        started = time.monotonic()
        result = run(request, store, llm, make_pubmed(), datetime.now(UTC), RunOptions.from_env())
        elapsed = round(time.monotonic() - started, 1)
        out_dir = Path(settings.BASE_DIR) / "var"
        out_dir.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        report = {
            "request": request.model_dump(exclude={"statpearls_html"}),
            "provider": llm.config.provider,
            "models": {"small": llm.config.model_small, "strong": llm.config.model_strong},
            "seconds": elapsed,
            "result": result,
            "warnings": store.warnings,
            "steps": store.steps,
            "searches": store.searches,
            "calls": [{k: v for k, v in c.items() if k != "messages"} for c in store.calls],
        }
        path = out_dir / f"smoke_{stamp}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (out_dir / f"smoke_{stamp}.docx").write_bytes(build_docx(result))
        self.stdout.write(f"status: {result['status']} in {elapsed}s; usage: {result['usage']}")
        self.stdout.write(f"report: {path}")
