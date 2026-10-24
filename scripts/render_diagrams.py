"""Generate Mermaid (.mmd) and rendered SVG (.svg) architecture diagrams in docs/diagrams/."""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BoxNode:
    """Visual box node for SVG architectural rendering."""

    node_id: str
    title: str
    subtitle: str
    x: int
    y: int
    w: int
    h: int
    fill: str
    stroke: str


@dataclass(frozen=True)
class EdgeArrow:
    """Directed edge between two coordinates with an optional label."""

    x1: int
    y1: int
    x2: int
    y2: int
    label: str = ""
    dashed: bool = False


@dataclass(frozen=True)
class DiagramSpec:
    """Complete specification for a Mermaid source and its matching SVG rendering."""

    filename_stem: str
    title: str
    subtitle: str
    mermaid_source: str
    width: int
    height: int
    boxes: list[BoxNode]
    edges: list[EdgeArrow]


DIAGRAM_SPECS: list[DiagramSpec] = [
    DiagramSpec(
        filename_stem="01_system_context",
        title="1. System Context Diagram — Incident Response & Root Cause Post-Mortem Agent",
        subtitle="External triggers, observability backends, deploy feeds, and dual-provider LLM tiers",
        mermaid_source="""flowchart LR
    subgraph Triggers["Alert & ChatOps Sources"]
        PD["PagerDuty Webhooks<br/>(trigger / ack / resolve)"]
        SL["Slack Workspace<br/>(/triage, @incident-agent, Approve/Reject)"]
        AM["Prometheus Alertmanager<br/>(v4 Alert Webhooks)"]
    end

    subgraph AgentPod["Incident Response & Root Cause Post-Mortem Agent"]
        CORE["FastAPI + LangGraph Triage Pod<br/>(Read-Only Sub-Agents + ActionGate + Redaction)"]
    end

    subgraph Observability["Read-Only Observability & Infra Backends"]
        LOGS["Loki / Elasticsearch<br/>(LogAdapter)"]
        METRICS["Prometheus TSDB<br/>(MetricsAdapter)"]
        DEPLOYS["ArgoCD / GitHub / LaunchDarkly<br/>(ChangeAdapter)"]
        TOPO["Service Catalog Graph<br/>(TopologyAdapter)"]
        RUNBOOKS["Runbook & Post-Mortem Store<br/>(RunbookAdapter)"]
    end

    subgraph LLMProviders["Tiered LLM Providers"]
        GROQ["Groq Cloud API<br/>(llama-3.1-8b-instant & llama-3.3-70b-versatile)"]
        GEMINI["Google Gemini API<br/>(gemini-3.5-flash-lite & gemini-3.8-flash 1M ctx)"]
    end

    PD -->|"HTTPS Webhook"| CORE
    SL <-->|"Slash Cmds / Block Kit / Approvals"| CORE
    AM -->|"HTTPS Webhook"| CORE
    CORE -->|"Read-Only Queries"| LOGS
    CORE -->|"Read-Only Queries"| METRICS
    CORE -->|"Read-Only Queries"| DEPLOYS
    CORE -->|"Read-Only Queries"| TOPO
    CORE -->|"Read-Only Queries"| RUNBOOKS
    CORE -->|"Primary Inference"| GROQ
    CORE -.->|"Fallback & >24k Long-Context"| GEMINI
""",
        width=1120,
        height=560,
        boxes=[
            BoxNode(
                "pd",
                "PagerDuty Webhooks",
                "trigger / ack / resolve",
                40,
                90,
                240,
                75,
                "#EEF2FF",
                "#4F46E5",
            ),
            BoxNode(
                "sl",
                "Slack Workspace",
                "/triage, mentions, Approve buttons",
                40,
                210,
                240,
                75,
                "#EEF2FF",
                "#4F46E5",
            ),
            BoxNode(
                "am",
                "Alertmanager",
                "Prometheus firing alerts",
                40,
                330,
                240,
                75,
                "#EEF2FF",
                "#4F46E5",
            ),
            BoxNode(
                "pod",
                "Incident Response & Post-Mortem Pod",
                "FastAPI + LangGraph Supervisor + Safety Layer",
                390,
                175,
                310,
                150,
                "#ECFDF5",
                "#059669",
            ),
            BoxNode(
                "obs",
                "Observability & Infra Backends",
                "Logs, Metrics, Deploys, Topology, Runbooks",
                820,
                85,
                260,
                110,
                "#FFFBEB",
                "#D97706",
            ),
            BoxNode(
                "groq",
                "Groq Cloud (Primary LLM)",
                "llama-3.1-8b-instant / llama-3.3-70b",
                820,
                235,
                260,
                95,
                "#F0F9FF",
                "#0284C7",
            ),
            BoxNode(
                "gem",
                "Google Gemini API (Secondary / 1M)",
                "gemini-3.5-flash-lite / gemini-3.8-flash",
                820,
                365,
                260,
                95,
                "#FDF2F8",
                "#DB2777",
            ),
        ],
        edges=[
            EdgeArrow(280, 127, 390, 220, "Webhook"),
            EdgeArrow(280, 247, 390, 250, "Events & Approvals"),
            EdgeArrow(280, 367, 390, 280, "Alert JSON"),
            EdgeArrow(700, 220, 820, 140, "Read-Only"),
            EdgeArrow(700, 255, 820, 280, "Primary Tier"),
            EdgeArrow(700, 290, 820, 410, "Fallback & Long-Ctx", dashed=True),
        ],
    ),
    DiagramSpec(
        filename_stem="02_component_architecture",
        title="2. Component Architecture Diagram — Internal Modules & Data Flow",
        subtitle="FastAPI routers, safety controls, LangGraph supervisor, 11 typed sub-agent tools, adapters, and SQLite store",
        mermaid_source="""flowchart TB
    subgraph API["src/incident_agent/main.py & integrations/"]
        FAST["FastAPI Endpoints<br/>(/webhooks/pagerduty, /webhooks/alertmanager, /slack/*)"]
        INT["SlackClient & PagerDutyClient<br/>(Retry + Exponential Backoff)"]
    end

    subgraph Safety["src/incident_agent/safety/"]
        RED["RedactionFilter<br/>(Emails, Tokens, Keys, IPs)"]
        INJ["InjectionFilter<br/>(Neutralize Prompt Injections)"]
        BUD["BudgetEnforcer<br/>(Max 3 Rounds, 25 Calls, 100k Tokens)"]
        ALLOW["DataSourceAllowlist<br/>(Read-Only Verification)"]
        GATE["ActionGate<br/>(execute_approved_action)"]
    end

    subgraph Supervisor["src/incident_agent/supervisor/"]
        SUP["IncidentSupervisor<br/>(LangGraph StateGraph)"]
    end

    subgraph Subagents["src/incident_agent/subagents/ (11 LangChain StructuredTools)"]
        T1["triage_intake"]
        T2["log_analyst / metrics_analyst / change_correlator / topology_mapper / runbook_retriever"]
        T3["hypothesis_ranker & remediation_proposer"]
        T4["timeline_builder / diagram_generator / postmortem_writer"]
    end

    subgraph Infra["adapters/ + models/ + persistence/ + tracing/"]
        ADP["AdapterBundle<br/>(5 Mock/Protocol Adapters)"]
        MOD["get_chat_model(role)<br/>(ResilientTieredChatModel)"]
        STR["SQLiteIncidentStore<br/>(IncidentStore Protocol)"]
        TRC["LLMTracer<br/>(Token & USD Cost Accounting)"]
    end

    FAST --> RED --> INJ --> SUP
    SUP --> BUD --> T1 & T2 & T3 & T4
    T2 --> ALLOW --> ADP
    T1 & T2 & T3 & T4 --> MOD --> TRC --> STR
    SUP --> STR
    SUP --> INT
    FAST --> GATE --> STR
""",
        width=1120,
        height=600,
        boxes=[
            BoxNode(
                "api",
                "FastAPI & Integrations Layer",
                "main.py, SlackClient, PagerDutyClient",
                60,
                85,
                300,
                95,
                "#EEF2FF",
                "#4F46E5",
            ),
            BoxNode(
                "saf",
                "Defense-in-Depth Safety Layer",
                "RedactionFilter, InjectionFilter, BudgetEnforcer, ActionGate",
                410,
                85,
                320,
                95,
                "#FEF2F2",
                "#DC2626",
            ),
            BoxNode(
                "sup",
                "LangGraph IncidentSupervisor",
                "supervisor/graph.py (StateGraph + Checkpointer)",
                780,
                85,
                280,
                95,
                "#ECFDF5",
                "#059669",
            ),
            BoxNode(
                "sub1",
                "Intake & Parallel Discovery Tools",
                "triage_intake, log_analyst, metrics_analyst, change_correlator, topology_mapper, runbook_retriever",
                60,
                250,
                480,
                110,
                "#F0F9FF",
                "#0284C7",
            ),
            BoxNode(
                "sub2",
                "Reasoning & Post-Mortem Tools",
                "hypothesis_ranker, remediation_proposer, timeline_builder, diagram_generator, postmortem_writer",
                580,
                250,
                480,
                110,
                "#F0F9FF",
                "#0284C7",
            ),
            BoxNode(
                "adp",
                "Read-Only Adapters",
                "logs, metrics, deploys, topology, runbooks",
                60,
                430,
                310,
                100,
                "#FFFBEB",
                "#D97706",
            ),
            BoxNode(
                "mod",
                "Model Factory & LLMTracer",
                "get_chat_model(role) + Groq/Gemini Fallback",
                410,
                430,
                310,
                100,
                "#FDF2F8",
                "#DB2777",
            ),
            BoxNode(
                "db",
                "Persistence (IncidentStore)",
                "SQLiteIncidentStore (Checkpoints, Evidence, Traces)",
                760,
                430,
                300,
                100,
                "#F3F4F6",
                "#374151",
            ),
        ],
        edges=[
            EdgeArrow(360, 132, 410, 132, "Sanitize"),
            EdgeArrow(730, 132, 780, 132, "Budget Check"),
            EdgeArrow(850, 180, 300, 250, "Fan-Out"),
            EdgeArrow(920, 180, 820, 250, "Synthesize"),
            EdgeArrow(250, 360, 215, 430, "Allowlist Read"),
            EdgeArrow(560, 360, 565, 430, "LLM Invoke"),
            EdgeArrow(860, 360, 910, 430, "Save State"),
        ],
    ),
    DiagramSpec(
        filename_stem="03_incident_lifecycle_sequence",
        title="3. Incident Lifecycle Sequence Diagram — Trigger to Blameless Post-Mortem",
        subtitle="End-to-end message flow across PagerDuty, FastAPI, Supervisor, Sub-Agents, Slack, and ActionGate",
        mermaid_source="""sequenceDiagram
    autonumber
    participant PD as PagerDuty / Alertmanager
    participant API as FastAPI Server
    participant SUP as LangGraph Supervisor
    participant SUB as Sub-Agent Tools (11)
    participant SLK as Slack Incident Thread
    participant ENG as On-Call Engineer

    PD->>API: POST /webhooks/pagerduty (incident.triggered)
    API->>SLK: Post immediate acknowledgment (<2s)
    API->>SUP: start_incident_triage(incident_id)
    SUP->>SUB: triage_intake(raw_payload)
    par Parallel Telemetry Gathering (Round 1)
        SUP->>SUB: topology_mapper(service)
        SUP->>SUB: log_analyst(service, window)
        SUP->>SUB: metrics_analyst(service, window)
        SUP->>SUB: change_correlator(services, onset)
        SUP->>SUB: runbook_retriever(service, symptoms)
    end
    SUP->>SUB: hypothesis_ranker(all_evidence)
    opt confidence < 0.70 and round < 3
        SUP->>SUB: Targeted Round 2/3 investigation on critical dependency
        SUP->>SUB: hypothesis_ranker(updated_evidence)
    end
    SUP->>SUB: remediation_proposer(hypotheses) [PROPOSAL_ONLY]
    SUP->>SLK: Post Triage Summary + Approve/Reject Action Buttons
    ENG->>API: Click Approve Button (POST /slack/interactions)
    API->>SUP: ActionGate.record_approval() & execute_approved_action()
    PD->>API: POST /webhooks/pagerduty (incident.resolved)
    API->>SUP: resolve_and_generate_postmortem(incident_id)
    SUP->>SUB: timeline_builder() -> diagram_generator() -> postmortem_writer()
    SUP->>SLK: Post Blameless Post-Mortem Draft & Mermaid Diagrams
""",
        width=1120,
        height=560,
        boxes=[
            BoxNode(
                "s1",
                "1. Alert Trigger & Ack",
                "PagerDuty webhook -> Slack thread ack",
                50,
                95,
                310,
                95,
                "#EEF2FF",
                "#4F46E5",
            ),
            BoxNode(
                "s2",
                "2. Intake & Parallel Triage",
                "triage_intake + 5 parallel telemetry tools",
                405,
                95,
                310,
                95,
                "#F0F9FF",
                "#0284C7",
            ),
            BoxNode(
                "s3",
                "3. Hypothesis Ranking Loop",
                "hypothesis_ranker (up to 3 rounds if conf < 0.70)",
                760,
                95,
                310,
                95,
                "#ECFDF5",
                "#059669",
            ),
            BoxNode(
                "s4",
                "4. Proposal & Slack Summary",
                "remediation_proposer (PROPOSAL_ONLY) -> Slack",
                50,
                265,
                310,
                95,
                "#FFFBEB",
                "#D97706",
            ),
            BoxNode(
                "s5",
                "5. Human Approval Gate",
                "On-Call clicks Approve -> ActionGate executes",
                405,
                265,
                310,
                95,
                "#FEF2F2",
                "#DC2626",
            ),
            BoxNode(
                "s6",
                "6. Resolution & Post-Mortem",
                "timeline_builder -> diagram_generator -> postmortem_writer",
                760,
                265,
                310,
                95,
                "#FDF2F8",
                "#DB2777",
            ),
        ],
        edges=[
            EdgeArrow(360, 142, 405, 142, "Step 1->2"),
            EdgeArrow(715, 142, 760, 142, "Step 2->3"),
            EdgeArrow(915, 190, 205, 265, "Step 3->4"),
            EdgeArrow(360, 312, 405, 312, "Step 4->5"),
            EdgeArrow(715, 312, 760, 312, "Step 5->6"),
        ],
    ),
    DiagramSpec(
        filename_stem="04_supervisor_control_flow",
        title="4. Supervisor Control-Flow Diagram — Confidence Loop & 3-Round Cap",
        subtitle="LangGraph routing rules, budget checks, and conditional edges in src/incident_agent/supervisor/graph.py",
        mermaid_source="""flowchart TD
    START(("START")) --> INTAKE["Node: intake<br/>Call triage_intake & save checkpoint"]
    INTAKE --> ROUND_INC["Node: investigate<br/>Increment round_number & check BudgetEnforcer"]
    ROUND_INC --> PAR["Parallel Sub-Agent Fan-Out:<br/>topology_mapper, log_analyst, metrics_analyst,<br/>change_correlator, runbook_retriever"]
    PAR --> RANK["Node: rank_hypotheses<br/>Call hypothesis_ranker & validate evidence_ids"]
    RANK --> CHECK{"top_confidence >= 0.70<br/>OR round_number >= 3<br/>OR budget_halted?"}
    CHECK -- "No (Confidence < 0.70 & round < 3)" --> TARGET["Select critical downstream dependency<br/>for targeted follow-up round"]
    TARGET --> ROUND_INC
    CHECK -- "Yes (Sufficient confidence or cap hit)" --> PROP["Node: propose_remediation<br/>Call remediation_proposer (PROPOSAL_ONLY)"]
    PROP --> POST["Format TriageSummaryReport & Post to Slack<br/>Save 'triaged' checkpoint"]
    POST --> END_NODE(("END"))
""",
        width=1120,
        height=560,
        boxes=[
            BoxNode(
                "n1",
                "START -> Node: intake",
                "Normalize alert & checkpoint 'intake'",
                60,
                100,
                260,
                90,
                "#EEF2FF",
                "#4F46E5",
            ),
            BoxNode(
                "n2",
                "Node: investigate (Round 1..3)",
                "Parallel fan-out of 5 discovery sub-agents",
                390,
                100,
                310,
                90,
                "#F0F9FF",
                "#0284C7",
            ),
            BoxNode(
                "n3",
                "Node: rank_hypotheses",
                "Rank hypotheses & verify evidence citations",
                770,
                100,
                290,
                90,
                "#ECFDF5",
                "#059669",
            ),
            BoxNode(
                "n4",
                "Decision: Confidence & Budget Check",
                "conf >= 0.70 OR round >= 3 OR budget_halted?",
                390,
                265,
                310,
                95,
                "#FFFBEB",
                "#D97706",
            ),
            BoxNode(
                "n5",
                "Node: propose_remediation -> END",
                "Generate PROPOSAL_ONLY steps & post Slack summary",
                770,
                265,
                290,
                95,
                "#FDF2F8",
                "#DB2777",
            ),
        ],
        edges=[
            EdgeArrow(320, 145, 390, 145, "Checkpoint"),
            EdgeArrow(700, 145, 770, 145, "Evidence Pool"),
            EdgeArrow(915, 190, 545, 265, "Evaluate"),
            EdgeArrow(460, 265, 460, 190, "conf < 0.70 & round < 3", dashed=True),
            EdgeArrow(700, 312, 770, 312, "conf >= 0.70 or round == 3"),
        ],
    ),
    DiagramSpec(
        filename_stem="05_model_tier_routing",
        title="5. Model Tier & Provider Fallback Diagram",
        subtitle="Role-based model assignment (supervisor, core, long_context) and automatic retry/failover chains",
        mermaid_source="""flowchart LR
    subgraph Subagents["11 Sub-Agent Tools"]
        S_SUP["Supervisor Router &<br/>triage_intake"]
        S_CORE["Core Sub-Agents (9):<br/>metrics, changes, topology,<br/>runbooks, ranker, proposer,<br/>timeline, postmortem, diagrams"]
        S_LOG["log_analyst<br/>(Token-Count Router)"]
    end

    subgraph Tiers["Model Tiers (config/models.yaml)"]
        T_SUP["Tier: supervisor<br/>Primary: Groq llama-3.1-8b-instant (131k)"]
        T_CORE["Tier: core<br/>Primary: Groq llama-3.3-70b-versatile (131k)"]
        T_LONG["Tier: long_context<br/>Primary: Gemini gemini-3.8-flash (1M)"]
    end

    subgraph Fallbacks["Secondary Failover Providers (on HTTP 429 / 5xx)"]
        F_SUP["Fallback: Gemini<br/>gemini-3.5-flash-lite (1M)"]
        F_CORE["Fallback: Gemini<br/>gemini-3.8-flash (1M)"]
        F_LONG["Fallback: Gemini<br/>gemini-2.5-flash (1M)"]
    end

    S_SUP --> T_SUP
    S_CORE --> T_CORE
    S_LOG -->|"tokens <= 24,000"| T_CORE
    S_LOG -->|"tokens > 24,000"| T_LONG

    T_SUP -.->|"Retry x2 + Backoff -> Failover"| F_SUP
    T_CORE -.->|"Retry x2 + Backoff -> Failover"| F_CORE
    T_LONG -.->|"Retry x2 + Backoff -> Failover"| F_LONG
""",
        width=1120,
        height=560,
        boxes=[
            BoxNode(
                "m1",
                "Supervisor & triage_intake",
                "Fast routing & structured intake",
                45,
                95,
                270,
                85,
                "#EEF2FF",
                "#4F46E5",
            ),
            BoxNode(
                "m2",
                "9 Core Analytical Sub-Agents",
                "Metrics, Changes, Topology, Ranker, Writer",
                45,
                225,
                270,
                85,
                "#F0F9FF",
                "#0284C7",
            ),
            BoxNode(
                "m3",
                "log_analyst (Token Router)",
                "Checks estimated_tokens > 24,000",
                45,
                355,
                270,
                85,
                "#FFFBEB",
                "#D97706",
            ),
            BoxNode(
                "p1",
                "Tier: supervisor (Primary)",
                "Groq llama-3.1-8b-instant (131k ctx)",
                415,
                95,
                290,
                85,
                "#ECFDF5",
                "#059669",
            ),
            BoxNode(
                "p2",
                "Tier: core (Primary)",
                "Groq llama-3.3-70b-versatile (131k ctx)",
                415,
                225,
                290,
                85,
                "#ECFDF5",
                "#059669",
            ),
            BoxNode(
                "p3",
                "Tier: long_context (Primary)",
                "Gemini gemini-3.8-flash (1M ctx)",
                415,
                355,
                290,
                85,
                "#ECFDF5",
                "#059669",
            ),
            BoxNode(
                "f1",
                "Supervisor Secondary Fallback",
                "Gemini gemini-3.5-flash-lite (1M ctx)",
                795,
                95,
                280,
                85,
                "#FDF2F8",
                "#DB2777",
            ),
            BoxNode(
                "f2",
                "Core Secondary Fallback",
                "Gemini gemini-3.8-flash (1M ctx)",
                795,
                225,
                280,
                85,
                "#FDF2F8",
                "#DB2777",
            ),
            BoxNode(
                "f3",
                "Long-Context Fallback",
                "Gemini gemini-2.5-flash (1M ctx)",
                795,
                355,
                280,
                85,
                "#FDF2F8",
                "#DB2777",
            ),
        ],
        edges=[
            EdgeArrow(315, 137, 415, 137, "Default"),
            EdgeArrow(315, 267, 415, 267, "Default"),
            EdgeArrow(315, 380, 415, 285, "<= 24k tok"),
            EdgeArrow(315, 405, 415, 397, "> 24k tok"),
            EdgeArrow(705, 137, 795, 137, "429/5xx Failover", dashed=True),
            EdgeArrow(705, 267, 795, 267, "429/5xx Failover", dashed=True),
            EdgeArrow(705, 397, 795, 397, "429/5xx Failover", dashed=True),
        ],
    ),
    DiagramSpec(
        filename_stem="06_deployment_topology",
        title="6. Deployment Diagram — Local Dev vs. Production Reference Architecture",
        subtitle="Local SQLite + Mock Adapters setup vs. Production Container, Queue, PostgreSQL, and Secrets Manager",
        mermaid_source="""flowchart TB
    subgraph LocalDev["Local Development & CI Setup"]
        L_SIM["scripts/simulate_incident.py<br/>or pytest Suite"]
        L_API["FastAPI App (Uvicorn)<br/>src/incident_agent/main.py"]
        L_MOCK["Mock AdapterBundle<br/>(5 Built-In Scenarios)"]
        L_DB[("SQLite WAL Store<br/>./data/incidents.db")]
        L_ENV[".env / Environment Variables"]
        L_SIM --> L_API
        L_ENV --> L_API
        L_API --> L_MOCK
        L_API --> L_DB
    end

    subgraph ProdEnv["Production Reference Setup (Kubernetes / Cloud)"]
        P_ING["Cloud Load Balancer / API Gateway<br/>(TLS + Webhook HMAC Verification)"]
        P_POD["Incident Agent API & Worker Pods<br/>(Docker Container, Read-Only IAM Role)"]
        P_Q["Async Task Queue<br/>(Redis / Cloud Tasks)"]
        P_PG[("Managed PostgreSQL<br/>IncidentStore + Checkpoints")]
        P_SEC["Cloud Secrets Manager<br/>(GROQ_API_KEY, GEMINI_API_KEY, Slack/PD Tokens)"]
        P_OBS["Live Loki, Prometheus, ArgoCD,<br/>Backstage & Runbook Vector Store"]
        P_ING --> P_POD --> P_Q --> P_POD
        P_SEC --> P_POD
        P_POD --> P_PG
        P_POD -->|"Read-Only Credentials"| P_OBS
    end
""",
        width=1120,
        height=580,
        boxes=[
            BoxNode(
                "l1",
                "Local Dev: Simulator & Pytest",
                "scripts/simulate_incident.py + Uvicorn FastAPI",
                55,
                95,
                440,
                95,
                "#EEF2FF",
                "#4F46E5",
            ),
            BoxNode(
                "l2",
                "Local Dev: 5-Scenario Mock Adapters",
                "MockLogAdapter, MockMetricsAdapter, MockChangeAdapter",
                55,
                230,
                440,
                95,
                "#F0F9FF",
                "#0284C7",
            ),
            BoxNode(
                "l3",
                "Local Dev: SQLite Persistence",
                "SQLiteIncidentStore (./data/incidents.db or :memory:)",
                55,
                365,
                440,
                95,
                "#ECFDF5",
                "#059669",
            ),
            BoxNode(
                "p1",
                "Prod: API Gateway & Container Pods",
                "TLS Webhook Ingress + Kubernetes Worker Deployment",
                605,
                95,
                460,
                95,
                "#FFFBEB",
                "#D97706",
            ),
            BoxNode(
                "p2",
                "Prod: Secrets Manager & Task Queue",
                "KMS/Secrets Manager + Redis/Cloud Tasks Worker Queue",
                605,
                230,
                460,
                95,
                "#FEF2F2",
                "#DC2626",
            ),
            BoxNode(
                "p3",
                "Prod: Managed PostgreSQL & Live Telemetry",
                "PostgresIncidentStore + Read-Only Loki/Prometheus/ArgoCD",
                605,
                365,
                460,
                95,
                "#FDF2F8",
                "#DB2777",
            ),
        ],
        edges=[
            EdgeArrow(275, 190, 275, 230, "In-Process"),
            EdgeArrow(275, 325, 275, 365, "SQLite WAL"),
            EdgeArrow(835, 190, 835, 230, "Async Dispatch"),
            EdgeArrow(835, 325, 835, 365, "TLS Read-Only"),
        ],
    ),
]


