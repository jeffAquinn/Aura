"""Inquiry, backed by data: where a package comes from and what is known about it.

Every answer comes from PyPI's JSON API, the OSV vulnerability database, or a fixed rule. Anything
unknown can only make the verdict worse, never better.
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from aura.governor import Finding

# Typosquat targets: widely used packages whose near-misspellings are a known attack vector.
POPULAR = """
requests urllib3 numpy pandas scipy matplotlib pillow pyyaml setuptools wheel pip six certifi
idna charset-normalizer python-dateutil pytz attrs packaging jinja2 markupsafe click colorama
cryptography pycparser cffi boto3 botocore s3transfer jmespath awscli protobuf grpcio pydantic
fastapi starlette uvicorn httpx httpcore anyio sniffio h11 typing-extensions tqdm rich lxml
beautifulsoup4 soupsieve openpyxl xlrd pypdf pdfminer-six reportlab sqlalchemy psycopg2 pymysql
redis celery flask django werkzeug itsdangerous gunicorn aiohttp multidict yarl frozenlist
scikit-learn torch tensorflow keras transformers tokenizers huggingface-hub safetensors regex
nltk spacy networkx sympy statsmodels seaborn plotly bokeh pytest coverage tomli toml docutils
markdown pygments selenium playwright paramiko pexpect psutil watchdog pyjwt oauthlib websockets
""".split()


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class PackageReport:
    name: str
    version: str
    findings: list[Finding] = field(default_factory=list)
    age_days: int | None = None
    releases: int | None = None
    has_wheel: bool | None = None
    vulns: list[str] = field(default_factory=list)

    def summary(self) -> str:
        facts = []
        if self.age_days is not None:
            facts.append(f"{self.age_days // 365} years on PyPI" if self.age_days >= 365 else f"{self.age_days} days on PyPI")
        if self.releases is not None:
            facts.append(f"{self.releases} releases")
        if self.has_wheel is not None:
            facts.append("ready-built package" if self.has_wheel else "no ready-built package")
        problems = [f.message for f in self.findings if f.severity in ("yellow", "red")]
        return f"{self.name}=={self.version}: " + ", ".join(facts) + (" | " + "; ".join(problems) if problems else "")


def typosquat_of(name: str) -> str | None:
    n = normalize(name)
    if n in POPULAR:
        return None
    close = difflib.get_close_matches(n, POPULAR, n=1, cutoff=0.85)
    return close[0] if close else None


def check_package(name: str, version: str | None = None, client: httpx.Client | None = None) -> PackageReport:
    """Every rule in the Governor's provenance table, for one package version."""
    http = client or httpx.Client(timeout=15)
    report = PackageReport(name=normalize(name), version=version or "")
    add = report.findings.append
    if lookalike := typosquat_of(name):
        add(Finding("provenance", "red", f"'{name}' is one edit away from the popular '{lookalike}' (possible typosquat)"))

    r = http.get(f"https://pypi.org/pypi/{normalize(name)}/json")
    if r.status_code == 404:
        add(Finding("provenance", "red", f"'{name}' does not exist on PyPI"))
        return report
    r.raise_for_status()
    data = r.json()
    report.version = version = version or data["info"]["version"]
    releases = {v: files for v, files in data["releases"].items() if files}
    if version not in releases:
        add(Finding("provenance", "red", f"version {version} of '{name}' is not on PyPI"))
        return report

    uploads = [f["upload_time_iso_8601"] for files in releases.values() for f in files]
    first = min(datetime.fromisoformat(u.replace("Z", "+00:00")) for u in uploads)
    report.age_days = (datetime.now(timezone.utc) - first).days
    report.releases = len(releases)
    report.has_wheel = any(f["packagetype"] == "bdist_wheel" for f in releases[version])
    if not report.has_wheel:
        add(Finding("provenance", "red", f"{name} {version} has no ready-built package: installing it would run its own setup code"))
    if report.age_days < 90 or report.releases < 3:
        add(Finding("provenance", "yellow", f"young project: first release {report.age_days} days ago, {report.releases} releases"))
    if any(f.get("yanked") for f in releases[version]):
        add(Finding("provenance", "red", f"{name} {version} was withdrawn (yanked) by its maintainers"))

    osv = http.post("https://api.osv.dev/v1/query",
                    json={"package": {"name": normalize(name), "ecosystem": "PyPI"}, "version": version})  # fmt: skip
    osv.raise_for_status()
    report.vulns = [v["id"] for v in osv.json().get("vulns", [])]
    if report.vulns:
        add(Finding("provenance", "red", f"{len(report.vulns)} known vulnerabilities in {name} {version}",
                    ", ".join(report.vulns[:5])))  # fmt: skip
    if not [f for f in report.findings if f.severity in ("yellow", "red")]:
        add(Finding("provenance", "pass", report.summary()))
    return report


def resolve(requirements: list[str], uv: str | None = None) -> list[tuple[str, str]]:
    """Every package the requirements pull in (indirect ones too), for the sandbox's Linux image.
    Uses ready-built package metadata only (--no-build), so no package code runs."""
    if not requirements:
        return []
    uv = uv or shutil.which("uv") or str(Path("~/.local/bin/uv").expanduser())
    out = subprocess.run(
        [uv, "pip", "compile", "-", "--no-build", "--python-version", "3.12",
         "--python-platform", "aarch64-manylinux_2_28", "--no-header", "--no-annotate", "-q"],
        input="\n".join(requirements), capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    if out.returncode != 0:
        raise ValueError(out.stderr.strip()[-400:] or "dependencies could not be resolved")
    pins = []
    for line in out.stdout.splitlines():
        if "==" in line and not line.startswith("#"):
            name, version = line.split(";")[0].strip().split("==")
            pins.append((normalize(name), version))
    return pins
