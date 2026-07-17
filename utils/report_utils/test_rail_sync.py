# utils/report_utils/test_rail_sync.py
import os
import sys
import json
import requests
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Ensure we can log properly
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RegressionAI.TestRailSync")

# TestRail Defaults
TESTRAIL_URL = os.getenv("TESTRAIL_URL", "https://regression.testrail.io/").rstrip("/") + "/"
TESTRAIL_EMAIL = os.getenv("TESTRAIL_EMAIL", "mohit.mungra@moschip.com")
TESTRAIL_PASSWORD = os.getenv("TESTRAIL_PASSWORD", "MMP@welcome1234")
TESTRAIL_PROJECT_ID = int(os.getenv("TESTRAIL_PROJECT_ID", "3"))
TESTRAIL_SUITE_ID = os.getenv("TESTRAIL_SUITE_ID", "7")
# If TESTRAIL_RUN_ID is not provided, we can dynamically create a new run
TESTRAIL_RUN_ID = os.getenv("TESTRAIL_RUN_ID") 

def get_auth():
    return (TESTRAIL_EMAIL, TESTRAIL_PASSWORD)

def fetch_testrail_case_mapping():
    """
    Fetches all test cases from TestRail for the project and maps 'refs' (e.g., 'TC-001') to TestRail Case ID (e.g., 12345).
    """
    auth = get_auth()
    url = f"{TESTRAIL_URL}index.php?/api/v2/get_cases/{TESTRAIL_PROJECT_ID}"
    if TESTRAIL_SUITE_ID:
        url += f"&suite_id={TESTRAIL_SUITE_ID}"
    
    mapping = {}
    try:
        response = requests.get(url, auth=auth, headers={'Content-Type': 'application/json'})
        if response.status_code == 200:
            cases = response.json()
            # TestRail returns cases as a dict under 'cases' or list
            case_list = cases.get("cases", []) if isinstance(cases, dict) else cases
            for case in case_list:
                ref = case.get("refs")
                if ref:
                    # Clean the reference (e.g., "TC-001" or "TC-001, TC-002")
                    for single_ref in ref.split(","):
                        clean_ref = single_ref.strip()
                        mapping[clean_ref] = case.get("id")
            logger.info(f"🔑 Loaded {len(mapping)} TestRail Case mappings from Refs.")
        else:
            logger.error(f"❌ Failed to fetch test cases from TestRail: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"❌ Error fetching TestRail case mappings: {e}")
        
    return mapping

def get_or_create_test_run(case_ids):
    """
    Returns the active TESTRAIL_RUN_ID or creates a new one containing the specified case IDs.
    """
    if TESTRAIL_RUN_ID:
        logger.info(f"📋 Using existing TestRail Run ID: {TESTRAIL_RUN_ID}")
        return int(TESTRAIL_RUN_ID)

    auth = get_auth()
    url = f"{TESTRAIL_URL}index.php?/api/v2/add_run/{TESTRAIL_PROJECT_ID}"
    
    payload = {
        "name": f"RegressionAI Automated Run - Self-Healing Pipeline",
        "include_all": False,
        "case_ids": list(case_ids)
    }
    if TESTRAIL_SUITE_ID:
        payload["suite_id"] = int(TESTRAIL_SUITE_ID)
    
    try:
        response = requests.post(url, auth=auth, json=payload, headers={'Content-Type': 'application/json'})
        if response.status_code == 200:
            run_info = response.json()
            new_run_id = run_info.get("id")
            logger.info(f"🚀 Created new TestRail Run: {run_info.get('name')} (ID: {new_run_id})")
            return new_run_id
        else:
            logger.error(f"❌ Failed to create TestRail Run: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"❌ Error creating TestRail Run: {e}")
        
    return None

def sync_results_to_testrail(json_report_path: str):
    """
    Processes the final JSON report and uploads the results to TestRail.
    """
    if not os.path.exists(json_report_path):
        logger.error(f"❌ JSON report not found: {json_report_path}")
        return

    # Check if enabled
    if os.getenv("TESTRAIL_ENABLED", "false").lower() not in ("true", "1", "yes"):
        logger.info("ℹ️ TestRail synchronization is disabled (TESTRAIL_ENABLED is not set to true).")
        return

    logger.info(f"🔄 Syncing final results from '{json_report_path}' to TestRail...")
    
    # 1. Load results from JSON
    with open(json_report_path, "r", encoding="utf-8") as f:
        report_data = json.load(f)
    
    results = report_data.get("results", [])
    if not results:
        logger.info("⚠️ No test results found in JSON report.")
        return

    # 2. Fetch case mapping from TestRail
    ref_to_case_map = fetch_testrail_case_mapping()
    if not ref_to_case_map:
        logger.warning("⚠️ No case mappings loaded. Skipping synchronization.")
        return

    # 3. Match results with TestRail Case IDs
    testrail_results = []
    case_ids_to_run = set()
    
    for r in results:
        testid = r.get("doc_test_case_id") or r.get("testid") # e.g. "TC-001"
        if not testid or testid not in ref_to_case_map:
            logger.debug(f"Test ID '{testid}' has no corresponding TestRail Case ID mapping.")
            continue
            
        case_id = ref_to_case_map[testid]
        case_ids_to_run.add(case_id)
        
        status = r.get("status") # "passed", "failed", "healed", "skipped"
        is_healed = r.get("is_healed", False)
        
        # TestRail Status IDs: 1 = Passed, 2 = Blocked, 4 = Retest, 5 = Failed
        status_id = 5 # default to failed
        comment = ""
        
        if status in ("PASS", "PASSED"):
            status_id = 1
            comment = "✅ Test passed successfully via automation."
        elif is_healed:
            status_id = 1  # Mark as Passed if healed
            comment = f"💜 Test Auto-Healed by RegressionAI LLM.\n\nApplied Fix:\n{r.get('ai_suggested_fix')}\n\nPull Request: {r.get('pr_url')}"
        elif status == "SKIPPED":
            status_id = 2  # Blocked / Skipped
            comment = "ℹ️ Test skipped."
        else:
            status_id = 5
            comment = f"❌ Test failed.\n\nError Message:\n{r.get('failure_reason')}\n\nJira Ticket: {r.get('jira_url')}"

        testrail_results.append({
            "case_id": case_id,
            "status_id": status_id,
            "comment": comment,
            "elapsed": f"{max(1, int(r.get('duration') or 1))}s"
        })

    if not testrail_results:
        logger.warning("⚠️ No matching TestRail cases found for the test suite results.")
        return

    # 4. Get or Create Test Run
    run_id = get_or_create_test_run(case_ids_to_run)
    if not run_id:
        logger.error("❌ Could not obtain a TestRail Run ID. Aborting sync.")
        return

    # 5. Push results to the Test Run
    auth = get_auth()
    url = f"{TESTRAIL_URL}index.php?/api/v2/add_results_for_cases/{run_id}"
    
    payload = {"results": testrail_results}
    
    try:
        response = requests.post(url, auth=auth, json=payload, headers={'Content-Type': 'application/json'})
        if response.status_code == 200:
            logger.info(f"🎉 Successfully uploaded {len(testrail_results)} results to TestRail Run R{run_id}!")
        else:
            logger.error(f"❌ Failed to upload results to TestRail: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"❌ Error uploading results to TestRail: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        sync_results_to_testrail(sys.argv[1])
    else:
        print("Usage: python test_rail_sync.py <json_report_path>")
