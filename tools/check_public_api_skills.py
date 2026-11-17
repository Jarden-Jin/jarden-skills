from __future__ import annotations

import ast
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOTS = (ROOT / "skills" / "jarden-image", ROOT / "skills" / "jarden-video")
FORBIDDEN_TEXT = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bhia_[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |PGP )?PRIVATE KEY(?: BLOCK)?-----"),
    re.compile(r"(?i)\b(?:password|secret|token|api[_-]?key)\s*[:=]\s*['\"][A-Za-z0-9_./+=-]{24,}['\"]"),
    re.compile(r"(?i)\bAuthorization:\s*Bearer\s+[A-Za-z0-9_./+=-]{24,}"),
    re.compile(r"/Users/|/home/|[A-Za-z]:\\Users\\|jarden-ai-skills"),
)
FORBIDDEN_NAMES = {
    ".env", ".env.local", "personal.key", "device.key", "credentials.json",
    "secrets.json", "auth.json", "metadata.json",
}
ALLOWED_FILES = {
    "SKILL.md", "README.md", "agents/openai.yaml", "scripts/client.py",
}


def validate(root: Path = ROOT) -> list[str]:
    errors = []
    for skill_name in ("jarden-image", "jarden-video"):
        skill = root / "skills" / skill_name
        for relative in ALLOWED_FILES:
            filename = skill / relative
            if not filename.is_file():
                errors.append("missing " + str(filename.relative_to(root)))
        for filename in skill.rglob("*"):
            if not filename.is_file() and not filename.is_symlink():
                continue
            relative = filename.relative_to(skill).as_posix()
            if "__pycache__" in filename.parts or filename.suffix == ".pyc":
                continue
            if filename.is_symlink() or relative not in ALLOWED_FILES:
                errors.append("unexpected file " + str(filename.relative_to(root)))
                continue
            if filename.name in FORBIDDEN_NAMES:
                errors.append("credential-like file " + str(filename.relative_to(root)))
            try:
                text = filename.read_text(encoding="utf-8-sig")
            except (UnicodeError, OSError):
                errors.append("unreadable public file " + str(filename.relative_to(root)))
                continue
            if filename.suffix == ".py":
                try:
                    ast.parse(text, filename=str(filename))
                except SyntaxError:
                    errors.append("invalid Python " + str(filename.relative_to(root)))
            for index, pattern in enumerate(FORBIDDEN_TEXT, start=1):
                if pattern.search(text):
                    errors.append("secret/private-path rule " + str(index) + " in " + str(filename.relative_to(root)))
    return errors


def main() -> None:
    errors = validate()
    if errors:
        for error in errors:
            print("FAIL: " + error, file=sys.stderr)
        raise SystemExit(1)
    print("PUBLIC_API_SKILLS_OK")


if __name__ == "__main__":
    main()
