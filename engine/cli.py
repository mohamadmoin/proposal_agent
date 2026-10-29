"""Command line: draft a proposal from a title, without the web app.

    python -m engine.cli check
    python -m engine.cli run "شیوع کمبود ویتامین D در زنان باردار مراجعه‌کننده به مراکز بهداشتی تهران"
    python -m engine.cli run "…" --fake          # no keys, no network: the built-in fake model and saved PubMed records

(`./proposal-agent` in the repository root is the same command.)

The default engine is the agentic one (a lead researcher agent with cheap workers; it needs a Gemini key, and an
OpenAI key, if set, is its fallback). `--engine pipeline` runs the step-by-step pipeline instead. Settings come from the environment
and, if present, from `.env` in the repository root; a variable already set in the shell wins.
Outputs go to `output/<time>-<title>/`: proposal.docx, result.json (the sections and references) and trace.json
(every step, search, warning and model call, without the prompts).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engine.store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent
ENGINES = ("agentic", "pipeline")


def load_dotenv(path: Path) -> list[str]:
    """Read KEY=VALUE lines into os.environ without overriding what the shell already set. Returns the keys read."""
    if not path.is_file():
        return []
    read = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            read.append(key)
    return read


def has(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def model_keys() -> dict[str, bool]:
    """Which model keys are present (never their values)."""
    from engine.agentic.models import google_key

    return {"gemini": bool(google_key(os.environ)), "openai": has("OPENAI_API_KEY"),
            "pipeline (LLM_API_KEY)": has("LLM_API_KEY")}


def config_problem(engine: str) -> str:
    """Why this engine cannot run with the current settings, or '' if it can."""
    keys = model_keys()
    if engine == "agentic" and not keys["gemini"]:
        return ("The agentic engine needs a Gemini key: set GOOGLE_API_KEY (OPENAI_API_KEY is only its fallback). "
                "Or try `--fake` (no keys), or `--engine pipeline` with LLM_PROVIDER=openai and LLM_API_KEY.")
    if engine == "pipeline":
        provider = (os.environ.get("LLM_PROVIDER") or "openai").strip().lower()
        if provider != "fake" and not keys["pipeline (LLM_API_KEY)"]:
            return f"The pipeline engine with LLM_PROVIDER={provider} needs LLM_API_KEY (or use --fake)."
    return ""


class PrintingStore(MemoryStore):
    """The engine's in-memory store, also printing each step as it happens."""

    def __init__(self, quiet: bool = False) -> None:
        super().__init__()
        self.quiet, self.t0 = quiet, time.monotonic()

    def _say(self, mark: str, text: str) -> None:
        if not self.quiet:
            print(f"  {time.monotonic() - self.t0:6.0f}s  {mark} {text}", flush=True)

    def step_started(self, step: str, label: str = "") -> None:
        super().step_started(step, label)
        self._say("▸", label or step)

    def step_done(self, step: str, detail: str = "") -> None:
        super().step_done(step, detail)
        self._say("✓", f"{step}{': ' + detail if detail else ''}")

    def step_failed(self, step: str, reason: str) -> None:
        super().step_failed(step, reason)
        self._say("✗", f"{step}: {reason}")

    def warn(self, message: str, step: str = "") -> None:
        super().warn(message, step)
        self._say("!", message)

    def step_event(self, step: str, item: dict[str, Any]) -> None:
        """The agentic engine's fine-grained events (a search, a paper kept, a value extracted, a section returned)."""
        super().step_event(step, item)
        fields = [f"{k}={v}" for k, v in item.items() if k not in {"kind", "at"} and not isinstance(v, dict | list)]
        detail = ", ".join(fields)[:110]
        self._say("·", f"{step.removeprefix('plan:')} {item.get('kind', '')}{': ' + detail if detail else ''}")


def slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    return re.sub(r"\s+", "-", s)[:48] or "proposal"


def summary(result: dict[str, Any], seconds: float) -> list[str]:
    usage = result.get("usage") or {}
    cost = usage.get("cost_usd")
    lines = [f"status: {result.get('status')}   time: {seconds / 60:.1f} min" + (f"   model cost: ${cost:.2f}" if cost else "")]
    sections = result.get("sections") or {}
    if sections:
        lines.append("sections: " + ", ".join(f"{k} ({v.get('status')})" for k, v in sections.items()))
    refs = result.get("references") or []
    lines.append(f"references: {len(refs)}")
    return lines


