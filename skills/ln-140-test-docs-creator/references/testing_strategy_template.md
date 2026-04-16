# Testing Strategy

Universal testing philosophy and strategy for modern software projects: principles, organization, and best practices.

<!-- SCOPE: Testing philosophy, risk-based strategy, test organization, isolation patterns, what to test ONLY. -->
<!-- DO NOT add here: project structure, framework-specific patterns, CI/CD configuration, test tooling setup -->

## Quick Navigation

- **Tests Organization:** [tests/README.md](../../tests/README.md) - Directory structure, Story-Level Pattern, running tests
- **Test Inventory:** [tests/unit/REGISTRY.md](../../tests/unit/REGISTRY.md), [tests/integration/REGISTRY.md](../../tests/integration/REGISTRY.md), [tests/e2e/REGISTRY.md](../../tests/e2e/REGISTRY.md)

---

## Core Philosophy

### Test YOUR Code, Not Frameworks

**Focus testing effort on YOUR business logic and integration usage.** Do not retest database constraints, ORM internals, framework validation, or third-party library mechanics.

**Rule of thumb:** If deleting your code wouldn't fail the test, you're testing someone else's code.

### Examples

| Verdict | Test Description | Rationale |
|---------|-----------------|-----------|
| ✅ **GOOD** | Custom validation logic raises exception for invalid input | Tests YOUR validation rules |
| ✅ **GOOD** | Repository query returns filtered results based on business criteria | Tests YOUR query construction |
| ✅ **GOOD** | API endpoint returns correct HTTP status for error scenarios | Tests YOUR error handling |
| ❌ **BAD** | Database enforces UNIQUE constraint on email column | Tests database, not your code |
| ❌ **BAD** | ORM model has correct column types and lengths | Tests ORM configuration, not logic |
| ❌ **BAD** | Framework validates request body matches schema | Tests framework validation |

---

## Risk-Based Testing Strategy

### Priority Matrix

**Automate only high-value scenarios** using Business Impact (1-5) × Probability (1-5).

| Priority Score | Action | Example Scenarios |
|----------------|--------|-------------------|
| **≥15** | MUST test | Payment processing, authentication, data loss scenarios |
| **10-14** | Consider testing | Edge cases with moderate impact |
| **<10** | Skip automated tests | Low-probability edge cases, framework behavior |

### Test Usefulness Criteria

**No numerical targets.** Test count driven by risk assessment, not volume goals.

Every test must pass ALL 6 Usefulness Criteria:

| # | Criterion | Question |
|---|-----------|----------|
| 1 | **Risk Priority ≥15** | Business Impact × Probability ≥15? |
| 2 | **Confidence ROI** | Meaningful confidence vs maintenance cost? |
| 3 | **Behavioral** | Tests behavior, not implementation? |
| 4 | **Predictive** | Passing test = works in production? |
| 5 | **Specific** | Failed → cause immediately obvious? |
| 6 | **Non-Duplicative** | Unique value not covered by other tests? |

**Key principles:**
- **No test pyramids** - Test distribution based on risk, not arbitrary ratios
- **Every test must add value** - Each test should validate unique Priority ≥15 scenario
- **Baseline** - Positive + negative E2E per endpoint (methodology, not count target)

---

## Story-Level Testing Pattern

### When to Write Tests

**Consolidate ALL tests in Story's final test task** AFTER implementation + manual verification.

| Task Type | Contains Tests? | Rationale |
|-----------|----------------|-----------|
| **Implementation Tasks** | ❌ NO tests | Focus on implementation only |
| **Final Test Task** | ✅ ALL tests | Complete Story coverage after manual verification |

### Benefits

1. **Complete context** - Tests written when all code implemented
2. **No duplication** - E2E covers integration paths, no need to retest same code
3. **Better prioritization** - Manual testing identifies Priority ≥15 scenarios before automation
4. **Atomic delivery** - Story delivers working code + comprehensive tests together

### Anti-Pattern Example

| ❌ Wrong Approach | ✅ Correct Approach |
|-------------------|---------------------|
| Task 1: Implement feature X + write unit tests<br>Task 2: Update integration + write integration tests<br>Task 3: Add logging + write E2E tests | Task 1: Implement feature X<br>Task 2: Update integration points<br>Task 3: Add logging<br>**Task 4 (Final): Write ALL tests (2 E2E, 3 Integration, 8 Unit)** |
| **Result:** Tests scattered, duplication, incomplete coverage | **Result:** Tests consolidated, no duplication, complete coverage |

---

## Test Organization

### Directory Structure

