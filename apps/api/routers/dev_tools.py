"""
OpsLens AI — Demo / Dev Tools
==============================
Endpoints for triggering REAL errors and forcing log syncs so you can
demo the full alert → RAG → RRT brief pipeline without waiting for the
scheduler or manufacturing artificial conditions.

All endpoints require admin auth. Do NOT expose in production without
restricting access — these intentionally generate noisy logs.

Endpoints:
    POST /dev/crash        — Write a real traceback to stdout N times so
                             Railway captures it; OpsLens polls and alerts.
    POST /dev/force-sync   — Immediately sync a Railway (or any) integration
                             without waiting for the 5-minute poll cycle.
    POST /dev/scenario     — Run a named demo scenario that executes REAL buggy
                             code paths, captures the live Python traceback, and
                             logs it N times so OpsLens can detect and alert.
                             GitHub code context will find the exact source lines.
    GET  /dev/scenarios    — List all available demo scenarios with descriptions.
"""
from __future__ import annotations

import asyncio
import traceback
import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_admin
from ..db.session import get_db
from ..utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ── Demo scenarios — real buggy code paths ────────────────────────────────────

def _scenario_alert_rule_null_condition() -> str:
    """
    Simulates a bug in the alerts module: an AlertRule is loaded from DB
    with a None conditions list (data-migration gap), and the evaluator
    tries to iterate it → TypeError deep in the rule evaluation stack.
    """
    from ..models.alert import AlertRule

    # Simulate a rule object with a None conditions field (corrupt DB row)
    corrupt_rule = AlertRule.__new__(AlertRule)
    object.__setattr__(corrupt_rule, "id", uuid.uuid4())
    object.__setattr__(corrupt_rule, "name", "P0 Payment Failures")
    object.__setattr__(corrupt_rule, "conditions", None)   # <-- the bug

    def evaluate_conditions(rule: AlertRule) -> bool:
        """Inner evaluator — crashes on None conditions."""
        for condition in rule.conditions:                  # <-- TypeError here
            field = condition.get("field")
            if condition.get("operator") == "gt":
                return float(condition.get("value", 0)) > 0
        return False

    def dispatch_alert_rule(rule: AlertRule) -> None:
        """Alert dispatcher — calls evaluator."""
        result = evaluate_conditions(rule)
        if result:
            logger.info("Alert rule '%s' triggered", rule.name)

    dispatch_alert_rule(corrupt_rule)
    return ""  # never reached


def _scenario_qdrant_embedding_mismatch() -> str:
    """
    Simulates a dimension mismatch when storing a vector in Qdrant:
    the embedding model was swapped from 1536-dim to 3072-dim but the
    collection was not recreated → VectorStoreError on upsert.
    """
    import numpy as np

    EXPECTED_DIM = 1536
    ACTUAL_DIM   = 3072   # new model, collection not migrated

    class VectorStoreError(RuntimeError):
        pass

    def _validate_vector_dimensions(vec: list[float], collection: str) -> None:
        if len(vec) != EXPECTED_DIM:
            raise VectorStoreError(
                f"Vector dimension mismatch in collection '{collection}': "
                f"expected {EXPECTED_DIM} got {len(vec)}. "
                f"Re-create the collection or downgrade the embedding model."
            )

    def embed_and_store(text: str, collection: str = "opslens_tenant_prod") -> None:
        # Simulate embedding with wrong-dimension model
        fake_vector = list(np.random.randn(ACTUAL_DIM).astype(float))
        _validate_vector_dimensions(fake_vector, collection)
        # upsert would happen here
        logger.info("Stored vector for: %s", text[:40])

    def ingest_rrt_brief_embedding(brief_id: str) -> None:
        text = f"RRT Brief {brief_id}: Payment gateway timeout spike detected in prod"
        embed_and_store(text)

    ingest_rrt_brief_embedding(str(uuid.uuid4())[:8])
    return ""