def cmd_check(args: argparse.Namespace) -> int:
    engine = args.engine or (os.environ.get("PROPOSAL_ENGINE") or "agentic").strip().lower()
    print(f"engine: {engine}")
    for name, ok in model_keys().items():
        print(f"  {name} key: {'set' if ok else 'not set'}")
    offline = (os.environ.get("PUBMED_OFFLINE") or "").strip().lower() in {"1", "true", "yes", "on"}
    print(f"PubMed: {'offline (saved records)' if offline else 'live'}"
          + ("" if offline or has("NCBI_EMAIL") else "  — set NCBI_EMAIL (and ideally NCBI_API_KEY) for live use"))
    problem = config_problem(engine)
    print(problem or "ready.")
    return 2 if problem else 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.fake:
        os.environ.update(LLM_PROVIDER="fake", PUBMED_OFFLINE="1")
        engine = "pipeline"   # the agentic engine needs a real model; the fake model drives the pipeline
    else:
        engine = args.engine or (os.environ.get("PROPOSAL_ENGINE") or "agentic").strip().lower()
    if engine not in ENGINES:
        print(f"unknown engine {engine!r}; choose one of {', '.join(ENGINES)}", file=sys.stderr)
        return 2
    problem = config_problem(engine)
    if problem:
        print(problem, file=sys.stderr)
        return 2

    from engine.docx_export import build_docx
    from engine.knowledge import get_knowledge
    from engine.llm import make_llm
    from engine.pipeline import RunOptions, run
    from engine.pubmed import make_pubmed
    from engine.schemas import SECTION_KEYS, ProposalRequest

    request = ProposalRequest(
        title=args.title, purpose=args.purpose, design=args.design, language=args.language,
        sections=[s.strip() for s in args.sections.split(",") if s.strip()] or list(SECTION_KEYS),
    )
    knowledge = get_knowledge()
    store = PrintingStore(quiet=args.quiet)
    env = dict(os.environ, PROPOSAL_ENGINE=engine)
    print(f"Drafting with the {engine} engine{' (fake mode: placeholder text)' if args.fake else ''}…", flush=True)
    started = time.monotonic()
    # the agentic engine builds its own models from the environment; the pipeline's client is then only a placeholder
    llm = make_llm(knowledge, dict(env, LLM_PROVIDER="fake") if engine == "agentic" else env)
    result = run(request, store, llm, make_pubmed(), datetime.now(UTC), RunOptions.from_env(),
                 knowledge=knowledge, engine_env=env)
    seconds = time.monotonic() - started

    out = Path(args.out) if args.out else ROOT / "output" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug(args.title)}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    trace = {"request": request.model_dump(exclude={"statpearls_html"}), "engine": engine, "seconds": round(seconds, 1),
             "steps": store.steps, "events": store.events, "searches": store.searches, "warnings": store.warnings,
             "calls": [{k: v for k, v in c.items() if k != "messages"} for c in store.calls]}
    (out / "trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    docx = out / "proposal.docx"
    try:
        docx.write_bytes(build_docx(result))
    except Exception as exc:   # a failed run may have nothing to export; the JSON files still explain why
        docx = None
        print(f"(no .docx: {exc})", file=sys.stderr)

    print()
    for line in summary(result, seconds):
        print(line)
    print(f"output: {out}" + (f"\n  {docx.name}, result.json, trace.json" if docx else "\n  result.json, trace.json"))
    print("This is a draft. You are the author: check every sentence, number and reference.")
    return 0 if result.get("status") in {"done", "done_with_errors"} else 1


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="proposal-agent", description="Draft a medical research proposal from a title.")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="show which engine and keys are configured (never prints a key)")
    c.add_argument("--engine", choices=ENGINES)
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("run", help="draft a proposal for one title")
    r.add_argument("title", help="the study title (Persian or English)")
    r.add_argument("--purpose", default="", help="the study's aim, if you want to state it")
    r.add_argument("--design", default="", help="force a design, e.g. cross_sectional (default: the agent chooses)")
    r.add_argument("--language", default="fa", choices=["fa", "en"], help="language of the proposal (default fa)")
    r.add_argument("--sections", default="", help="comma-separated subset, e.g. aims,methods (default: all)")
    r.add_argument("--engine", choices=ENGINES, help="agentic (default) or pipeline; or set PROPOSAL_ENGINE")
    r.add_argument("--fake", action="store_true", help="no keys, no network: fake model + saved PubMed records")
    r.add_argument("--out", default="", help="output folder (default: output/<time>-<title>/)")
    r.add_argument("--quiet", action="store_true", help="print only the summary")
    r.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
