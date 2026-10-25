"""manage.py eval_run: run evals/titles.yaml and write a report with the automatic checks (docs/EVALUATION.md).

Live by default (real model, real PubMed); it does not touch the database. The report leaves a column for an
expert's blind rating.
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from engine.checks import run_checks
from engine.knowledge import get_knowledge
from engine.llm import make_llm
from engine.pipeline import RunOptions, run
from engine.pubmed import make_pubmed
from engine.schemas import SECTION_KEYS, ProposalRequest
from engine.store import MemoryStore

MARK = {True: "pass", False: "FAIL", None: "n/a"}


class Command(BaseCommand):
    help = "Run the evaluation titles and write a report under var/eval/."

    def add_arguments(self, parser):
        parser.add_argument("--titles", default=str(Path(settings.BASE_DIR) / "evals" / "titles.yaml"))
        parser.add_argument("--language", default="fa", choices=["fa", "en", "both"])
        parser.add_argument("--only", default="", help="comma-separated title ids")
        parser.add_argument("--give-design", action="store_true", help="pass the expected design instead of routing")
        parser.add_argument("--allow-fake", action="store_true", help="allow LLM_PROVIDER=fake (a dry run)")
        parser.add_argument("--out", default=str(Path(settings.BASE_DIR) / "var" / "eval"))

    def handle(self, *args, **options):
        knowledge = get_knowledge()
        llm = make_llm(knowledge)
        if llm.config.provider == "fake" and not options["allow_fake"]:
            raise CommandError("LLM_PROVIDER is 'fake'. Set a real provider and key (or pass --allow-fake).")
        pubmed = make_pubmed()
        items = yaml.safe_load(Path(options["titles"]).read_text(encoding="utf-8"))["titles"]
        only = {s for s in options["only"].split(",") if s}
        languages = ["fa", "en"] if options["language"] == "both" else [options["language"]]
        rows = []
        for item in items:
            if only and item["id"] not in only:
                continue
            for lang in languages:
                rows.append(self.run_one(item, lang, options["give_design"], llm, pubmed))
                self.stdout.write(f"{item['id']} [{lang}]: {rows[-1]['status']}")
        out = Path(options["out"])
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        (out / f"eval_{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), "utf-8")
        (out / f"eval_{stamp}.md").write_text(markdown_report(rows, llm), "utf-8")
        self.stdout.write(f"report: {out / f'eval_{stamp}.md'}")

    def run_one(self, item, lang, give_design, llm, pubmed):
        request = ProposalRequest(
            title=item[lang],
            purpose=item.get("purpose", ""),
            design=item["design"] if give_design else "",
            language=lang,
        )
        store = MemoryStore()
        started = time.monotonic()
        result = run(request, store, llm, pubmed, datetime.now(UTC), RunOptions.from_env())
        selected = [
            {"pmid": e["paper"].pmid, "abstract": e["paper"].abstract, "screening": e.get("screening")}
            for e in store.papers_for("background")
            if e.get("selected")
        ]
        checks = run_checks(result, selected, list(SECTION_KEYS))
        design = (result.get("design") or {}).get("label", "")
        return {
            "id": item["id"],
            "language": lang,
            "title": item[lang],
            "expected_design": item["design"],
            "design": design,
            "design_correct": design == item["design"],
            "status": result["status"],
            "seconds": round(time.monotonic() - started, 1),
            "references": len(result.get("references", [])),
            "background_studies": len(selected),
            "usage": result.get("usage"),
            "warnings": store.warnings,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
            "expert_rating": None,
            "result": result,
        }


def markdown_report(rows, llm) -> str:
    names = [c["name"] for c in rows[0]["checks"]] if rows else []
    lines = [
        "# Evaluation report",
        "",
        f"Date: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · provider: {llm.config.provider} · models: "
        f"{llm.config.model_small} / {llm.config.model_strong}",
        "",
        "| Title | Lang | Design (expected) | Status | " + " | ".join(names)
        + " | Refs | Tokens | Cost | Seconds | Expert rating (1–5, blind) |",
        "|" + "---|" * (9 + len(names)),
    ]  # fmt: skip
    for row in rows:
        usage = row["usage"] or {}
        cost = "" if usage.get("cost_usd") is None else f"${usage['cost_usd']}"
        marks = " | ".join(MARK[c["passed"]] for c in row["checks"])
        design = f"{row['design']} ({'ok' if row['design_correct'] else row['expected_design']})"
        lines.append(
            f"| {row['id']} | {row['language']} | {design} | {row['status']} | {marks} | {row['references']} | "
            f"{usage.get('total_tokens', '')} | {cost} | {row['seconds']} |  |"
        )
    lines += ["", "## Check details", ""]
    for row in rows:
        lines.append(f"### {row['id']} [{row['language']}]")
        lines += [f"- {c['name']}: {MARK[c['passed']]} — {c['detail']}" for c in row["checks"]]
        lines += [f"- warning: {w['message']}" for w in row["warnings"]]
        lines.append("")
    return "\n".join(lines)