def _scenario_jira_sync_token_expired() -> str:
    """
    Simulates a Jira sync failure: the stored API token was rotated in
    Atlassian but not updated in OpsLens — every sync call gets a 401.
    The sync worker retries 3 times then raises a persistent auth error.
    """
    import httpx

    class JiraAuthError(PermissionError):
        pass

    class JiraSyncWorker:
        def __init__(self, server_url: str, email: str, token: str):
            self.server_url = server_url
            self.email      = email
            self.token      = token
            self._retry_count = 0

        def _fetch_projects(self) -> list[dict]:
            # Simulate expired token → always 401
            raise httpx.HTTPStatusError(
                "401 Unauthorized",
                request=httpx.Request("GET", f"{self.server_url}/rest/api/3/project"),
                response=httpx.Response(401),
            )

        def sync_with_retry(self, max_retries: int = 3) -> list[dict]:
            for attempt in range(1, max_retries + 1):
                try:
                    return self._fetch_projects()
                except httpx.HTTPStatusError as exc:
                    self._retry_count += 1
                    if exc.response.status_code == 401:
                        if attempt == max_retries:
                            raise JiraAuthError(
                                f"Jira authentication failed after {max_retries} retries. "
                                f"API token for {self.email} may have expired or been revoked. "
                                f"Update credentials at Settings → Integrations → Jira."
                            ) from exc
            return []

    worker = JiraSyncWorker(
        server_url="https://acme-corp.atlassian.net",
        email="ops-bot@acme.com",
        token="ATATT3xFfGF0_EXPIRED_TOKEN",
    )
    worker.sync_with_retry()
    return ""


def _scenario_rrt_brief_llm_timeout() -> str:
    """
    Simulates the RRT brief generator timing out when the LLM backend is
    slow under high load — the Celery task raises a socket timeout and the
    brief is never saved, leaving the incident unresolved.
    """
    import socket

    class LLMGatewayError(TimeoutError):
        pass

    class RRTBriefGenerator:
        MODEL    = "gpt-4o"
        TIMEOUT  = 30  # seconds

        def _call_llm_api(self, prompt: str) -> dict:
            raise socket.timeout(
                f"[Errno 110] Connection timed out after {self.TIMEOUT}s "
                f"while calling {self.MODEL} — LLM gateway overloaded"
            )

        def _build_prompt(self, error_log: str, context_docs: list[str]) -> str:
            return (
                f"You are an SRE. Given the following error and context, "
                f"generate a structured RRT brief.\n\nError:\n{error_log}\n\n"
                f"Context:\n" + "\n---\n".join(context_docs[:3])
            )

        def generate(self, error_log: str, context_docs: list[str]) -> dict:
            prompt = self._build_prompt(error_log, context_docs)
            try:
                return self._call_llm_api(prompt)
            except socket.timeout as exc:
                raise LLMGatewayError(
                    f"RRT brief generation failed: LLM API timeout. "
                    f"Brief for incident will not be auto-generated. "
                    f"Retry or generate manually."
                ) from exc

    gen = RRTBriefGenerator()
    gen.generate(
        error_log="CRITICAL: payment-service pod OOMKilled — 847 requests dropped",
        context_docs=["Jira PAY-1234: High memory usage in payment pod", "Slack: #incidents — @oncall paged"],
    )
    return ""


def _scenario_db_migration_column_missing() -> str:
    """
    Simulates a failed migration in production: alembic ran but the
    'resolution_notes' column wasn't added due to a lock timeout, so
    every INSERT to rrt_briefs raises ProgrammingError: column does not exist.
    """
    class ProgrammingError(Exception):
        """sqlalchemy.exc.ProgrammingError stub."""

    class RRTBriefRepository:
        TABLE = "opslens.rrt_briefs"

        def _build_insert(self, data: dict) -> str:
            columns = ", ".join(data.keys())
            placeholders = ", ".join(f":{k}" for k in data.keys())
            return f"INSERT INTO {self.TABLE} ({columns}) VALUES ({placeholders})"

        def _execute(self, sql: str, params: dict) -> None:
            # Simulate column missing because migration 0007 failed to apply
            if "resolution_notes" in params:
                raise ProgrammingError(
                    f'column "resolution_notes" of relation "rrt_briefs" does not exist\n'
                    f'LINE 1: INSERT INTO opslens.rrt_briefs (..., resolution_notes) ...\n'
                    f'HINT: Run `alembic upgrade head` to apply pending migrations.'
                )

        def save(self, brief_data: dict) -> None:
            sql = self._build_insert(brief_data)
            self._execute(sql, brief_data)
            logger.info("Saved RRT brief: %s", brief_data.get("title"))

    repo = RRTBriefRepository()
    repo.save({
        "id":               str(uuid.uuid4()),
        "title":            "P1: Payment API timeout spike",
        "what_happened":    "payment-service latency spiked to 8s p99",
        "status":           "open",
        "resolution_notes": "",   # <-- triggers the bug
    })
    return ""