```
tests/
├── e2e/              # End-to-end tests (full system, real services)
│   ├── test_user_journey.ext
│   └── REGISTRY.md   # E2E test inventory
├── integration/      # Integration tests (multiple components, real dependencies)
│   ├── api/
│   ├── services/
│   ├── db/
│   └── REGISTRY.md   # Integration test inventory
├── unit/             # Unit tests (single component, mocked dependencies)
│   ├── api/
│   ├── services/
│   ├── db/
│   └── REGISTRY.md   # Unit test inventory
└── README.md         # Test documentation
```

### Test Inventory (REGISTRY.md)

**Each test category has REGISTRY.md** with detailed test descriptions:

**Purpose:**
- Document what each test validates
- Track test counts per Epic/Story
- Provide navigation for test maintenance

**Format example:**

```markdown
# E2E Test Registry

## Quality Estimation (Epic 6 - API-69)

**File:** tests/e2e/test_quality_estimation.ext

**Tests (4):**
1. **evaluate_endpoint_batch_splitting** - MetricX batch splitting (segments >128 split into batches)
2. **evaluate_endpoint_gpu_integration** - MetricX-24 GPU service integration
3. **evaluate_endpoint_error_handling** - Service timeout handling (503 status)
4. **evaluate_endpoint_response_format** - Response schema validation

**Total:** 4 E2E tests | **Coverage:** 100% Priority ≥15 scenarios
```

---

## Test Levels

### E2E (End-to-End) Tests

**Definition:** Full system tests with real external services and complete data flow.

**Characteristics:**
- Real external APIs/services
- Real database
- Full request-response cycle
- Validates complete user journeys

**When to write:**
- Critical user workflows (authentication, payments, core features)
- Integration with external services
- Priority ≥15 scenarios that span multiple systems

**Example:** User registration flow (E2E) vs individual validation function (Unit)

### Integration Tests

**Definition:** Tests multiple components together with real dependencies (database, cache, file system).

**Characteristics:**
- Real database/cache/file system
- Multiple components interact
- May mock external APIs
- Validates component integration

**When to write:**
- Database query behavior
- Service orchestration
- Component interaction
- API endpoint behavior (without external services)

**Example:** Repository query with real database vs service logic with mocked repository

### Unit Tests

**Definition:** Tests single component in isolation with mocked dependencies.

**Characteristics:**
- Fast execution (<1ms per test)
- No external dependencies
- Mocked collaborators
- Validates single responsibility

**When to write:**
- Business logic validation
- Complex calculations
- Error handling logic
- Custom transformations

**Example:** Validation function with mocked data vs endpoint with real database

---

## Isolation Patterns

### Pattern Comparison

| Pattern | Speed | Complexity | Best For |
|---------|-------|------------|----------|
| **Data Deletion** | ⚡⚡⚡ Fastest | Simple | Default choice (90% of projects) |
| **Transaction Rollback** | ⚡⚡ Fast | Moderate | Transaction semantics testing |
| **Database Recreation** | ⚡ Slow | Simple | Maximum isolation paranoia |

### Data Deletion (Default)

**How it works:**
1. Create schema once at test session start
2. Delete data after each test
3. Drop schema at test session end

**Benefits:**
- Fast (5-8s for 50 tests)
- Simple implementation
- Full isolation between tests

**When to use:** Default choice for most projects

### Transaction Rollback

**How it works:**
1. Start transaction before each test
2. Run test code
3. Rollback transaction after test

**Benefits:**
- Good for testing transaction semantics
- Faster than DB recreation

**When to use:** Testing transaction behavior, savepoints, isolation levels

### Database Recreation

**How it works:**
1. Drop and recreate database before each test
2. Apply migrations
3. Run test

**Benefits:**
- Maximum isolation
- Catches migration issues

**When to use:** Paranoia about shared state, testing migrations

---

## What To Test vs NOT Test

### ✅ Test (GOOD)

**Test YOUR code and integration usage:**

| Category | Examples |
|----------|----------|
| **Business logic** | Validation rules, orchestration, error handling, computed properties |
| **Query construction** | Filters, joins, aggregations, pagination |
| **API behavior** | Request validation, response shape, HTTP status codes |
| **Custom validators** | Complex validation logic, transformations |
| **Integration smoke** | Database connectivity, basic CRUD, configuration |

### ❌ Avoid (BAD)

**Don't test framework internals and third-party libraries:**

| Category | Examples |
|----------|----------|
| **Database constraints** | UNIQUE, FOREIGN KEY, NOT NULL, CHECK constraints |
| **ORM internals** | Column types, table creation, metadata, relationships |
| **Framework validation** | Request body validation, dependency injection, routing |
| **Third-party libraries** | HTTP client behavior, serialization libraries, cryptography |

