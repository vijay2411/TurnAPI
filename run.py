#!/usr/bin/env python3
"""Simple startup helper for the browser chat bridge."""

import os
import subprocess
import sys
import shutil
from pathlib import Path

DEFAULT_TARGET_URL = os.getenv("BROWSER_CHAT_URL", "https://consensus.app/search/")
DEFAULT_BROWSER_PROFILE_DIR = Path(
    os.getenv("BROWSER_CHAT_PROFILE_DIR", Path(__file__).resolve().parent / ".browser-profile")
).resolve()
DEFAULT_BROWSER_CDP_PORT = os.getenv("BROWSER_CHAT_CDP_PORT", "9222")
DEFAULT_BROWSER_APP_NAME = os.getenv("BROWSER_CHAT_BROWSER_APP", "Brave Browser").strip()
DEFAULT_BROWSER_WINDOW_MODE = os.getenv("BROWSER_CHAT_WINDOW_MODE", "background").strip().lower()
DEFAULT_LOCAL_BROWSER_USER_DATA_DIR = Path(
    os.getenv("BROWSER_CHAT_CHROME_USER_DATA_DIR", _default_user_data_dir := (
        Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser"
        if DEFAULT_BROWSER_APP_NAME == "Brave Browser"
        else Path.home() / "Library/Application Support/Google/Chrome"
    ))
).resolve()
DEFAULT_LOCAL_BROWSER_PROFILE_DIRECTORY = os.getenv("BROWSER_CHAT_CHROME_PROFILE_DIRECTORY", "Default").strip()


def setup():
    """Auto-install dependencies using UV"""
    if not shutil.which("uv"):
        print("❌ UV not found. Install from https://astral.sh/uv")
        sys.exit(1)
    
    print("📦 Installing dependencies with UV...")
    subprocess.run(["uv", "sync"], check=True)
    
    # Install playwright browsers if needed
    try:
        import playwright
        browser_path = Path.home() / ".cache" / "ms-playwright"
        if not browser_path.exists():
            print("🌐 Installing Playwright browsers...")
            subprocess.run(["uv", "run", "playwright", "install", "chromium"], check=True)
    except ImportError:
        pass


def _chrome_executable():
    chrome_path = os.getenv("BROWSER_CHAT_CHROME_PATH", "").strip()
    candidates = [
        chrome_path,
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("brave-browser"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).exists()), None)


def _chrome_launch_command(args: list[str]) -> list[str]:
    if sys.platform == "darwin":
        flags = ["-n"]
        if DEFAULT_BROWSER_WINDOW_MODE in {"background", "offscreen", "hidden"}:
            flags.extend(["-g", "-j"])
        return ["open", *flags, "-a", DEFAULT_BROWSER_APP_NAME, "--args", *args]
    executable = _chrome_executable()
    if not executable:
        return []
    if DEFAULT_BROWSER_WINDOW_MODE in {"background", "minimized"}:
        args = [*args, "--start-minimized"]
    if DEFAULT_BROWSER_WINDOW_MODE == "offscreen":
        args = [*args, "--window-position=-2400,0", "--window-size=1280,900"]
    return [executable, *args]


def launch_debug_browser(use_local_profile: bool = False):
    """Launch Chrome with remote debugging enabled."""
    if sys.platform != "darwin" and not _chrome_executable():
        print("❌ Chrome executable not found. Set BROWSER_CHAT_CHROME_PATH.")
        sys.exit(1)

    if use_local_profile:
        if not DEFAULT_LOCAL_BROWSER_USER_DATA_DIR.exists():
            print(f"❌ Browser user data dir not found: {DEFAULT_LOCAL_BROWSER_USER_DATA_DIR}")
            sys.exit(1)
        args = [
            f"--remote-debugging-port={DEFAULT_BROWSER_CDP_PORT}",
            f"--user-data-dir={DEFAULT_LOCAL_BROWSER_USER_DATA_DIR}",
            f"--profile-directory={DEFAULT_LOCAL_BROWSER_PROFILE_DIRECTORY}",
            DEFAULT_TARGET_URL,
        ]
        if DEFAULT_BROWSER_WINDOW_MODE == "offscreen":
            args.extend(["--window-position=-2400,0", "--window-size=1280,900"])
        elif DEFAULT_BROWSER_WINDOW_MODE in {"background", "minimized"}:
            args.append("--start-minimized")
        cmd = _chrome_launch_command(args)
        print(f"Launching {DEFAULT_BROWSER_APP_NAME} debug session with local profile...")
        print(f"user-data-dir={DEFAULT_LOCAL_BROWSER_USER_DATA_DIR}")
        print(f"profile-directory={DEFAULT_LOCAL_BROWSER_PROFILE_DIRECTORY}")
        print(f"window-mode={DEFAULT_BROWSER_WINDOW_MODE}")
        print(f"If {DEFAULT_BROWSER_APP_NAME} is already running on this profile, fully quit it first.")
    else:
        DEFAULT_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        args = [
            f"--remote-debugging-port={DEFAULT_BROWSER_CDP_PORT}",
            f"--user-data-dir={DEFAULT_BROWSER_PROFILE_DIR}",
            DEFAULT_TARGET_URL,
        ]
        if DEFAULT_BROWSER_WINDOW_MODE == "offscreen":
            args.extend(["--window-position=-2400,0", "--window-size=1280,900"])
        elif DEFAULT_BROWSER_WINDOW_MODE in {"background", "minimized"}:
            args.append("--start-minimized")
        cmd = _chrome_launch_command(args)
        print(f"Launching {DEFAULT_BROWSER_APP_NAME} debug session with dedicated profile...")
        print(f"user-data-dir={DEFAULT_BROWSER_PROFILE_DIR}")
        print(f"window-mode={DEFAULT_BROWSER_WINDOW_MODE}")

    print(" ".join(cmd))
    subprocess.Popen(cmd)


def main():
    import uvicorn
    print("🚀 Starting browser chat bridge...")
    uvicorn.run(
        "consensus_api:app",
        host="0.0.0.0",
        port=8002,
        reload=True,  # Auto-reload on code changes
        workers=1
    )

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup()
    elif len(sys.argv) > 1 and sys.argv[1] == "chrome":
        launch_debug_browser()
    elif len(sys.argv) > 1 and sys.argv[1] == "chrome-local":
        launch_debug_browser(use_local_profile=True)
    else:
        # Auto-setup on first run
        if not Path("uv.lock").exists():
            setup()
        main()