# Registry of all scenarios
_SCENARIOS: dict[str, tuple[str, str, callable]] = {
    "alert_rule_null_condition": (
        "Alert Rule: NullPointerError on conditions",
        "alerts.py evaluator crashes when a rule's conditions list is None (data migration gap).",
        _scenario_alert_rule_null_condition,
    ),
    "qdrant_embedding_mismatch": (
        "Vector Store: Embedding dimension mismatch",
        "Qdrant upsert fails after embedding model upgrade — collection not re-created (1536 vs 3072 dims).",
        _scenario_qdrant_embedding_mismatch,
    ),
    "jira_sync_token_expired": (
        "Jira Sync: API token expired",
        "Jira integration sync fails with 401 after token rotation — retries 3×, then raises JiraAuthError.",
        _scenario_jira_sync_token_expired,
    ),
    "rrt_brief_llm_timeout": (
        "RRT Brief Generator: LLM API timeout",
        "Brief generation times out under high load — socket.timeout from LLM gateway, brief never saved.",
        _scenario_rrt_brief_llm_timeout,
    ),
    "db_migration_column_missing": (
        "Database: column does not exist (missing migration)",
        "INSERT into rrt_briefs fails — migration 0007 applied on dev but not prod, column missing.",
        _scenario_db_migration_column_missing,
    ),
}


# ── /scenarios (list) ────────────────────────────────────────────────────────

class ScenarioInfo(BaseModel):
    id:          str
    title:       str
    description: str


@router.get("/scenarios", response_model=list[ScenarioInfo])
async def list_scenarios(
    ctx: Annotated[TenantContext, Depends(require_admin)],
):
    """Return all available demo scenarios with their IDs and descriptions."""
    return [
        ScenarioInfo(id=k, title=v[0], description=v[1])
        for k, v in _SCENARIOS.items()
    ]


# ── /scenario (run) ──────────────────────────────────────────────────────────

class ScenarioRequest(BaseModel):
    scenario: str = Field(
        default="alert_rule_null_condition",
        description=(
            "Which demo scenario to run. "
            "Call GET /dev/scenarios for the full list. "
            "Available: alert_rule_null_condition, qdrant_embedding_mismatch, "
            "jira_sync_token_expired, rrt_brief_llm_timeout, db_migration_column_missing"
        ),
    )
    repeat: int = Field(
        default=8,
        ge=1,
        le=50,
        description="How many times to log the error. Must be > LOG_FAST_ALERT_THRESHOLD (default 5).",
    )


class ScenarioResponse(BaseModel):
    scenario:    str
    title:       str
    logged:      int
    error_type:  str
    next_step:   str


@router.post("/scenario", response_model=ScenarioResponse, status_code=202)
async def run_scenario(
    body: ScenarioRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
):
    """
    Run a named demo scenario that executes **real Python code** designed to fail,
    captures the live traceback, and writes it to stdout ``repeat`` times.

    Unlike /crash (which logs a hand-crafted string), these scenarios run actual
    buggy functions in your codebase — so the tracebacks reference real file paths
    (routers/dev_tools.py) and real function names.  GitHub code context will find
    and display the exact failing lines when OpsLens generates the RRT brief.

    Workflow:
      1. POST /dev/scenario  {"scenario": "alert_rule_null_condition", "repeat": 8}
      2. POST /dev/force-sync {"source_type": "railway"}
      3. Watch Slack + /rrt-briefs for the auto-generated brief (< 60 s)
    """
    entry = _SCENARIOS.get(body.scenario)
    if not entry:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown scenario '{body.scenario}'. "
                f"Valid choices: {', '.join(_SCENARIOS)}"
            ),
        )

    title, description, fn = entry

    # Execute the buggy function and capture the REAL Python traceback
    real_tb = ""
    error_type = "UnknownError"
    try:
        fn()
        # If we reach here the scenario didn't raise — shouldn't happen
        logger.warning("Scenario '%s' completed without error — check implementation", body.scenario)
        real_tb = f"WARNING: scenario '{body.scenario}' did not raise an exception."
    except Exception as exc:
        real_tb   = traceback.format_exc()
        error_type = type(exc).__name__

    # Log the real traceback N times so Railway captures them in deployment logs
    header = f"[DEMO:{body.scenario}] {error_type}: {title}"
    for i in range(body.repeat):
        logger.error("%s\n%s", header, real_tb)
        await asyncio.sleep(0.1)

    logger.error(
        "DEMO: scenario '%s' fired %d times — waiting for Railway log propagation before sync",
        body.scenario, body.repeat,
    )

    # Railway's log API has a propagation delay — stdout lines written now
    # don't appear in deploymentLogs until ~10–20 s later.  We wait here so
    # that force-sync (called immediately by the frontend) sees the fresh lines.
    await asyncio.sleep(20)

    logger.info("DEMO: Railway log propagation wait complete — ready to sync")

    return ScenarioResponse(
        scenario=body.scenario,
        title=title,
        logged=body.repeat,
        error_type=error_type,
        next_step=(
            "Call POST /api/v1/dev/force-sync {\"source_type\": \"railway\"} "
            "to ingest these logs immediately and trigger the RRT brief pipeline."
        ),
    )