---

## Testing Patterns

### Arrange-Act-Assert

**Structure tests clearly:**

```
test_example:
    # ARRANGE: Set up test data and dependencies
    setup_data()
    mock_dependencies()

    # ACT: Execute code under test
    result = execute_operation()

    # ASSERT: Verify outcomes
    assert result == expected
    verify_side_effects()
```

**Benefits:**
- Clear test structure
- Easy to read and maintain
- Explicit test phases

### Mock at the Seam

**Mock at component boundaries, not internals:**

| Test Type | What to Mock | What to Use Real |
|-----------|--------------|------------------|
| **Unit tests** | External dependencies (repositories, APIs, file system) | Business logic |
| **Integration tests** | External APIs, slow services | Database, cache, your code |
| **E2E tests** | Nothing (or minimal external services) | Everything |

**Anti-pattern:** Over-mocking your own code defeats the purpose of integration tests.

### Test Data Builders

**Create readable test data:**

```
# Builder pattern for test data
user = build_user(
    email="test@example.com",
    role="admin",
    active=True
)

# Easy to create edge cases
inactive_user = build_user(active=False)
guest_user = build_user(role="guest")
```

**Benefits:**
- Readable test setup
- Easy edge case creation
- Reusable across tests

---

## Common Issues

### Flaky Tests

**Symptom:** Tests pass/fail randomly without code changes

**Common causes:**
- Shared state between tests (global variables, cached data)
- Time-dependent logic (timestamps, delays)
- External service instability
- Improper cleanup between tests

**Solutions:**
- Isolate test data (per-test creation, cleanup)
- Mock time-dependent code
- Use test-specific configurations
- Implement proper teardown

### Slow Tests

**Symptom:** Test suite takes too long (>30s for 50 tests)

**Common causes:**
- Database recreation per test
- Running migrations per test
- No connection pooling
- Too many E2E tests

**Solutions:**
- Use Data Deletion pattern
- Run migrations once per session
- Optimize test data creation
- Balance test levels (more Unit, fewer E2E)

### Test Coupling

**Symptom:** Changing one component breaks many unrelated tests

**Common causes:**
- Tests depend on implementation details
- Shared test fixtures across unrelated tests
- Testing framework internals instead of behavior

**Solutions:**
- Test behavior, not implementation
- Use independent test data per test
- Focus on public APIs, not internal state

---

## Coverage Guidelines

### Targets

| Layer | Target | Priority |
|-------|--------|----------|
| **Critical business logic** | 100% branch coverage | HIGH |
| **Repositories/Data access** | 90%+ line coverage | HIGH |
| **API endpoints** | 80%+ line coverage | MEDIUM |
| **Utilities/Helpers** | 80%+ line coverage | MEDIUM |
| **Overall** | 80%+ line coverage | MEDIUM |

### What Coverage Means

**Coverage is a tool, not a goal:**
- ✅ High coverage + focused tests = good quality signal
- ❌ High coverage + meaningless tests = false confidence
- ❌ Low coverage = blind spots in testing

**Focus on:**
- Critical paths covered
- Edge cases tested
- Error handling validated

**Not on:**
- Arbitrary percentage targets
- Testing getters/setters
- Framework code

---

## Verification Checklist

### Strategy

- [ ] Risk-based selection (Priority ≥15)
- [ ] Each test passes all 6 Usefulness Criteria
- [ ] Tests target YOUR code, not framework internals
- [ ] E2E smoke tests for critical integrations

### Organization

- [ ] Story-Level Test Task Pattern followed
- [ ] Tests consolidated in final Story task
- [ ] REGISTRY.md files maintained for all test categories
- [ ] Test directory structure follows conventions

### Isolation

- [ ] Isolation pattern chosen (Data Deletion recommended)
- [ ] Each test creates own data
- [ ] Proper cleanup between tests
- [ ] No shared state between tests

### Quality

- [ ] Tests are order-independent
- [ ] Tests run fast (<10s for 50 integration tests)
- [ ] No flaky tests
- [ ] Coverage ≥80% overall, 100% for critical logic
- [ ] Meaningful test names and descriptions

---

## Maintenance

**Update Triggers:**
- New testing patterns discovered
- Framework version changes affecting tests
- Significant changes to test architecture
- New isolation issues identified

**Verification:** Review this strategy when starting new projects or experiencing test quality issues.

**Last Updated:** [CURRENT_DATE] - Initial universal testing strategy
