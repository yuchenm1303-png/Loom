-- DESIGN PROTOTYPE ONLY: isolated SQLite database, not a production migration.
-- Application commands must validate transitions, DAG cycles, evidence and CAS.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE project_runner_schema_versions (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL
);
INSERT INTO project_runner_schema_versions VALUES (1, 'Design prototype 2026-10-25');

CREATE TABLE project_runs (
    id TEXT PRIMARY KEY,
    workspace_project_id TEXT NOT NULL,
    objective TEXT NOT NULL CHECK (length(trim(objective)) > 0),
    execution_state TEXT NOT NULL CHECK (execution_state IN
        ('created','planning','running','verifying','completed','failed','cancelled')),
    control_state TEXT NOT NULL DEFAULT 'active' CHECK (control_state IN
        ('active','paused','budget_limited')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    spec_revision INTEGER NOT NULL DEFAULT 1 CHECK (spec_revision > 0),
    control_revision INTEGER NOT NULL DEFAULT 1 CHECK (control_revision > 0),
    policy_json TEXT NOT NULL CHECK (json_valid(policy_json)),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE project_tasks (
    project_id TEXT NOT NULL REFERENCES project_runs(id),
    id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    spec_revision INTEGER NOT NULL CHECK (spec_revision > 0),
    kind TEXT NOT NULL DEFAULT 'implementation' CHECK (kind IN
        ('planning','implementation','diagnosis','verification','integration')),
    state TEXT NOT NULL CHECK (state IN ('pending','ready','running','candidate',
        'reviewing','integrating','done','blocked','failed','superseded')),
    contract_json TEXT NOT NULL CHECK (json_valid(contract_json)),
    epoch INTEGER NOT NULL DEFAULT 0 CHECK (epoch >= 0),
    ready_at_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, id)
);

CREATE TABLE project_dependencies (
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    prerequisite_id TEXT NOT NULL,
    PRIMARY KEY (project_id, task_id, prerequisite_id),
    CHECK (task_id <> prerequisite_id),
    FOREIGN KEY (project_id, task_id) REFERENCES project_tasks(project_id, id),
    FOREIGN KEY (project_id, prerequisite_id) REFERENCES project_tasks(project_id, id)
);

CREATE TABLE project_attempts (
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    id TEXT NOT NULL,
    task_revision INTEGER NOT NULL CHECK (task_revision > 0),
    spec_revision INTEGER NOT NULL CHECK (spec_revision > 0),
    control_revision INTEGER NOT NULL CHECK (control_revision > 0),
    epoch INTEGER NOT NULL CHECK (epoch > 0),
    state TEXT NOT NULL CHECK (state IN ('preparing','active','waiting_approval',
        'candidate','failed','cancelled','outcome_unknown','superseded')),
    base_commit TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    candidate_commit TEXT,
    lease_owner TEXT,
    lease_expires_at_ms INTEGER,
    PRIMARY KEY (project_id, id),
    UNIQUE (project_id, task_id, epoch),
    FOREIGN KEY (project_id, task_id) REFERENCES project_tasks(project_id, id)
);

-- An unknown result prevents an automatic replacement until reconciliation.
CREATE UNIQUE INDEX project_one_open_attempt ON project_attempts(project_id, task_id)
    WHERE state IN ('preparing','active','waiting_approval','outcome_unknown');

CREATE TABLE project_episodes (
    project_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    id TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('prepared','dispatching','starting','running',
        'waiting_approval','ended','failed','interrupted','outcome_unknown')),
    outcome_json TEXT CHECK (outcome_json IS NULL OR json_valid(outcome_json)),
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id, attempt_id) REFERENCES project_attempts(project_id, id)
);

CREATE UNIQUE INDEX project_one_open_episode ON project_episodes(session_id)
    WHERE state IN ('prepared','dispatching','starting','running','waiting_approval','outcome_unknown');
CREATE UNIQUE INDEX project_turn_identity ON project_episodes(session_id, turn_id)
    WHERE turn_id IS NOT NULL;

CREATE TABLE project_commands (
    project_id TEXT NOT NULL REFERENCES project_runs(id),
    operation_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL CHECK (json_valid(response_json)),
    PRIMARY KEY (project_id, operation_id)
);

CREATE TABLE project_events (
    project_id TEXT NOT NULL REFERENCES project_runs(id),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    event_id TEXT NOT NULL UNIQUE,
    event_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (project_id, sequence)
);

CREATE TABLE project_messages (
    project_id TEXT NOT NULL REFERENCES project_runs(id),
    direction TEXT NOT NULL CHECK (direction IN ('inbox','outbox')),
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','claimed','acknowledged','needs_reconciliation')),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    next_attempt_at_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, direction, operation_id)
);

CREATE TABLE project_requirements (
    project_id TEXT NOT NULL REFERENCES project_runs(id),
    id TEXT NOT NULL,
    spec_revision INTEGER NOT NULL CHECK (spec_revision > 0),
    required INTEGER NOT NULL CHECK (required IN (0,1)),
    definition_hash TEXT NOT NULL,
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json)),
    PRIMARY KEY (project_id, id, spec_revision)
);

CREATE TABLE project_assessments (
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    spec_revision INTEGER NOT NULL,
    subject_commit TEXT NOT NULL,
    subject_tree TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    environment_hash TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('passed','failed','blocked')),
    source TEXT NOT NULL CHECK (source IN ('executed_check','independent_review','human_review')),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id, requirement_id, spec_revision)
        REFERENCES project_requirements(project_id, id, spec_revision)
);

CREATE TABLE project_usage_reservations (
    project_id TEXT NOT NULL REFERENCES project_runs(id),
    request_id TEXT NOT NULL,
    reserved_tokens INTEGER NOT NULL CHECK (reserved_tokens >= 0),
    actual_tokens INTEGER CHECK (actual_tokens IS NULL OR actual_tokens >= 0),
    state TEXT NOT NULL CHECK (state IN ('reserved','settled','released','unknown')),
    attribution_json TEXT NOT NULL CHECK (json_valid(attribution_json)),
    PRIMARY KEY (project_id, request_id),
    CHECK (state <> 'settled' OR actual_tokens IS NOT NULL)
);

CREATE INDEX project_ready_tasks ON project_tasks(project_id, state, ready_at_ms);
CREATE INDEX project_messages_due ON project_messages(direction, state, next_attempt_at_ms);
-- Readiness, acceptance freshness, command authorization, transition guards,
-- cross-resource compensation and total-budget reservation belong to the service.
-- These constraints alone cannot certify project completion.