def render_svg(spec: DiagramSpec) -> str:
    """Render a clean, self-contained vector SVG diagram matching the Mermaid source."""
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {spec.width} {spec.height}" width="{spec.width}" height="{spec.height}">',
        "  <defs>",
        '    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">',
        '      <path d="M 0 1 L 10 5 L 0 9 z" fill="#475569"/>',
        "    </marker>",
        "    <style>",
        "      .bg { fill: #F8FAFC; }",
        '      .header { font-family: "Segoe UI", Arial, sans-serif; font-size: 18px; font-weight: 700; fill: #0F172A; }',
        '      .sub { font-family: "Segoe UI", Arial, sans-serif; font-size: 13px; fill: #475569; }',
        '      .node-title { font-family: "Segoe UI", Arial, sans-serif; font-size: 13px; font-weight: 700; fill: #1E293B; }',
        '      .node-sub { font-family: "Segoe UI", Arial, sans-serif; font-size: 11px; fill: #475569; }',
        '      .edge-label { font-family: "Segoe UI", Arial, sans-serif; font-size: 11px; font-weight: 600; fill: #334155; }',
        "    </style>",
        "  </defs>",
        f'  <rect class="bg" x="0" y="0" width="{spec.width}" height="{spec.height}" rx="12"/>',
        f'  <text class="header" x="40" y="38">{html.escape(spec.title)}</text>',
        f'  <text class="sub" x="40" y="60">{html.escape(spec.subtitle)}</text>',
    ]

    for edge in spec.edges:
        dash_attr = ' stroke-dasharray="6,4"' if edge.dashed else ""
        parts.append(
            f'  <line x1="{edge.x1}" y1="{edge.y1}" x2="{edge.x2}" y2="{edge.y2}" '
            f'stroke="#475569" stroke-width="2"{dash_attr} marker-end="url(#arrow)"/>'
        )
        if edge.label:
            mx = (edge.x1 + edge.x2) // 2
            my = (edge.y1 + edge.y2) // 2 - 8
            parts.append(
                f'  <text class="edge-label" x="{mx}" y="{my}" text-anchor="middle">{html.escape(edge.label)}</text>'
            )

    for box in spec.boxes:
        parts.append(
            f'  <rect x="{box.x}" y="{box.y}" width="{box.w}" height="{box.h}" rx="8" '
            f'fill="{box.fill}" stroke="{box.stroke}" stroke-width="2"/>'
        )
        cx = box.x + box.w // 2
        cy = box.y + box.h // 2
        parts.append(
            f'  <text class="node-title" x="{cx}" y="{cy - 6}" text-anchor="middle">{html.escape(box.title)}</text>'
        )
        parts.append(
            f'  <text class="node-sub" x="{cx}" y="{cy + 14}" text-anchor="middle">{html.escape(box.subtitle)}</text>'
        )

    parts.append("</svg>\n")
    return "\n".join(parts)


def generate_all_diagrams(output_dir: str | Path = "docs/diagrams") -> list[Path]:
    """Write all 6 `.mmd` and `.svg` diagrams to `output_dir`."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for spec in DIAGRAM_SPECS:
        mmd_file = out_path / f"{spec.filename_stem}.mmd"
        svg_file = out_path / f"{spec.filename_stem}.svg"
        mmd_file.write_text(spec.mermaid_source.strip() + "\n", encoding="utf-8")
        svg_file.write_text(render_svg(spec), encoding="utf-8")
        written.extend([mmd_file, svg_file])

    return written


def main() -> None:
    files = generate_all_diagrams()
    print(f"Generated {len(files)} diagram artifacts in docs/diagrams/:")
    for f in files:
        print(f"  - {f}")


if __name__ == "__main__":
    main()
