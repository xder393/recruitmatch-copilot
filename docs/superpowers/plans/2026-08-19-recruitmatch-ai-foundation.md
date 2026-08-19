# RecruitMatch AI Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an OpenAI-compatible model gateway, privacy-safe tracing, and evidence-validated LLM resume parsing with deterministic fallback.

**Architecture:** Recruiting code depends on a structured-model protocol. Production uses an OpenAI-compatible adapter; tests use deterministic fakes. Model facts are accepted only when exact evidence resolves against the resume, and failures fall back to the heuristic parser.

**Tech Stack:** Python 3.9+, FastAPI, Pydantic v2, OpenAI SDK, SQLAlchemy 2, Alembic, pytest.

**Spec:** docs/superpowers/specs/2026-08-19-recruitmatch-ai-layer-design.md

## Global Constraints

- No-key startup and rules-v1 remain available.
- Only ModelGateway may call an external chat model.
- Tests use no network or real credentials.
- Traces exclude full resumes, full prompts, API keys, and full responses.
- Sensitive recruiting traits and unsupported facts never enter matching profiles.

---

### Task 1: Structured Model Gateway

**Files:**
- Create: app/ai/__init__.py
- Create: app/ai/contracts.py
- Create: app/ai/gateway.py
- Create: tests/ai/fakes.py
- Modify: app/config.py
- Modify: .env.example
- Test: tests/ai/test_gateway.py
- Test: tests/test_config.py

**Interfaces:**
- Produces: ModelRequest[T](operation, prompt_version, system, user, schema).
- Produces: ModelResponse[T](value, provider, model, input_tokens, output_tokens, estimated_cost, latency_ms).
- Produces: StructuredModel.generate(request: ModelRequest[T]) -> ModelResponse[T].
- Produces: OpenAICompatibleGateway.generate(request) -> ModelResponse.
- Produces: FakeStructuredModel and RaisingModel reusable by offline AI tests.

- [ ] **Step 1: Write failing contract and configuration tests**

~~~python
def test_ai_is_disabled_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert Settings.load().ai_enabled is False

def test_fake_model_returns_validated_value():
    fake = FakeStructuredModel({"resume_extract": ResumeProfile()})
    request = ModelRequest("resume_extract", "resume-extract-v1", "extract", "Python", ResumeProfile)
    assert fake.generate(request).value == ResumeProfile()
~~~

- [ ] **Step 2: Run tests and confirm red state**

Run: .venv/bin/pytest tests/ai/test_gateway.py tests/test_config.py -q  
Expected: FAIL because the contracts and AI settings are absent.

- [ ] **Step 3: Implement contracts, fake, settings, and adapter**

Add AI_ENABLED=false, MODEL_TIMEOUT_SECONDS=20, MODEL_MAX_RETRIES=1, prompt versions, retrieval limits, maximum evidence characters, and per-million input/output token prices used only for estimates. Settings.validate() requires a key only when AI is enabled. The adapter uses client.beta.chat.completions.parse with request.schema, retries retryable timeout/rate-limit/transport failures once, and raises ModelGatewayError(code, retryable).

- [ ] **Step 4: Verify and commit**

~~~bash
.venv/bin/pytest tests/ai/test_gateway.py tests/test_config.py -q
.venv/bin/ruff check app/ai app/config.py tests/ai tests/test_config.py
git add app/ai app/config.py .env.example tests/ai tests/test_config.py
git commit -m "feat: add structured model gateway"
~~~

### Task 2: Privacy-Safe Model Traces

**Files:**
- Modify: app/models/operations.py
- Create: app/models/prompts.py
- Create: app/repositories/model_traces.py
- Create: app/repositories/prompts.py
- Create: alembic/versions/20260819_05_ai_trace_fields.py
- Test: tests/ai/test_model_traces.py
- Modify: tests/foundation/test_migrations.py

**Interfaces:**
- Consumes: ModelRequest, ModelResponse, and ModelGatewayError.
- Produces: ModelTraceWriter.succeeded(tenant_id, business_type, business_id, source_ids, request, response) -> ModelTrace.
- Produces: ModelTraceWriter.failed(tenant_id, business_type, business_id, source_ids, request, error, latency_ms) -> ModelTrace.
- Produces: PromptVersion(operation, version, schema_version, template_fingerprint, is_active).
- Produces: PromptRepository.active(tenant_id, operation) -> PromptVersion.

- [ ] **Step 1: Write a failing privacy test**

~~~python
def test_trace_contains_metadata_not_prompt(session, trace_writer, request, response):
    trace = trace_writer.succeeded("tenant", "resume_extract", "resume-id", request, response)
    assert trace.prompt_version == "resume-extract-v1"
    assert "candidate-private-text" not in " ".join(map(str, vars(trace).values()))
~~~

- [ ] **Step 2: Run tests and confirm red state**

Run: .venv/bin/pytest tests/ai/test_model_traces.py tests/foundation/test_migrations.py -q  
Expected: FAIL because the writer and new metadata are absent.

- [ ] **Step 3: Implement migration and writer**

