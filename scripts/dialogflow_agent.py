"""Export / restore the Dialogflow ES agent through the API.

The agent definition (intents, training phrases, entities, contexts) lives in
Google Cloud, not in this repo. `export` pulls it into dialogflow/agent/ as
plain JSON files so it is versioned and reviewable; `restore` pushes such a
directory back, which REPLACES the whole agent in the target project.

Usage:
    uv run python scripts/dialogflow_agent.py export  --project your-gcp-project-id
    uv run python scripts/dialogflow_agent.py restore --project <target> --confirm <target>

Auth: Application Default Credentials (`gcloud auth application-default login`
or GOOGLE_APPLICATION_CREDENTIALS). Add --gcloud to use the token of the
account signed in to the gcloud CLI instead. Export needs
dialogflow.agents.export and restore dialogflow.agents.restore (e.g. the
"Dialogflow API Admin" role); the server's own key (Dialogflow API Client)
is deliberately NOT enough.

The webhook (fulfillment) section is kept, but the webhook URL, username,
password and custom headers are blanked before writing, so no host names or
credentials end up in git. Fulfilment is off in this agent; to use a webhook
after a restore, re-enter them in the console.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.cloud import dialogflow_v2 as dialogflow

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO_ROOT / "dialogflow" / "agent"
# The URL is blanked too: it names a host (often a temporary tunnel) that
# has no place in a public repo.
SECRET_WEBHOOK_KEYS = ("url", "username", "password", "headers")


def _credentials(use_gcloud: bool):
    if not use_gcloud:
        return None  # client library falls back to ADC
    from google.oauth2.credentials import Credentials

    gcloud = shutil.which("gcloud") or "gcloud"
    token = (
        subprocess.run(
            [gcloud, "auth", "print-access-token"],
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    return Credentials(token)


def _client(project: str, use_gcloud: bool) -> dialogflow.AgentsClient:
    return dialogflow.AgentsClient(
        credentials=_credentials(use_gcloud),
        client_options=ClientOptions(quota_project_id=project),
    )


def _scrub_webhook(agent_json: dict) -> list[str]:
    """Blank webhook credentials and legacy API keys in agent.json; return the keys blanked."""
    blanked = []
    # Legacy Dialogflow V1 client access key (V1 API shut down 2020). Never commit it.
    if agent_json.get("secondaryKey"):
        agent_json["secondaryKey"] = ""
        blanked.append("secondaryKey")
    webhook = agent_json.get("webhook")
    if isinstance(webhook, dict):
        for key in SECRET_WEBHOOK_KEYS:
            if webhook.get(key):
                webhook[key] = {} if isinstance(webhook[key], dict) else ""
                blanked.append(f"webhook.{key}")
    return blanked


def export(project: str, out_dir: Path, use_gcloud: bool) -> None:
    client = _client(project, use_gcloud)
    operation = client.export_agent(request={"parent": f"projects/{project}"})
    content = operation.result(timeout=300).agent_content

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for member in archive.infolist():
            target = (out_dir / member.filename).resolve()
            if out_dir.resolve() not in target.parents and target != out_dir.resolve():
                raise SystemExit(f"Refusing unsafe path in export: {member.filename}")
            if member.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(member)
            if member.filename.endswith(".json"):
                parsed = json.loads(data.decode("utf-8"))
                if member.filename == "agent.json":
                    for key in _scrub_webhook(parsed):
                        print(f"  blanked {key} (re-enter it in the console after a restore)")
                data = (json.dumps(parsed, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            target.write_bytes(data)

    files = sorted(p for p in out_dir.rglob("*") if p.is_file())
    print(f"Exported projects/{project}/agent -> {out_dir} ({len(files)} files)")


def restore(project: str, src_dir: Path, use_gcloud: bool) -> None:
    if not (src_dir / "agent.json").is_file():
        raise SystemExit(f"{src_dir} does not look like an agent export (no agent.json).")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(src_dir).as_posix())
    client = _client(project, use_gcloud)
    operation = client.restore_agent(
        request={"parent": f"projects/{project}", "agent_content": buffer.getvalue()}
    )
    operation.result(timeout=300)
    print(f"Restored {src_dir} -> projects/{project}/agent (previous agent replaced)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "restore"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True, help="GCP project that hosts the agent")
        p.add_argument("--dir", type=Path, default=DEFAULT_DIR, help=f"default: {DEFAULT_DIR}")
        p.add_argument("--gcloud", action="store_true", help="use the gcloud CLI account's token")
    sub.choices["restore"].add_argument(
        "--confirm",
        required=True,
        help="repeat the project ID: restore REPLACES the agent in that project",
    )
    args = parser.parse_args(argv)

    if args.command == "export":
        export(args.project, args.dir, args.gcloud)
    else:
        if args.confirm != args.project:
            raise SystemExit("--confirm must equal --project; nothing was changed.")
        restore(args.project, args.dir, args.gcloud)


if __name__ == "__main__":
    main()
