import os
import sys
from datetime import datetime
from supabase import create_client, Client
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException

SUPABASE_URL = os.environ["SUPABASE_URL"]
# Prefer the service role key env, but also allow SUPABASE_SERVICE_KEY
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
)
BUCKET_NAME = os.getenv("SUPABASE_SCREENSHOT_BUCKET", "automation-screenshots")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def update_job(job_id: str, **fields):
    supabase.table("automation_jobs").update(fields).eq("id", job_id).execute()


def upload_screenshot(job_id: str, path: str, update_latest: bool = True) -> str:
    """
    Upload a screenshot to Supabase Storage.

    If update_latest is True, also update automation_jobs.latest_screenshot_url.
    This lets us avoid overwriting the last meaningful screenshot with
    maintenance shots like 'before-quit' or 'final'.
    """
    storage_path = f"{job_id}/{os.path.basename(path)}"
    with open(path, "rb") as f:
        supabase.storage.from_(BUCKET_NAME).upload(storage_path, f, {"upsert": "true"})
    public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(storage_path)

    if update_latest:
        update_job(job_id, latest_screenshot_url=public_url)

    return public_url


def run_job(job_id: str) -> None:
    # In‑DB status + log buffer
    update_job(job_id, status="running")
    log_lines: list[str] = []

    def log(msg: str):
        text = str(msg)
        print(text, flush=True)
        log_lines.append(text)

    try:
        # 1) Fetch script from Supabase
        res = (
            supabase.table("automation_jobs")
            .select("script")
            .eq("id", job_id)
            .single()
            .execute()
        )
        script = res.data["script"]

        log(f"Starting automation job {job_id}...")

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        driver = None
        try:
            # Let Selenium Manager resolve the correct ChromeDriver
            driver = webdriver.Chrome(options=options)

            # Allow generated script to call webdriver.Chrome() but always reuse this driver
            def _reuse_existing_driver(*_args, **_kwargs):
                return driver

            webdriver.Chrome = _reuse_existing_driver  # type: ignore[assignment]

            # Screenshot helper exposed to the script
            def capture_screenshot(label: str | None = None, update_latest: bool = True) -> str:
                safe_label = label.replace(" ", "-") if label else "step"
                filename = f"{job_id}-{safe_label}-{datetime.utcnow().isoformat()}.png"
                try:
                    driver.save_screenshot(filename)
                except Exception as e:
                    log(f"Failed to capture screenshot '{safe_label}': {e}")
                    return ""
                try:
                    return upload_screenshot(job_id, filename, update_latest=update_latest)
                except Exception as e:
                    log(f"Failed to upload screenshot '{safe_label}': {e}")
                    return ""

            # Wrap driver.quit so scripts that call it still produce a debug screenshot
            # but do NOT overwrite latest_screenshot_url.
            original_quit = driver.quit

            def _wrapped_quit(*_args, **_kwargs):
                try:
                    capture_screenshot("before-quit", update_latest=False)
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
                "_log": log,
                "log_step": log,          # <— IMPORTANT: what your script is calling
                "capture_screenshot": capture_screenshot,
                "webdriver": webdriver,
                "WebDriverException": WebDriverException,
                "sys": sys,
                "__name__": "__main__",
            }

            log("Executing generated script...")
            exec(script, exec_globals, {})
            log("Script execution finished.")

            # Final best-effort screenshot for debugging, but do NOT overwrite
            # latest_screenshot_url that came from the script itself.
            try:
                capture_screenshot("final", update_latest=False)
            except Exception as e:
                log(f"Failed to capture final screenshot: {e}")

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        fields = {"status": "completed", "error_message": None}
        if log_lines:
            fields["log_output"] = "\n".join(log_lines)
        update_job(job_id, **fields)
        log(f"Job {job_id} completed.")
    except Exception as e:
        # Any unhandled error should be recorded on the job
        err_msg = f"Unhandled exception in run_job for job {job_id}: {e}"
        print(err_msg, flush=True)
        log_lines.append(err_msg)
        fields = {"status": "failed", "error_message": str(e)}
        if log_lines:
            fields["log_output"] = "\n".join(log_lines)
        update_job(job_id, **fields)
        raise
