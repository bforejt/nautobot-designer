"""Lint findings: the capture stage's honesty mechanism.

Capture refuses to emit a spec without a lint report; a template is blessed
by a human who knows exactly what it omits (plan §4). Findings are data so
the renderer can replay them into the generated package README.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

SEVERITIES = ("error", "warning", "info")


@dataclass
class Finding:
    severity: str  # error | warning | info
    category: str  # short slug, e.g. "cross-site-cable"
    message: str
    obj: str = ""  # object identifier, when applicable

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")

    def to_dict(self) -> dict:
        return asdict(self)


def to_dicts(findings: Iterable[Finding]) -> list[dict]:
    return [f.to_dict() for f in findings]


def from_dicts(raw: Iterable[dict]) -> list[Finding]:
    return [Finding(**entry) for entry in raw]


def has_errors(findings: Iterable[Finding]) -> bool:
    return any(f.severity == "error" for f in findings)


def render_markdown(findings: Iterable[Finding], title: str = "Capture lint report") -> str:
    findings = list(findings)
    lines = [f"# {title}", ""]
    if not findings:
        lines.append("No findings — capture is clean for the selected scope.")
        return "\n".join(lines) + "\n"

    for severity in SEVERITIES:
        group = [f for f in findings if f.severity == severity]
        if not group:
            continue
        lines.append(f"## {severity.upper()} ({len(group)})")
        lines.append("")
        for finding in group:
            obj = f" `{finding.obj}`" if finding.obj else ""
            lines.append(f"- **{finding.category}**{obj}: {finding.message}")
        lines.append("")
    return "\n".join(lines)