# ── /crash ────────────────────────────────────────────────────────────────────

class CrashRequest(BaseModel):
    service_name: str = Field(
        default="payments-api",
        description="Service label that appears in the log line (cosmetic only).",
    )
    error_message: str = Field(
        default="CRITICAL: Database connection pool exhausted — all 20 connections in use",
        description="The error text to log. Should match keywords in your Jira ticket.",
    )
    repeat: int = Field(
        default=8,
        ge=1,
        le=50,
        description="How many times to log the error. Must exceed LOG_FAST_ALERT_THRESHOLD (default 5).",
    )


class CrashResponse(BaseModel):
    logged: int
    error_message: str
    next_step: str


@router.post("/crash", response_model=CrashResponse, status_code=202)
async def trigger_crash(
    body: CrashRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
):
    """
    Writes a realistic Python traceback to stdout **repeat** times so Railway
    captures it as real logs.

    After calling this:
    1. Call POST /dev/force-sync to pull the logs immediately, OR
    2. Wait ~5 minutes for the scheduler to pick them up automatically.

    OpsLens will detect the error spike, query Qdrant for related Jira tickets,
    and fire a Slack alert + generate an RRT brief.
    """
    # Build a realistic traceback that matches _CRITICAL_RE and shows up clearly
    fake_tb = (
        f"Traceback (most recent call last):\n"
        f'  File "/app/{body.service_name}/handlers.py", line 87, in handle_request\n'
        f'    result = await db_pool.acquire(timeout=5.0)\n'
        f'  File "/app/core/db.py", line 134, in acquire\n'
        f'    raise PoolExhaustedError(msg)\n'
        f"PoolExhaustedError: {body.error_message}"
    )

    for i in range(body.repeat):
        # Use ERROR level so it matches _CRITICAL_RE in fast_scan / _trigger_log_source_incidents
        logger.error(
            "[%s] %s\n%s",
            body.service_name,
            body.error_message,
            fake_tb,
        )
        # Small stagger so lines have distinct timestamps in Railway
        await asyncio.sleep(0.05)

    logger.error(
        "DEMO: %d error(s) logged for service '%s' — trigger a sync or wait for scheduler",
        body.repeat, body.service_name,
    )

    return CrashResponse(
        logged=body.repeat,
        error_message=body.error_message,
        next_step="Call POST /api/v1/dev/force-sync to pull these logs into OpsLens immediately.",
    )


# ── /force-sync ───────────────────────────────────────────────────────────────

class ForceSyncRequest(BaseModel):
    source_type: str = Field(
        default="railway",
        description="Integration source type to sync: 'railway', 'datadog', 'elasticsearch', etc.",
    )


class ForceSyncResponse(BaseModel):
    synced: bool
    integration_id: str | None
    source_type: str
    records_added: int | None
    message: str


