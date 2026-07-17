# 🤖 RegressionAI: Autonomous Self-Healing Test Pipeline

RegressionAI is a state-of-the-art, **LangGraph-powered agentic regression testing framework** designed to execute tests, parse failures, analyze root causes using Large Language Models (LLMs), and autonomously apply code fixes (self-healing) to either application code or tests. 

By linking test outcomes directly to your project management (Jira) and version control (GitHub) platforms, it streamlines CI/CD workflows and minimizes human triage effort.

---

## 🏗️ Architecture & Decision Flow

The framework operates as a LangGraph-based state machine, orchestrating individual agent nodes from failure parsing to ticketing and PR generation.

![Self-Healing Decision Matrix](https://raw.githubusercontent.com/mohit-mungra-moschip/agentic_pipeline/main/docs/images/self_healing_decision_matrix_pro.png)

### Key Stages of the Pipeline:
1. **Test Execution**: Runs the test suite via pytest. If everything passes, the pipeline generates reports and exits.
2. **Failure Parsing**: If tests fail, it parses the JUnit XML output to extract structured traceback metadata.
3. **LLM Failure Analysis**: Classifies each test failure into one of four categories:
   * `TEST_BUG`: Flaky or outdated test assertions.
   * `APP_BUG`: A genuine regression bug in the application source code.
   * `ENV_ISSUE`: Environment-related problems (e.g., missing dependencies, database locks).
   * `SCHEMA / UNKNOWN`: Database schema changes or unclassified failures.
4. **Jira Creation**: Creates a Jira ticket on the sprint board to track the failure.
5. **Apply LLM Fix (Jira-Linked)**: Generates and applies a targeted patch to the source code (app or test) corresponding to the classification.
6. **Test Re-Execution**: Re-runs the failed tests to validate the repair.
   * **If Pass**: Creates a GitHub Pull Request with an AI-generated change description and moves the Jira ticket to **In Review**.
   * **If Fail**: Checks `max_attempts`. If not reached, it loops back to analysis. If reached, it flags the issue and moves the Jira ticket to **TODO**.

---

## 🌟 Key Features

* **Intelligent Self-Healing**: Automatically writes and applies fixes to python files.
* **Unified Reporting**: Generates a rich Excel dashboard (`.xlsx`), interactive HTML summary, and a raw JSON file capturing the exact pipeline state.
* **Smart Dashboarding**: Translates healed tests to violet/yellow highlights in Excel, displaying a 100% success rate if all failures were successfully auto-healed.
* **Jira & GitHub Sync**: Programmatically opens Jira issues, transitions them across states (TODO → In Review), and opens pull requests for healed code.

---

## 📂 Project Structure

```
agentic_pipeline_tests/
├── RegressionAI/               # Core LangGraph implementation
│   ├── agents/                 # Graph nodes (python modules per step)
│   │   ├── node_1_test_runner.py
│   │   ├── node_2_parse_failures.py
│   │   ├── node_3_fetch_files.py
│   │   ├── node_4_failure_analysis.py
│   │   ├── node_5_root_cause_analyzer.py
│   │   ├── node_6_self_healing.py
│   │   ├── node_7_action_recommender.py
│   │   └── node_8_jira_agent.py
│   ├── skills/                 # LLM system prompts and code editor helper tools
│   ├── graph.py                # Graph assembly and routing logic
│   └── state.py                # Graph state definitions (AgentState)
├── common_utils/               # Common helper utilities
│   ├── ai_wrapper.py           # LLM client abstractions
│   ├── token_tracker.py        # Pipeline-wide token/cost tracking
│   └── logger.py               # Standardized logging
├── tests/                      # Python Test Suite
│   ├── unit/                   # Unit tests (models, crud operations)
│   ├── integration/            # API integration tests
│   └── e2e/                    # Full E2E user workflow tests
├── utils/                      # Helper scripts
│   └── report_utils/           # HTML & Excel report generation & PR updates
├── config.py                   # Environment config variables
├── conftest.py                 # Pytest hooks for metadata collection
└── regression_runner.py        # Main execution entrypoint CLI
```

---

## 🚀 How to Run

### Prerequisite Environment Variables
Create a `.env` file with your credentials:
```env
# LLM Providers (at least one is required)
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AIzaSy...
OPENAI_API_KEY=sk-...

# Jira Credentials
JIRA_SERVER=https://your-domain.atlassian.net
JIRA_USERNAME=your-email@domain.com
JIRA_API_TOKEN=your-jira-token

# GitHub Credentials
GITHUB_TOKEN=ghp_...
```

### Running the Regression Runner CLI
Run the main script using the virtual environment:
```bash
python regression_runner.py --test-command "pytest tests/ -v" --max-iterations 3
```

**Options:**
* `--test-command` / `-c`: Command string to run pytest suite (default: `pytest tests/ -v --tb=short --junitxml=logs/test-results.xml`).
* `--max-iterations` / `-i`: Maximum self-healing loops for failed tests (default: `3`).
* `--verbose` / `-v`: Enable debug level console output.

---

## 📊 Reports and Artifacts

After every execution, the runner consolidates reports inside the `reports/` folder:
1. `test_results_<timestamp>.json`: Complete agent status, tracebacks, token usage, and applied fixes.
2. `test_results_<timestamp>.html`: Interactive visual summary.
3. `test_results_<timestamp>.xlsx`: Professional multi-sheet Excel dashboard featuring total counts, breakdown by modules, healed test summaries, and direct PR/Jira hyperlinks.

---

## 🎛️ TestRail Integration

The RegressionAI pipeline is integrated with **TestRail** to dynamically upload and synchronize test results after self-healing:

1. **Mapping**: Automated test cases are decorated with `@pytest.mark.testid("TC-XXX")`. When results are synced, the framework dynamically queries your TestRail project's test cases and maps the `refs` attribute (e.g. `TC-001`) to the corresponding TestRail Case ID.
2. **Uploading Results**: Results are posted to TestRail at the end of the pipeline run. If a test has been auto-healed, its status is marked as **Passed** in TestRail, with comments detailing the applied LLM patch and the created GitHub Pull Request link.

### TestRail Configuration Variables:
Add these to your `.env` file to enable TestRail sync:
```env
TESTRAIL_ENABLED=true
TESTRAIL_URL=https://regression.testrail.io/
TESTRAIL_EMAIL=mohit.mungra@moschip.com
TESTRAIL_PASSWORD=NimDg72S93AOFi55OfAW-R7KoK0ZK6yXTZtivR.Pk
TESTRAIL_PROJECT_ID=3
TESTRAIL_RUN_ID=1234  # Leave empty to dynamically create a new run per execution
```

