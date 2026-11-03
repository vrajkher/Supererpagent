"""Run the connector in the background on Windows, using a Task Scheduler task.

Task Scheduler is used instead of a Windows service so the installer needs no third-party service
wrapper. The task is registered as follows:

* runs as **SYSTEM** at **system start**, with no time limit, and only one instance at a time;
* restarts every minute after a failure (up to 999 times), and keeps running on battery;
* starts ``raj-connector.exe --data <dir> run --log <dir>\\connector.log``.

Tally runs in the logged-in user's session and listens on localhost:9000, which SYSTEM can reach.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "RAJ Connector"


def connector_command() -> tuple[str, list[str]]:
    """The executable (and leading args) that run this connector, frozen or not."""
    if getattr(sys, "frozen", False):
        return sys.executable, []
    return sys.executable, ["-m", "raj.connector"]


def build_task_xml(exe: str, args: list[str], workdir: str) -> str:
    arguments = " ".join(f'"{a}"' if " " in a or not a else a for a in args)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>RAJ Finance Agent connector: local gateway for Tally and the SSH tunnel to the firm server.</Description>
    <URI>\\{escape(TASK_NAME)}</URI>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled><Delay>PT30S</Delay></BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    if os.name != "nt":
        raise SystemExit("The background task is only available on Windows.")
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def install(data: Path, start: bool = True) -> None:
    data = Path(data).resolve()
    if start and not (data / "config.json").exists():
        raise SystemExit(f"This PC is not paired yet ({data / 'config.json'} is missing). Run pairing first.")
    exe, lead = connector_command()
    xml = build_task_xml(exe, [*lead, "--data", str(data), "run", "--log", str(data / "connector.log")], str(data))
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
        f.write(xml)
        path = f.name
    try:
        r = _schtasks("/Create", "/TN", TASK_NAME, "/XML", path, "/F")
    finally:
        os.unlink(path)
    if r.returncode != 0:
        raise SystemExit(f"Could not register the background task: {r.stderr.strip() or r.stdout.strip()}")
    print(f"Background task '{TASK_NAME}' registered (starts at boot as SYSTEM, restarts on failure).")
    if start:
        _schtasks("/Run", "/TN", TASK_NAME)
        print("Started.")


def uninstall() -> None:
    _schtasks("/End", "/TN", TASK_NAME)
    r = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    print("Background task removed." if r.returncode == 0 else "No background task was registered.")


def status() -> int:
    r = _schtasks("/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST")
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode


def start() -> None:
    r = _schtasks("/Run", "/TN", TASK_NAME)
    print(r.stdout.strip() or r.stderr.strip())


def stop() -> None:
    r = _schtasks("/End", "/TN", TASK_NAME)
    print(r.stdout.strip() or r.stderr.strip())
