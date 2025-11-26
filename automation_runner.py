import os
from datetime import datetime
from supabase import create_client, Client
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
BUCKET_NAME = os.getenv("SUPABASE_SCREENSHOT_BUCKET", "automation-screenshots")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def update_job(job_id: str, **fields):
    supabase.table("automation_jobs").update(fields).eq("id", job_id).execute()


def upload_screenshot(job_id: str, path: str) -> str:
    storage_path = f"{job_id}/{os.path.basename(path)}"
    with open(path, "rb") as f:
        supabase.storage.from_(BUCKET_NAME).upload(storage_path, f, {"upsert": "true"})
    public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(storage_path)
    update_job(job_id, latest_screenshot_url=public_url)
    return public_url


def run_job(job_id: str) -> None:
    # 1) Fetch script from Supabase
    res = (
        supabase.table("automation_jobs")
        .select("script")
        .eq("id", job_id)
        .single()
        .execute()
    )
    script = res.data["script"]

    # 2) Mark job as running
    update_job(job_id, status="running")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    driver = None
    try:
        # Let Selenium Manager resolve the correct ChromeDriver for the installed Chromium
        driver = webdriver.Chrome(options=options)

        # Allow generated script to call webdriver.Chrome() but always reuse this driver
        def _reuse_existing_driver(*_args, **_kwargs):
            return driver

        webdriver.Chrome = _reuse_existing_driver  # type: ignore[assignment]

        # Helper for structured logging
        def log(msg: str):
            print(msg, flush=True)

        # Helper so script can push screenshots mid-run
        def capture_screenshot(label: str | None = None) -> str:
            safe_label = label.replace(" ", "-") if label else "step"
            filename = f"{job_id}-{safe_label}-{datetime.utcnow().isoformat()}.png"
            try:
                driver.save_screenshot(filename)
            except Exception as e:
                log(f"Failed to capture screenshot '{safe_label}': {e}")
                return ""
            try:
                return upload_screenshot(job_id, filename)
            except Exception as e:
                log(f"Failed to upload screenshot '{safe_label}': {e}")
                return ""

        # Wrap driver.quit so script-level quit still works
        original_quit = driver.quit

        def _wrapped_quit(*_args, **_kwargs):
            try:
                capture_screenshot("before-quit")
            except Exception as e:
                print(f"Error capturing screenshot before quit: {e}", flush=True)
            try:
                return original_quit(*_args, **_kwargs)
            except Exception as e:
                print(f"Error while quitting driver: {e}", flush=True)
                return None

        driver.quit = _wrapped_quit  # type: ignore[assignment]

        # Execute generated script as if run as __main__
        exec_globals = {
            "driver": driver,
            "log": log,
            "capture_screenshot": capture_screenshot,
            "webdriver": webdriver,
            "WebDriverException": WebDriverException,
            "__name__": "__main__",
        }
        exec(script, exec_globals, {})

        # Final best-effort screenshot
        capture_screenshot("final")

        update_job(job_id, status="completed", error_message=None)
    except Exception as e:
        update_job(job_id, status="failed", error_message=str(e))
        raise
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
