import os

# ------------------------------------------
# Generate Jira bug configs
# ------------------------------------------

# Jira Connection Settings
JIRA_SERVER = os.getenv("JIRA_SERVER", "https://krupali-bhadaraka-moschip.atlassian.net")
JIRA_USERNAME = os.getenv("JIRA_USERNAME", "mohit.mungra@moschip.com")
JIRA_PASSWORD = os.getenv("JIRA_PASSWORD", "")

# Jira Issue Settings
ASSIGNEE_EMAIL = "mohit.mungra@moschip.com"
ISSUE_TYPE = "Bug"
JIRA_PROJECT_KEY = "SCRUM"
JIRA_BOARD_NAME = "My Scrum Space"
JIRA_SPRINT_NAME = "SCRUM Sprint 1"

# Master toggle for automated Jira bug creation on test failure.
# Set to False (or env CREATE_JIRA=false/0) to skip Jira ticket creation.
CREATE_JIRA = os.getenv("CREATE_JIRA", "false").strip().lower() in ("true", "1", "yes")
 
# Excel Settings
SHEET_NAME = "Test Details"
COLUMN_STATUS = "Status"
COLUMN_TITLE = "Test Case Name"
COLUMN_STEPS = "Steps"
COLUMN_EXPECTED_RESULT = "Expected Output"
COLUMN_DESCRIPTION = "Description"
COLUMN_FAILURE_REASON = "Message"
COLUMN_JIRA_ID = "JiraID"
JIRA_ID_COLUMN_WIDTH = 20
COLUMN_AI_SUMMARY = "Short Summary"
COLUMN_AI_FIX = "Suggested Fix"
COLUMN_CREATE_JIRA = "CreateJira"
CREATE_JIRA_COLUMN_WIDTH = 15


# ------------------------------------------
# update_jira_status configs
# ------------------------------------------
JIRA_RESOLVE_COMMENT = "Tested the issue and is resolved!"
JIRA_TRANSITION_NAME = "Done"
EXCEL_RESOLVED_LABEL = "Resolved"
COLUMN_JIRA_STATUS = "Jira Status"
JIRA_STATUS_COLUMN_WIDTH = 20

# ------------------------------------------
# TestRail Settings
# ------------------------------------------
TESTRAIL_ENABLED = os.getenv("TESTRAIL_ENABLED", "false").strip().lower() in ("true", "1", "yes")
TESTRAIL_URL = os.getenv("TESTRAIL_URL", "https://regression.testrail.io/")
TESTRAIL_EMAIL = os.getenv("TESTRAIL_EMAIL", "mohit.mungra@moschip.com")
TESTRAIL_PASSWORD = os.getenv("TESTRAIL_PASSWORD", "MMP@welcome1234")
TESTRAIL_PROJECT_ID = int(os.getenv("TESTRAIL_PROJECT_ID", "3"))
TESTRAIL_SUITE_ID = os.getenv("TESTRAIL_SUITE_ID", "7")
TESTRAIL_RUN_ID = os.getenv("TESTRAIL_RUN_ID")