Add operation, nullable fallback_reason, and request_fingerprint. The fingerprint is SHA-256 of operation, prompt version, and caller-supplied source IDs, never prompt text. Add prompt_versions with unique (tenant_id, operation, version), schema version, safe template fingerprint, active flag, and timestamps. Index traces by (tenant_id, created_at) and (tenant_id, operation, status).

- [ ] **Step 4: Verify migration and commit**

~~~bash
TASK_DB=$(mktemp -d)/ai.db
DATABASE_URL="sqlite:///$TASK_DB" .venv/bin/alembic upgrade head
.venv/bin/pytest tests/ai/test_model_traces.py tests/foundation/test_migrations.py -q
git add app/models/operations.py app/models/prompts.py app/repositories/model_traces.py app/repositories/prompts.py alembic/versions/20260819_05_ai_trace_fields.py tests/ai/test_model_traces.py tests/foundation/test_migrations.py
git commit -m "feat: trace model calls safely"
~~~

### Task 3: Evidence-Validated LLM Resume Parser

**Files:**
- Modify: app/resumes/schemas.py
- Create: app/ai/evidence.py
- Create: app/ai/resume_parser.py
- Test: tests/ai/test_llm_resume_parser.py
- Modify: tests/resumes/test_resume_parser.py

**Interfaces:**
- Produces: ProjectEvidence(name, description, evidence).
- Produces: LLMResumeParser.parse(text: str) -> ResumeProfile.
- Produces: LLMResumeParser.last_outcome: ParseOutcome(mode, invalid_fact_count, fallback_reason).

- [ ] **Step 1: Write grounding and fallback tests**

~~~python
def test_parser_drops_fact_whose_span_does_not_resolve():
    fake = FakeStructuredModel({"resume_extract": llm_profile(skill="Kubernetes", start=0, end=10)})
    result = LLMResumeParser(fake, HeuristicResumeParser()).parse("Python developer")
    assert [item.name for item in result.skills] == ["Python"]

def test_gateway_failure_uses_heuristic_parser():
    parser = LLMResumeParser(RaisingModel("timeout"), HeuristicResumeParser())
    assert parser.parse("3年 Python").experience_years == 3
    assert parser.last_outcome.fallback_reason == "timeout"
~~~

- [ ] **Step 2: Run tests and confirm red state**

Run: .venv/bin/pytest tests/ai/test_llm_resume_parser.py -q  
Expected: FAIL because parser and evidence validation are absent.

- [ ] **Step 3: Implement schema and exact validation**

Add default-empty projects and optional evidence-bearing experience/education while retaining old profile JSON compatibility. Evidence is valid only when 0 <= start < end <= len(source) and source[start:end] equals evidence.text. Drop unsupported or sensitive facts; merge grounded heuristic skills by normalized name.

- [ ] **Step 4: Implement prompt and fallback**

The prompt requires explicit facts, null for unknown values, exact source spans, and exclusion of sensitive traits. Cap input with max_evidence_characters. Invalid schema output gets one repair request containing only the validation error and original bounded input; a second failure invokes the heuristic parser. Gateway failures also fall back and set ParseOutcome for tracing.

- [ ] **Step 5: Verify and commit**

~~~bash
.venv/bin/pytest tests/ai/test_llm_resume_parser.py tests/resumes/test_resume_parser.py tests/resumes/test_resume_processing.py -q
git add app/resumes/schemas.py app/ai/evidence.py app/ai/resume_parser.py tests/ai/test_llm_resume_parser.py tests/resumes/test_resume_parser.py
git commit -m "feat: parse resumes with grounded llm output"
~~~

### Task 4: Runtime Wiring and AI Status

**Files:**
- Modify: app/main.py
- Modify: app/api/v1/operations.py
- Modify: app/services/resume_processing.py
- Modify: app/tasks/celery_app.py
- Test: tests/ai/test_ai_readiness.py
- Modify: tests/resumes/test_resume_processing.py

**Interfaces:**
- Produces: GET /api/v1/ai/status with enabled, provider, model, embedding_model, and core_available.

- [ ] **Step 1: Write no-key and fake-model integration tests**

~~~python
def test_ai_disabled_does_not_disable_core(client):
    body = client.get("/api/v1/ai/status").json()
    assert body["enabled"] is False
    assert body["core_available"] is True
~~~

The enabled test injects a fake model, processes one resume, and asserts grounded profile persistence plus one privacy-safe trace.

- [ ] **Step 2: Run tests and confirm red state**

Run: .venv/bin/pytest tests/ai/test_ai_readiness.py tests/resumes/test_resume_processing.py -q  
Expected: FAIL because runtime selection and endpoint are absent.

- [ ] **Step 3: Wire parser selection and status**

Build LLMResumeParser only when AI is enabled; otherwise use HeuristicResumeParser. Keep database readiness independent and make the status endpoint configuration-only so it never calls the provider.

- [ ] **Step 4: Verify foundation and commit**

~~~bash
.venv/bin/pytest tests/ai tests/resumes tests/operations -q
.venv/bin/ruff check app tests scripts
git diff --check
git add app/main.py app/api/v1/operations.py app/services/resume_processing.py app/tasks/celery_app.py tests/ai/test_ai_readiness.py tests/resumes/test_resume_processing.py
git commit -m "feat: wire resilient ai resume processing"
~~~
