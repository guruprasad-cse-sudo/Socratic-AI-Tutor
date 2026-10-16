"""Design-phase curriculum for a web application project.

Each stage has concepts. Each concept has a rubric and a seed question per tier:
  Tier 1 = recall, Tier 2 = mechanism, Tier 3 = judgment.
A concept is mastered when the student passes its required tier
(tier 3 for core concepts, tier 2 otherwise).

The tutor rewrites seed questions around the student's own project;
the evaluator grades answers against the rubric.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Concept:
    id: str
    name: str
    is_core: bool
    rubrics: dict[int, str]
    seed_questions: dict[int, str]

    @property
    def required_tier(self) -> int:
        from .config import PACE, PACES

        return PACES[PACE]["core" if self.is_core else "other"]


@dataclass(frozen=True)
class Stage:
    id: str
    name: str
    goal: str
    concepts: list[Concept]
    template: str      # blank document template released when the stage starts
    checklist: str     # reference checklist released when the gate opens
    probes: list[str] = field(default_factory=list)


STAGES: list[Stage] = [
    Stage(
        id="requirements",
        name="Requirements",
        goal="Define who the users are, what the system must do, and what is out of scope.",
        concepts=[
            Concept(
                id="users_and_scope",
                name="Users, roles and scope",
                is_core=True,
                rubrics={
                    1: "Names the distinct user roles of their system and states at least one thing that is out of scope.",
                    2: "Explains what each role can and cannot do, and why stating scope prevents wasted work or disagreement.",
                    3: "Handles a scope conflict or new request (e.g. a stakeholder wants a feature mid-project): weighs value vs. effort and says how scope changes are decided.",
                },
                seed_questions={
                    1: "Who are the different users of your system, and what is one thing you will deliberately NOT build?",
                    2: "Pick two roles. What can each one do that the other can't, and why does writing that down matter before design?",
                    3: "Halfway through, someone asks for a feature you marked out of scope. How do you decide whether to add it?",
                },
            ),
            Concept(
                id="functional_vs_nonfunctional",
                name="Functional vs. non-functional requirements",
                is_core=False,
                rubrics={
                    1: "Functional = what the system does (features/behaviour); non-functional = how well (performance, availability, security, scalability).",
                    2: "Gives correct examples of each from their own project and explains how non-functional requirements shape architecture choices.",
                    3: "Identifies a trade-off between two non-functional requirements in their project (e.g. consistency vs. availability, security vs. usability).",
                },
                seed_questions={
                    1: "What's the difference between a functional and a non-functional requirement?",
                    2: "Give me two of each from your own project. Which non-functional one will most affect your design?",
                    3: "Name two non-functional requirements in your project that pull against each other. Which wins, and why?",
                },
            ),
            Concept(
                id="testable_requirements",
                name="Specific, testable requirements",
                is_core=False,
                rubrics={
                    1: "A good requirement is specific and verifiable; vague words like 'fast' or 'user-friendly' are not testable.",
                    2: "Rewrites a vague requirement from their project into a measurable one and explains how it would be tested.",
                    3: "Explains how untestable requirements cause problems later (disputes, unclear done-ness, wrong design) with a concrete example.",
                },
                seed_questions={
                    1: "What makes a requirement 'testable'? Is 'the app should be fast' a good requirement?",
                    2: "Take one vague requirement from your project and rewrite it so someone could test it.",
                    3: "What goes wrong later in a project if requirements aren't testable? Give an example from yours.",
                },
            ),
        ],
        template=(
            "# Requirements\n\n## Problem statement\n\n## Users and roles\n\n"
            "## Functional requirements\n1. \n\n## Non-functional requirements\n1. \n\n"
            "## Out of scope\n- \n\n## Open questions\n- \n"
        ),
        checklist=(
            "Requirements checklist\n"
            "- Every role is named and its permissions are listed\n"
            "- Each functional requirement is a single, testable statement\n"
            "- Non-functional requirements have numbers (e.g. 'p95 < 300 ms')\n"
            "- Out-of-scope items are explicit\n"
            "- Open questions are recorded, not silently assumed"
        ),
    ),
    Stage(
        id="specifications",
        name="System specifications",
        goal="Turn requirements into measurable targets: scale, latency, availability and storage.",
        concepts=[
            Concept(
                id="capacity_estimation",
                name="Back-of-envelope capacity estimation",
                is_core=True,
                rubrics={
                    1: "Explains why estimate scale before designing: estimates justify choices like caching, queues or sharding.",
                    2: "Estimates daily users, requests per second (average and peak) and storage growth for their project, showing the arithmetic and assumptions.",
                    3: "Uses the estimate to make or reject a design decision (e.g. 'at ~50 req/s a single DB is fine; caching is not needed yet') and says what would change at 10x.",
                },
                seed_questions={
                    1: "Why do engineers estimate scale before choosing an architecture?",
                    2: "Estimate your system's requests per second (average and peak) and storage after one year. Show your arithmetic.",
                    3: "Based on your numbers, what design choice can you skip for now, and at what scale would you need it?",
                },
            ),
            Concept(
                id="latency_availability",
                name="Latency and availability targets",
                is_core=False,
                rubrics={
                    1: "Latency = time to respond; availability = fraction of time the system works (e.g. 99.9% ~ 8.8 hours of downtime a year).",
                    2: "Sets sensible latency and availability targets for their main flows and explains why they differ between flows.",
                    3: "Explains the cost of raising availability (redundancy, failover, complexity) and justifies the target they chose.",
                },
                seed_questions={
                    1: "What do latency and availability mean? Roughly how much downtime per year does 99.9% allow?",
                    2: "Set latency and availability targets for your two most important flows. Why are they different?",
                    3: "What would it cost you to go from 99.9% to 99.99% availability? Is it worth it for your project?",
                },
            ),
            Concept(
                id="read_write_ratio",
                name="Read/write patterns",
                is_core=False,
                rubrics={
                    1: "Knows that systems are read-heavy or write-heavy and that this ratio guides storage and caching decisions.",
                    2: "Identifies the read/write ratio of their main data and explains how it affects their design.",
                    3: "Designs differently for a hot path (e.g. cache reads, queue writes) and explains the consistency trade-off that creates.",
                },
                seed_questions={
                    1: "What does it mean for a system to be read-heavy vs. write-heavy?",
                    2: "Is your system read-heavy or write-heavy? Estimate the ratio for your main data and say how it affects your design.",
                    3: "If you cache your most-read data, what consistency problem can appear, and is it acceptable in your project?",
                },
            ),
        ],
        template=(
            "# System specifications\n\n## Assumptions\n- Daily active users: \n- Actions per user per day: \n\n"
            "## Traffic estimates\n- Average requests/second: \n- Peak requests/second: \n- Read/write ratio: \n\n"
            "## Storage estimates\n- Size per record: \n- Growth per year: \n\n"
            "## Latency and availability targets\n| Flow | Latency target | Availability |\n|---|---|---|\n"
        ),
        checklist=(
            "Specifications checklist\n"
            "- Every number shows its assumptions and arithmetic\n"
            "- Peak traffic is estimated, not just the average\n"
            "- Targets differ per flow where it matters\n"
            "- At least one design decision is justified by the numbers"
        ),
    ),
    Stage(
        id="hld",
        name="High-level design",
        goal="Choose the major components, how they communicate, and where data lives.",
        concepts=[
            Concept(
                id="components_and_responsibilities",
                name="Components and responsibilities",
                is_core=True,
                rubrics={
                    1: "Names typical components (client, API server, database, cache, queue, external services) and what each is for.",
                    2: "Draws/describes their own system's components, what each is responsible for, and how a main request flows through them.",
                    3: "Justifies each component by a requirement or estimate and removes or defers any component that isn't needed.",
                },
                seed_questions={
                    1: "What are the usual building blocks of a web system, and what is each one for?",
                    2: "Describe the components of your system and trace one main request through them, step by step.",
                    3: "Which component in your design could you remove for version 1? Justify every component you keep.",
                },
            ),
            Concept(
                id="scaling_and_failures",
                name="Scaling and failure points",
                is_core=True,
                rubrics={
                    1: "Knows horizontal vs. vertical scaling and what a single point of failure is.",
                    2: "Identifies the single points of failure in their design and explains how each component scales.",
                    3: "Explains what happens to their system at 10x traffic or when a key component fails, and what they'd change.",
                },
                seed_questions={
                    1: "What's the difference between vertical and horizontal scaling? What is a single point of failure?",
                    2: "Where are the single points of failure in your design? How would each component scale?",
                    3: "Your database goes down for 5 minutes. Walk me through what users experience and what you'd change.",
                },
            ),
            Concept(
                id="sync_vs_async",
                name="Synchronous vs. asynchronous work",
                is_core=False,
                rubrics={
                    1: "Sync = caller waits for the result; async = work is handed off (e.g. via a queue) and done later.",
                    2: "Identifies which operations in their project should be async (emails, notifications, reports) and why.",
                    3: "Explains the new problems async introduces (retries, duplicates, ordering, status tracking) and how they'd handle one.",
                },
                seed_questions={
                    1: "What's the difference between doing work synchronously and asynchronously?",
                    2: "Which operations in your project should not make the user wait? How would you run them instead?",
                    3: "If you move that work to a queue, what can go wrong (think retries and duplicates), and how do you handle it?",
                },
            ),
        ],
        template=(
            "# High-level design\n\n## Component list\n| Component | Responsibility | Why needed |\n|---|---|---|\n\n"
            "## Main request flow\n1. \n\n## Data stores\n\n## Scaling plan\n\n## Single points of failure and mitigations\n"
        ),
        checklist=(
            "HLD checklist\n"
            "- Each component maps to a requirement or estimate\n"
            "- One main request is traced end to end\n"
            "- Single points of failure are named, with mitigations or accepted risk\n"
            "- Async work is identified, with retry and status tracking"
        ),
    ),
    Stage(
        id="lld",
        name="Low-level design",
        goal="Define modules or classes, their responsibilities, and how key flows work in detail.",
        concepts=[
            Concept(
                id="responsibility_assignment",
                name="Modules and responsibility assignment",
                is_core=False,
                rubrics={
                    1: "Explains single responsibility: each module/class has one clear reason to change.",
                    2: "Lists their main modules/classes and assigns each business rule to exactly one of them, with reasons.",
                    3: "Spots a module in their design doing too much (or a rule split across modules) and refactors it, explaining the benefit.",
                },
                seed_questions={
                    1: "What does 'single responsibility' mean for a module or class?",
                    2: "List your main modules. Which one owns your most important business rule, and why that one?",
                    3: "Which of your modules is doing too much? How would you split it, and what do you gain?",
                },
            ),
            Concept(
                id="flow_walkthrough",
                name="Key flow walkthrough",
                is_core=True,
                rubrics={
                    1: "Can describe what a sequence diagram shows: the order of calls between components for one flow.",
                    2: "Walks through their most important flow step by step: which module calls which, what is validated, what is stored, what is returned.",
                    3: "Handles failure partway through the flow (e.g. step 3 fails after step 2 wrote data) and explains how they keep data consistent.",
                },
                seed_questions={
                    1: "What does a sequence diagram show, and why is it useful in low-level design?",
                    2: "Walk me through your most important flow step by step: who calls whom, what's validated, what's saved.",
                    3: "In that flow, the last step fails after an earlier step already saved data. What state is your system in, and how do you fix it?",
                },
            ),
            Concept(
                id="concurrency_edge_cases",
                name="Concurrency and edge cases",
                is_core=True,
                rubrics={
                    1: "Knows what a race condition is: the result depends on the timing of concurrent operations.",
                    2: "Identifies a realistic race condition in their project (two users acting on the same record) and describes what goes wrong.",
                    3: "Fixes it with a concrete mechanism (transaction, row lock, unique constraint, conditional update, idempotency key) and explains the trade-off.",
                },
                seed_questions={
                    1: "What is a race condition?",
                    2: "Where in your project could two users act on the same thing at the same time? What goes wrong?",
                    3: "How exactly would you prevent that? Name the mechanism and what it costs you.",
                },
            ),
        ],
        template=(
            "# Low-level design\n\n## Modules / classes\n| Module | Responsibility | Key methods |\n|---|---|---|\n\n"
            "## Key flow: <name>\n1. \n\n## Edge cases and concurrency\n- \n\n## Error handling\n"
        ),
        checklist=(
            "LLD checklist\n"
            "- Every business rule has exactly one owner\n"
            "- The key flow is written step by step, including failures\n"
            "- Race conditions are named with a concrete prevention mechanism\n"
            "- Errors are mapped to what the user sees"
        ),
    ),
    Stage(
        id="api",
        name="API documentation",
        goal="Write the contract for every endpoint so another developer could build against it.",
        concepts=[
            Concept(
                id="api_contract",
                name="API contract",
                is_core=True,
                rubrics={
                    1: "API contract = the agreed spec between client and server: endpoints, methods, request/response shapes, status codes, errors, auth. Bonus: OpenAPI/Swagger.",
                    2: "Documents one of their endpoints completely enough for a frontend developer to build against it without asking questions.",
                    3: "Explains what happens when the contract changes (e.g. renaming a field breaks clients) and how to change it safely (versioning, deprecation, additive changes).",
                },
                seed_questions={
                    1: "What is an API contract, and who relies on it?",
                    2: "Write the full contract for one of your endpoints: method, path, request body, response body, status codes, errors, auth.",
                    3: "You need to rename a field in a response that a frontend already uses. What breaks, and how do you make the change safely?",
                },
            ),
            Concept(
                id="http_semantics",
                name="HTTP methods and status codes",
                is_core=False,
                rubrics={
                    1: "Knows GET/POST/PUT/PATCH/DELETE purposes and the main status code classes (2xx, 4xx, 5xx).",
                    2: "Maps their endpoints to correct methods and picks correct status codes (e.g. 201 on create, 400 vs 422, 401 vs 403, 404, 409 for conflict).",
                    3: "Defends a non-obvious choice in their API (action endpoint vs. PATCH, 202 for async work, 409 vs 422) with reasoning.",
                },
                seed_questions={
                    1: "What are the main HTTP methods for, and what do 2xx, 4xx and 5xx status codes mean?",
                    2: "For your endpoints, which method does each use, and which status codes can each return? When is it 401 vs 403?",
                    3: "Pick one endpoint where the right method or status code isn't obvious. Defend your choice.",
                },
            ),
            Concept(
                id="idempotency_pagination",
                name="Idempotency, pagination and versioning",
                is_core=True,
                rubrics={
                    1: "Idempotent = repeating the request has the same effect as doing it once (GET, PUT, DELETE are; POST usually isn't).",
                    2: "Explains how their list endpoints paginate and how a client retry could cause duplicates in their API.",
                    3: "Designs protection against duplicate writes (idempotency key or natural unique constraint) and explains offset vs. cursor pagination trade-offs.",
                },
                seed_questions={
                    1: "What does it mean for an API request to be idempotent? Which HTTP methods usually are?",
                    2: "How do your list endpoints paginate? If a client retries a create request after a timeout, what happens in your API?",
                    3: "How would you stop retries from creating duplicates? And would you use offset or cursor pagination, and why?",
                },
            ),
        ],
        template=(
            "# API documentation\n\nBase URL: /api/v1\nAuth: \n\n"
            "## <METHOD> <path>\nDescription: \n\nRequest:\n```json\n{}\n```\n\nResponse (200/201):\n```json\n{}\n```\n\n"
            "Errors:\n| Status | When | Body |\n|---|---|---|\n"
        ),
        checklist=(
            "API checklist\n"
            "- Every endpoint lists method, path, request, response, errors and auth\n"
            "- Status codes are specific (201, 401 vs 403, 404, 409, 422)\n"
            "- List endpoints paginate\n"
            "- Non-idempotent writes are protected against retries\n"
            "- There is a versioning plan"
        ),
    ),
    Stage(
        id="db",
        name="DB design",
        goal="Design the tables, keys, relationships, indexes and constraints that support the API.",
        concepts=[
            Concept(
                id="keys_relationships",
                name="Keys and relationships",
                is_core=False,
                rubrics={
                    1: "Primary key uniquely identifies a row; foreign key references another table's primary key and enforces the relationship.",
                    2: "Identifies their entities and the one-to-many / many-to-many relationships between them, including any join tables.",
                    3: "Justifies a modelling choice in their schema (separate table vs. column, join table, soft delete) against an alternative.",
                },
                seed_questions={
                    1: "What's the difference between a primary key and a foreign key?",
                    2: "List your entities and the relationships between them. Where do you need a join table?",
                    3: "Pick one modelling decision in your schema and defend it against the obvious alternative.",
                },
            ),
            Concept(
                id="normalization",
                name="Normalization and denormalization",
                is_core=False,
                rubrics={
                    1: "Normalization = storing each fact once to avoid duplication and update anomalies.",
                    2: "Points to data in their schema that would be duplicated without normalization and what bug that would cause.",
                    3: "Names a place where they'd deliberately denormalize for read performance and how they'd keep copies in sync.",
                },
                seed_questions={
                    1: "What is normalization, and what problem does it solve?",
                    2: "Find a piece of data in your project that could easily be duplicated. What bug would duplication cause?",
                    3: "Where, if anywhere, would you denormalize on purpose? How would you keep the copies consistent?",
                },
            ),
            Concept(
                id="indexes_constraints",
                name="Indexes and constraints",
                is_core=True,
                rubrics={
                    1: "An index speeds up lookups on a column at the cost of slower writes and extra storage; constraints (NOT NULL, UNIQUE, CHECK, FK) enforce rules in the database.",
                    2: "Names the queries their API runs most and which indexes those need; lists the constraints that protect their data.",
                    3: "Uses a database constraint or transaction to enforce a business rule under concurrency (e.g. unique constraint to stop double-booking) and explains why app-level checks alone fail.",
                },
                seed_questions={
                    1: "What does a database index do, and what does it cost? What are constraints for?",
                    2: "Which queries will your API run most often? Which indexes and constraints does your schema need for them?",
                    3: "Which business rule in your project must hold even when two requests arrive at once? How does your schema enforce it?",
                },
            ),
        ],
        template=(
            "# DB design\n\n## Entities and relationships\n\n## Tables\n### <table_name>\n| Column | Type | Constraints |\n|---|---|---|\n\n"
            "## Indexes\n| Table | Columns | Query it supports |\n|---|---|---|\n\n## Business rules enforced in the DB\n- \n"
        ),
        checklist=(
            "DB design checklist\n"
            "- Every API endpoint can be served by the schema\n"
            "- Each table has a primary key; relationships use foreign keys\n"
            "- Indexes match the most frequent queries\n"
            "- Concurrency-sensitive rules are enforced by constraints or transactions\n"
            "- Any denormalization is deliberate and documented"
        ),
    ),
]

STAGE_BY_ID = {s.id: s for s in STAGES}
CONCEPT_BY_ID = {c.id: c for s in STAGES for c in s.concepts}


def stage_of(concept_id: str) -> Stage:
    for s in STAGES:
        if any(c.id == concept_id for c in s.concepts):
            return s
    raise KeyError(concept_id)