@router.post("/force-sync", response_model=ForceSyncResponse, status_code=202)
async def force_sync(
    body: ForceSyncRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Immediately syncs the named integration for this tenant, bypassing the
    5-minute scheduler cycle.

    After syncing, newly captured error logs are processed through
    _trigger_log_source_incidents → enrich_and_alert → RRT brief pipeline
    in real time.

    Use after POST /dev/crash to close the demo loop in seconds rather than
    waiting for the next poll cycle.
    """
    from ..db.models import Integration
    from ..utils.crypto import decrypt_credentials
    from ..services.direct_sync_service import run_direct_sync

    # Find the active integration for this tenant + source_type
    result = await db.execute(
        sa.select(Integration).where(
            Integration.tenant_id == ctx.tenant_uuid,
            Integration.source_type == body.source_type,
            Integration.status == "active",
        ).limit(1)
    )
    integration: Integration | None = result.scalar_one_or_none()

    if not integration:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No active '{body.source_type}' integration found for your account. "
                f"Connect it first via Integrations page."
            ),
        )

    integration_id = str(integration.id)
    tenant_id = str(ctx.tenant_uuid)

    logger.info(
        "Force sync triggered: source=%s integration=%s tenant=%s",
        body.source_type, integration_id[:8], tenant_id,
    )

    try:
        records_added = await run_direct_sync(integration_id, tenant_id)
        return ForceSyncResponse(
            synced=True,
            integration_id=integration_id,
            source_type=body.source_type,
            records_added=records_added if isinstance(records_added, int) else None,
            message=(
                f"Sync complete. New records ingested and scanned for incidents. "
                f"Check Slack and /rrt-briefs in ~30 seconds."
            ),
        )
    except Exception as exc:
        logger.error("Force sync failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Sync failed: {exc}",
        )


# ── GitHub token diagnostic ───────────────────────────────────────────────────

@router.get("/github-check")
async def github_token_check(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Diagnose the GitHub integration token for this tenant.
    Returns: token validity, scopes, accessible repos, and whether the
    demo file (apps/api/routers/dev_tools.py) can be read.
    """
    import httpx
    from ..db.models import Integration
    from ..utils.crypto import decrypt_credentials

    result = await db.execute(
        sa.select(Integration).where(
            Integration.tenant_id == ctx.tenant_uuid,
            Integration.source_type == "github",
            Integration.status == "active",
        ).limit(1)
    )
    intg = result.scalar_one_or_none()

    if not intg:
        return {
            "status": "no_integration",
            "message": "No active GitHub integration found. Connect GitHub via Integrations page.",
            "token_valid": False,
        }

    creds = decrypt_credentials(intg.credentials)
    token = creds.get("access_token", "")

    if not token:
        return {
            "status": "no_token",
            "message": "Integration exists but access_token is empty. Reconnect GitHub.",
            "token_valid": False,
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(headers=headers, timeout=10) as client:

        # 1. Validate token and check scopes
        user_resp = await client.get("https://api.github.com/user")
        if not user_resp.is_success:
            return {
                "status": "token_invalid",
                "message": f"GitHub rejected the token (HTTP {user_resp.status_code}). Generate a new PAT with 'repo' scope.",
                "token_valid": False,
                "http_status": user_resp.status_code,
            }

        user_data = user_resp.json()
        scopes_header = user_resp.headers.get("X-OAuth-Scopes", "")
        scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
        has_repo_scope = "repo" in scopes
        has_public_repo = "public_repo" in scopes

        # 2. List repos
        repos_resp = await client.get(
            "https://api.github.com/user/repos",
            params={"per_page": 50, "sort": "updated", "affiliation": "owner,collaborator"},
        )
        repos = repos_resp.json() if repos_resp.is_success else []
        repo_names = [r["full_name"] for r in repos if isinstance(r, dict)]

        # 3. Try to read the demo file from each repo
        test_path = "apps/api/routers/dev_tools.py"
        file_found_in = None
        file_http_status = None
        for full_name in repo_names:
            file_resp = await client.get(
                f"https://api.github.com/repos/{full_name}/contents/{test_path}",
            )
            file_http_status = file_resp.status_code
            if file_resp.is_success:
                file_found_in = full_name
                break

    # Determine overall verdict
    if not scopes:
        scope_warning = "No OAuth scopes detected — token may be a fine-grained PAT. Fine-grained PATs work differently; use a classic PAT with 'repo' scope for best compatibility."
    elif not has_repo_scope and not has_public_repo:
        scope_warning = f"Token scopes [{scopes_header}] do not include 'repo' or 'public_repo'. Private repo files will return 404. Add 'repo' scope."
    elif not has_repo_scope and has_public_repo:
        scope_warning = "Token has 'public_repo' but NOT 'repo'. This works for public repos only. If opslens-ai is private, upgrade to 'repo' scope."
    else:
        scope_warning = None

    return {
        "status": "ok" if file_found_in else "file_not_found",
        "token_valid": True,
        "github_user": user_data.get("login"),
        "scopes": scopes,
        "scope_warning": scope_warning,
        "has_repo_scope": has_repo_scope,
        "accessible_repos": repo_names[:10],
        "repo_count": len(repo_names),
        "test_file": test_path,
        "file_found_in_repo": file_found_in,
        "file_http_status": file_http_status,
        "verdict": (
            f"✅ Token works — found '{test_path}' in '{file_found_in}'. Code context should appear in briefs."
            if file_found_in
            else (
                f"⚠️ Token is valid and sees {len(repo_names)} repo(s), but '{test_path}' not found "
                f"(last HTTP status: {file_http_status}). "
                + (scope_warning or "Check the repo name and that the file path exists.")
            )
        ),
    }


# ── Code context pipeline test ────────────────────────────────────────────────

@router.get("/code-context-test")
async def code_context_test(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Directly runs the full code-context pipeline against the demo file.
    Fully async — no run_until_complete. Reports exactly where the chain breaks.
    """
    import os
    from ..db.models import Integration
    from ..utils.crypto import decrypt_credentials

    tenant_id = str(ctx.tenant_uuid)

    # ── 1. Check what paths exist in this container ───────────────────────────
    candidate_paths = [
        "/app/apps/api/routers/dev_tools.py",
        "/app/api/routers/dev_tools.py",
        "apps/api/routers/dev_tools.py",
        os.path.join(os.getcwd(), "apps/api/routers/dev_tools.py"),
        os.path.abspath(__file__),  # this very file's actual path
    ]
    path_check = {p: os.path.isfile(p) for p in candidate_paths}
    actual_file = os.path.abspath(__file__)
    cwd = os.getcwd()

    # ── 2. Check GitHub token decryptability ─────────────────────────────────
    decrypt_ok = False
    token_preview = "(not checked)"
    try:
        r = await db.execute(
            sa.select(Integration).where(
                Integration.tenant_id == ctx.tenant_uuid,
                Integration.source_type == "github",
                Integration.status == "active",
            ).limit(1)
        )
        intg = r.scalar_one_or_none()
        if not intg:
            token_preview = "no active GitHub integration in DB"
        else:
            creds = decrypt_credentials(intg.credentials)
            tok = creds.get("access_token", "")
            if tok:
                decrypt_ok = True
                token_preview = f"{tok[:8]}..."
            else:
                token_preview = "decryption returned empty token (wrong CREDENTIAL_ENCRYPTION_KEY?)"
    except Exception as exc:
        token_preview = f"exception: {exc}"

    # ── 3. Parse traceback frames ─────────────────────────────────────────────
    sample_lines = [
        "[ERROR] [DEMO:alert_rule_null_condition] TypeError: 'NoneType' is not iterable",
        "Traceback (most recent call last):",
        f'  File "{actual_file}", line 72, in _scenario_alert_rule_null_condition',
        "    for condition in rule.conditions:",
        "TypeError: 'NoneType' object is not iterable",
    ]
    try:
        from apps.worker.tasks.rrt_briefing import _parse_traceback_frames, _fetch_code_context
        parsed_frames = _parse_traceback_frames(sample_lines)
    except Exception as exc:
        return {"error": f"import/parse failed: {exc}", "cwd": cwd, "actual_file": actual_file}

    # ── 4. Fetch code context (fully async — no run_until_complete) ───────────
    code_frames: list[dict] = []
    fetch_error = None
    if parsed_frames:
        try:
            code_frames = await _fetch_code_context(parsed_frames, tenant_id)
        except Exception as exc:
            fetch_error = str(exc)

    return {
        "cwd": cwd,
        "actual_file_path": actual_file,
        "path_check": path_check,
        "github_token_decryptable": decrypt_ok,
        "github_token_preview": token_preview,
        "sample_lines": sample_lines,
        "parsed_frames": parsed_frames,
        "frames_found": len(parsed_frames),
        "fetch_error": fetch_error,
        "code_frames_count": len(code_frames),
        "code_frames": [
            {
                "file": f["file"],
                "line": f["line"],
                "repo": f.get("repo"),
                "source": "github" if f.get("github_url") else "local",
                "snippet_lines": len(f.get("snippet", "").splitlines()),
                "snippet_preview": f.get("snippet", "")[:300],
            }
            for f in code_frames
        ],
    }


# ── Force-brief: bypass Railway logs, test worker code-fetch directly ─────────

@router.post("/force-brief")
async def force_brief(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Bypasses Railway log fetching entirely.
    Fires generate_rrt_brief directly with a hardcoded traceback that points
    to a real file in the repo. If the resulting brief has code_frames, the
    worker fetch works and the block extractor is the only problem to fix.
    If it still has no code_frames, the worker itself cannot fetch from GitHub.
    """
    import os
    import time

    # Use the actual path of this file so GitHub can definitely find it
    actual_path = os.path.abspath(__file__)  # /app/apps/api/routers/dev_tools.py
    # Strip /app/ prefix to get repo-relative path
    repo_relative = actual_path.lstrip("/").removeprefix("app/")  # apps/api/routers/dev_tools.py

    tenant_id = str(ctx.tenant_uuid)
    unique_sig = f"railway:force_brief_{int(time.time())}"

    error_group_dict = {
        "signature":    unique_sig,
        "first_line":   "[ERROR] TypeError: 'NoneType' object is not iterable — force-brief test",
        "count":        5,
        "sample_lines": [
            "[ERROR] [DEMO:force_brief] TypeError: 'NoneType' object is not iterable",
            "Traceback (most recent call last):",
            f'  File "{actual_path}", line 60, in evaluate_conditions',
            "    for condition in rule.conditions:",
            "TypeError: 'NoneType' object is not iterable",
        ],
        "source_label": "railway/api",
    }

    try:
        from apps.worker.tasks.log_fast_alert import enrich_and_alert  # type: ignore[import]
        enrich_and_alert.delay(
            tenant_id=tenant_id,
            error_group_dict=error_group_dict,
            webhook_url=None,
            routing_targets=None,
            error_count=5,
            window_minutes=5,
        )
        return {
            "fired": True,
            "signature": unique_sig,
            "sample_lines": error_group_dict["sample_lines"],
            "repo_relative_path": repo_relative,
            "message": (
                "Brief queued. Wait ~30s then click 'Inspect Briefs' — "
                "if code_frames > 0 the worker fetch works; if still 0 the worker cannot reach GitHub."
            ),
        }
    except Exception as exc:
        return {"fired": False, "error": str(exc)}


# ── Railway raw log diagnostic ───────────────────────────────────────────────

@router.get("/railway-raw")
async def railway_raw_logs(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Fetches the last deployment's Railway logs and shows:
    1. The first 30 raw lines as they appear after Railway API processing
    2. All blocks the incident extractor would build (showing if File lines are captured)
    3. How many lines each block contains

    Use this to diagnose whether Railway is delivering multi-line tracebacks
    as separate entries (with severity prefixes) or embedded in one entry.
    """
    import re as _re2
    import httpx
    from ..db.models import Integration
    from ..utils.crypto import decrypt_credentials

    # ── 1. Get Railway integration ────────────────────────────────────────────
    result = await db.execute(
        sa.select(Integration).where(
            Integration.tenant_id == str(ctx.tenant_uuid),
            Integration.source_type == "railway",
            Integration.status == "active",
        ).limit(1)
    )
    intg = result.scalar_one_or_none()
    if not intg:
        return {"error": "No active Railway integration found"}

    creds = decrypt_credentials(intg.credentials)
    api_token = creds.get("api_token", "")
    if not api_token:
        return {"error": "Railway integration has no api_token"}

    gql_url = "https://backboard.railway.app/graphql/v2"
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}

    # ── 2. Get first project/service/deployment ──────────────────────────────
    async with httpx.AsyncClient(headers=headers, timeout=20) as client:
        proj_resp = await client.post(gql_url, json={"query": """
            query { me { projects { edges { node { id name
                services { edges { node { id name
                    deployments(first: 1) { edges { node { id status } } }
                } } }
            } } } }
        """})
        if not proj_resp.is_success:
            return {"error": f"Railway projects query failed: {proj_resp.status_code}"}

        projects = (proj_resp.json().get("data", {}).get("me", {}).get("projects", {}).get("edges", []) or [])
        if not projects:
            return {"error": "No Railway projects found"}

        # find first deployment
        d_id = None
        s_name = None
        p_name = None
        for pe in projects:
            p = pe.get("node", {})
            p_name = p.get("name", "")
            for se in p.get("services", {}).get("edges", []):
                s = se.get("node", {})
                s_name = s.get("name", "")
                deps = s.get("deployments", {}).get("edges", [])
                if deps:
                    d_id = deps[0]["node"]["id"]
                    break
            if d_id:
                break

        if not d_id:
            return {"error": "No deployments found"}

        # ── 3. Fetch log lines ────────────────────────────────────────────────
        log_query = """
            query($deploymentId: String!) {
                deploymentLogs(deploymentId: $deploymentId) {
                    message severity timestamp
                }
            }
        """
        lresp = await client.post(gql_url, json={"query": log_query, "variables": {"deploymentId": d_id}})
        if not lresp.is_success:
            return {"error": f"Log fetch failed: {lresp.status_code}"}

        log_lines_raw = lresp.json().get("data", {}).get("deploymentLogs", []) or []

    # ── 4. Build all_lines same as _fetch_railway ────────────────────────────
    all_lines = []
    for entry in log_lines_raw:
        msg = (entry.get("message") or "").strip()
        sev = (entry.get("severity") or "").upper()
        if not msg:
            continue
        prefix = f"[{sev}] " if sev and sev != "UNSPECIFIED" else ""
        all_lines.append(f"{prefix}{msg}")

    tail_lines = all_lines[-500:]
    content = "\n".join(tail_lines)
    lines = content.splitlines()

    # ── 5. Sample lines (last 30 for context) ────────────────────────────────
    sample = lines[-30:] if len(lines) > 30 else lines

    # Show per-entry format for first few raw entries
    raw_entry_sample = []
    for entry in log_lines_raw[-20:]:
        msg = (entry.get("message") or "").strip()
        sev = (entry.get("severity") or "").upper()
        raw_entry_sample.append({
            "severity": sev,
            "message_len": len(msg),
            "message_has_newline": "\n" in msg,
            "message_preview": msg[:120].replace("\n", "\\n"),
        })

    # ── 6. Run block extractor on the full content ───────────────────────────
    from ..services.direct_sync_service import _INCIDENT_RE, _NOISE_RE

    blocks_found = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _INCIDENT_RE.search(line) and not _NOISE_RE.search(line):
            block = [line]
            j = i + 1
            while j < len(lines):
                cont = _re2.sub(r"^\[[A-Z]+\]\s*", "", lines[j])
                if not (
                    cont.startswith("  ")
                    or cont.startswith("\t")
                    or cont.startswith("Traceback (")
                    or cont.startswith("During handling of")
                    or cont.startswith("The above exception")
                ):
                    break
                block.append(lines[j])
                j += 1
            blocks_found.append({
                "trigger_line": line[:120],
                "block_size": len(block),
                "has_file_lines": any('File "' in b for b in block),
                "has_traceback": any("Traceback (" in b for b in block),
                "block_lines": block[:10],  # first 10 lines of block
            })
            i = j
        else:
            i += 1

    # Limit output
    blocks_found = blocks_found[-20:]  # last 20 incident matches

    return {
        "deployment_id": d_id,
        "project": p_name,
        "service": s_name,
        "total_log_entries_from_railway": len(log_lines_raw),
        "all_lines_count": len(all_lines),
        "lines_after_splitlines": len(lines),
        "raw_entry_sample_last_20": raw_entry_sample,
        "last_30_processed_lines": sample,
        "incident_blocks_found": len(blocks_found),
        "incident_blocks": blocks_found,
    }


# ── Latest RRT brief inspector ────────────────────────────────────────────────

@router.get("/latest-brief")
async def latest_brief_debug(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Fetches the most recent RRT brief from the DB and returns its raw diagnostic fields:
    - error_sample: the raw log lines passed to the brief generator
    - code_frames: what (if anything) was stored for source code context
    - error_signature: the dedup key
    - created_at: when it was generated

    Use this to diagnose why code context isn't appearing in briefs even though
    the /dev/code-context-test confirms the pipeline works in the API process.
    """
    from ..models.rrt_brief import RRTBrief

    tenant_id_str = str(ctx.tenant_uuid)

    result = await db.execute(
        sa.select(RRTBrief)
        .where(RRTBrief.tenant_id == tenant_id_str)
        .order_by(RRTBrief.created_at.desc())
        .limit(5)
    )
    briefs = result.scalars().all()

    if not briefs:
        return {
            "found": False,
            "message": "No RRT briefs found for this tenant. Run POST /dev/scenario + POST /dev/force-sync to generate one.",
        }

    def _analyze_brief(brief):
        error_sample_lines = []
        if brief.error_sample:
            if isinstance(brief.error_sample, list):
                error_sample_lines = brief.error_sample
            elif isinstance(brief.error_sample, str):
                error_sample_lines = brief.error_sample.splitlines()

        # Also check for File "..." lines even when prefixed with [ERROR]
        file_lines = [l for l in error_sample_lines if 'File "' in l]
        traceback_lines = [l for l in error_sample_lines if "Traceback" in l]

        try:
            from apps.worker.tasks.rrt_briefing import _parse_traceback_frames
            would_parse = _parse_traceback_frames(error_sample_lines)
        except Exception as exc:
            would_parse = []

        code_frames = brief.code_frames or []

        return {
            "brief_id": str(brief.id),
            "created_at": brief.created_at.isoformat() if brief.created_at else None,
            "error_signature": brief.error_signature,
            "error_sample_line_count": len(error_sample_lines),
            "error_sample_has_traceback": len(traceback_lines) > 0,
            "error_sample_has_file_lines": len(file_lines) > 0,
            "error_sample_file_lines": file_lines[:5],
            "error_sample_first_10_lines": error_sample_lines[:10],
            "would_parse_frame_count": len(would_parse),
            "code_frames_stored": len(code_frames),
            "diagnosis": (
                "✅ code_frames populated — brief has source code"
                if code_frames
                else (
                    "❌ no File lines in error_sample — block extractor not capturing traceback"
                    if not file_lines
                    else "❌ File lines present but code_frames empty — worker fetch failed (check worker logs)"
                )
            ),
        }

    analyses = [_analyze_brief(b) for b in briefs]
    any_with_file_lines = any(a["error_sample_has_file_lines"] for a in analyses)
    any_with_frames = any(a["code_frames_stored"] > 0 for a in analyses)

    return {
        "found": True,
        "brief_count": len(analyses),
        "any_with_file_lines": any_with_file_lines,
        "any_with_code_frames": any_with_frames,
        "briefs": analyses,
    }
