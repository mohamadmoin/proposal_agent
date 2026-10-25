"""Database tables: the proposal (job + results), its papers, references, model calls and progress log."""

import uuid

from django.db import models


class Proposal(models.Model):
    """One request and everything the app made for it. Also the job row the worker picks up."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        DONE = "done"
        DONE_WITH_ERRORS = "done_with_errors"
        FAILED = "failed"

    FINISHED = (Status.DONE, Status.DONE_WITH_ERRORS, Status.FAILED)

    # identity and request
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_ref = models.CharField(max_length=100, blank=True, db_index=True)
    title = models.CharField(max_length=300)
    purpose = models.TextField(blank=True)
    design_input = models.CharField(max_length=100, blank=True)
    design_other_name = models.CharField(max_length=100, blank=True)
    language = models.CharField(max_length=2, choices=[("fa", "فارسی"), ("en", "English")], default="fa")
    sections = models.JSONField(default=list)
    uploaded_html = models.TextField(blank=True)

    # job state
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    current_step = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    # results
    profile = models.JSONField(null=True, blank=True)
    design_decision = models.JSONField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    usage = models.JSONField(default=dict, blank=True)
    searches = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self) -> str:
        return f"{self.title[:60]} ({self.status})"

    @property
    def is_finished(self) -> bool:
        return self.status in self.FINISHED


class Paper(models.Model):
    """A PubMed record met while making the proposal, with its lanes, scores, screening and selection."""

    class Role(models.TextChoices):
        BACKGROUND = "background"
        METHODS = "methods"
        INTRO = "intro"

    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, related_name="papers")
    role = models.CharField(max_length=20, choices=Role.choices)
    token = models.CharField(max_length=10, blank=True)
    pmid = models.CharField(max_length=20, blank=True, db_index=True)
    doi = models.CharField(max_length=255, blank=True)
    pmcid = models.CharField(max_length=20, blank=True)
    title = models.TextField(blank=True)
    abstract = models.TextField(blank=True)
    journal = models.CharField(max_length=300, blank=True)
    year = models.IntegerField(null=True, blank=True)
    authors = models.JSONField(default=list, blank=True)
    publication_types = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)  # volume, issue, pages, book fields, MeSH
    lanes = models.JSONField(default=list, blank=True)
    scores = models.JSONField(default=dict, blank=True)
    screening = models.JSONField(null=True, blank=True)
    stage = models.CharField(max_length=20, blank=True)  # retrieved | screened | selected
    selected = models.BooleanField(default=False)
    paragraph = models.TextField(blank=True)
    flags = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["role", "token", "id"]

    def __str__(self) -> str:
        return f"{self.role} {self.token or self.pmid}"


class Reference(models.Model):
    """One entry of the final numbered reference list."""

    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, related_name="references")
    number = models.PositiveIntegerField()
    token = models.CharField(max_length=100, blank=True)  # the tokens that point to it, e.g. "P1, M3"
    text = models.TextField()
    pmid = models.CharField(max_length=20, blank=True)
    url = models.URLField(max_length=300, blank=True)

    class Meta:
        ordering = ["number"]

    def __str__(self) -> str:
        return f"[{self.number}] {self.text[:60]}"


class LLMCall(models.Model):
    """One model call: what was sent, what came back, tokens, latency and status."""

    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, related_name="llm_calls")
    step = models.CharField(max_length=100)
    tier = models.CharField(max_length=10, blank=True)
    model = models.CharField(max_length=100, blank=True)
    messages = models.JSONField(default=list)
    response = models.TextField(blank=True)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    status = models.CharField(max_length=20, default="ok")
    error = models.TextField(blank=True)
    attempt = models.IntegerField(default=1)
    cost_usd = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "LLM call"

    def __str__(self) -> str:
        return f"{self.step} ({self.status})"


class StepLog(models.Model):
    """A progress event shown to the user (step started/done/failed, warnings)."""

    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, related_name="step_logs")
    step = models.CharField(max_length=100)
    status = models.CharField(max_length=20)  # started | done | failed | warning
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.step}: {self.status}"
