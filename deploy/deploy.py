"""Promote Fabric items from this repository to a target workspace with fabric-cicd.

Publishing runs in two phases because fabric-cicd publishes VariableLibrary items
before Lakehouse and SQLDatabase items, and $items.* parameter references only
resolve to items that already exist in the target workspace.

    1. Infrastructure: Lakehouse, SQLDatabase (the SQL project creates the schema).
    2. Content: VariableLibrary, Notebook, DataPipeline (rebound to phase 1 items).

After publishing, the deployed definitions are read back and the run fails if any
item still references an ID outside the target workspace.

Usage:
    python deploy/deploy.py --environment UAT --check-only
    python deploy/deploy.py --environment UAT --workspace-id <target-workspace-id>
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import yaml

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ZERO_GUID = "00000000-0000-0000-0000-000000000000"

INFRASTRUCTURE_TYPES = ["Lakehouse", "SQLDatabase"]
CONTENT_TYPES = ["VariableLibrary", "Notebook", "DataPipeline"]
ITEM_TYPES = INFRASTRUCTURE_TYPES + CONTENT_TYPES

# Library variables holding tenant-level connection IDs, shared across environments.
CONNECTION_VARIABLES = {"SourceConnectionId", "DestinationConnectionId"}

REPO_ROOT = Path(__file__).resolve().parent.parent


class DeploymentError(Exception):
    pass


def log_error(message):
    if os.environ.get("TF_BUILD"):
        print(f"##vso[task.logissue type=error]{message}")
    else:
        print(f"ERROR: {message}", file=sys.stderr)


def repository_items(repository_directory):
    """Map (item type, display name) to the item folder for every in-scope item."""
    items = {}
    for platform_file in sorted(repository_directory.glob("*/.platform")):
        metadata = json.loads(platform_file.read_text(encoding="utf-8-sig"))["metadata"]
        if metadata["type"] in ITEM_TYPES:
            items[(metadata["type"], metadata["displayName"])] = platform_file.parent
    return items


def matches_filter(value, expected):
    if expected is None:
        return True
    return value in ([expected] if isinstance(expected, str) else expected)


def content_files(folder):
    return [path for path in sorted(folder.rglob("*")) if path.is_file() and path.name != ".platform"]


def check_repository(repository_directory, parameter_file, environment):
    """Validate parameterization statically, without credentials.

    Fails when a parameter entry no longer matches the repository (for example the
    Dev lakehouse was recreated with a new ID), or when an item contains a GUID that
    no parameter entry rewrites, which would leak a Dev reference into the target.
    """
    from jsonpath_ng.ext import parse as jsonpath_parse

    errors = []
    items = repository_items(repository_directory)
    print(f"Repository items in scope ({len(items)}):")
    for (item_type, name), folder in items.items():
        print(f"  {item_type:16} {name}  [{folder.name}]")
    for item_type in ITEM_TYPES:
        if not any(key[0] == item_type for key in items):
            errors.append(f"No {item_type} item found in {repository_directory}.")

    parameters = yaml.safe_load(parameter_file.read_text(encoding="utf-8")) or {}
    unsupported = set(parameters) - {"find_replace", "key_value_replace"}
    if unsupported:
        errors.append(f"Unsupported parameter sections for this check: {sorted(unsupported)}")

    covered = {}  # file path -> set of GUIDs rewritten by a parameter entry

    def targets(entry, label):
        if environment not in (entry.get("replace_value") or {}):
            errors.append(f"{label}: replace_value has no '{environment}' entry.")
        selected = [
            folder
            for (item_type, name), folder in items.items()
            if matches_filter(item_type, entry.get("item_type")) and matches_filter(name, entry.get("item_name"))
        ]
        if not selected:
            errors.append(f"{label}: item_type/item_name filters match no repository item.")
        return selected

    for index, entry in enumerate(parameters.get("find_replace") or []):
        label = f"find_replace[{index}] '{entry.get('find_value')}'"
        found = False
        for folder in targets(entry, label):
            for path in content_files(folder):
                if entry["find_value"] in path.read_text(encoding="utf-8-sig"):
                    covered.setdefault(path, set()).add(entry["find_value"].lower())
                    found = True
        if not found:
            errors.append(f"{label}: value not found in the targeted items; the Dev reference may have changed.")

    for index, entry in enumerate(parameters.get("key_value_replace") or []):
        label = f"key_value_replace[{index}] {entry.get('find_key')}"
        expression = jsonpath_parse(entry["find_key"])
        found = False
        for folder in targets(entry, label):
            for path in content_files(folder):
                if path.suffix.lower() != ".json":
                    continue
                for match in expression.find(json.loads(path.read_text(encoding="utf-8-sig"))):
                    found = True
                    covered.setdefault(path, set()).update(g.lower() for g in GUID.findall(str(match.value)))
        if not found:
            errors.append(f"{label}: JSONPath matches nothing in the targeted items.")

    for (item_type, _), folder in items.items():
        if item_type != "VariableLibrary":
            continue
        variables_file = folder / "variables.json"
        for variable in json.loads(variables_file.read_text(encoding="utf-8-sig"))["variables"]:
            if variable["name"] in CONNECTION_VARIABLES:
                covered.setdefault(variables_file, set()).add(str(variable["value"]).lower())

    for (item_type, name), folder in items.items():
        for path in content_files(folder):
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            for guid in sorted({g.lower() for g in GUID.findall(text)} - {ZERO_GUID} - covered.get(path, set())):
                errors.append(
                    f"{item_type} '{name}' file {path.relative_to(repository_directory)} contains "
                    f"unparameterized ID {guid}; add a parameter.yml entry for it."
                )

    if errors:
        for error in errors:
            log_error(error)
        raise DeploymentError(f"Static check failed with {len(errors)} error(s).")
    print(f"Static check passed for environment '{environment}'.")


def phase_parameters(parameter_file, item_types):
    """Keep only parameter entries that can apply to this phase's item types.

    fabric-cicd logs an error-level message for every entry whose item_type is out
    of scope; filtering keeps phase logs limited to genuine problems.
    """
    parameters = yaml.safe_load(parameter_file.read_text(encoding="utf-8")) or {}
    filtered = {}
    for section, entries in parameters.items():
        kept = [
            entry
            for entry in entries or []
            if entry.get("item_type") is None
            or any(matches_filter(item_type, entry["item_type"]) for item_type in item_types)
        ]
        if kept:
            filtered[section] = kept
    return filtered


def publish(phase, item_types, args, credential):
    import tempfile

    from fabric_cicd import FabricWorkspace, publish_all_items

    print(f"\n=== Phase {phase}: publishing {', '.join(item_types)} ===", flush=True)
    parameters = phase_parameters(args.parameter_file, item_types)
    with tempfile.TemporaryDirectory() as temp_dir:
        options = {"skip_parameterization": True}
        if parameters:
            phase_file = Path(temp_dir) / "parameter.yml"
            phase_file.write_text(yaml.safe_dump(parameters, sort_keys=False), encoding="utf-8")
            options = {"parameter_file_path": str(phase_file)}
        workspace = FabricWorkspace(
            workspace_id=args.workspace_id,
            environment=args.environment,
            repository_directory=str(args.repository_directory),
            item_type_in_scope=item_types,
            token_credential=credential,
            **options,
        )
        publish_all_items(workspace)


class FabricClient:
    def __init__(self, credential):
        import requests

        self.session = requests.Session()
        self.credential = credential
        self.expires_on = 0

    def request(self, method, url, **kwargs):
        if time.time() > self.expires_on - 300:
            token = self.credential.get_token(FABRIC_SCOPE)
            self.session.headers["Authorization"] = f"Bearer {token.token}"
            self.expires_on = token.expires_on
        response = self.session.request(method, url, timeout=120, **kwargs)
        if not response.ok:
            raise DeploymentError(f"{method} {url} returned {response.status_code}: {response.text[:500]}")
        return response

    def definition(self, workspace_id, item_id, definition_format=None):
        url = f"{FABRIC_API}/workspaces/{workspace_id}/items/{item_id}/getDefinition"
        if definition_format:
            url += f"?format={definition_format}"
        response = self.request("POST", url)
        if response.status_code == 202:
            location = response.headers["Location"]
            deadline = time.monotonic() + 600
            while True:
                time.sleep(int(response.headers.get("Retry-After", "5")))
                response = self.request("GET", location)
                status = response.json().get("status")
                if status == "Succeeded":
                    response = self.request("GET", f"{location.rstrip('/')}/result")
                    break
                if status in {"Failed", "Cancelled"} or time.monotonic() > deadline:
                    raise DeploymentError(f"getDefinition for {item_id} ended with status {status}.")
        return {
            part["path"]: base64.b64decode(part["payload"]).decode("utf-8-sig", errors="replace")
            for part in response.json()["definition"]["parts"]
            if part["path"] != ".platform"
        }


def verify(args, credential):
    """Read the deployed definitions back and confirm they reference only the target workspace."""
    print("\n=== Verifying target workspace ===", flush=True)
    client = FabricClient(credential)
    workspace_id = args.workspace_id.lower()
    deployed = client.request("GET", f"{FABRIC_API}/workspaces/{workspace_id}/items").json()["value"]
    by_key = {(item["type"], item["displayName"]): item["id"].lower() for item in deployed}
    allowed = {workspace_id, ZERO_GUID} | set(by_key.values())

    errors = []
    expected = repository_items(args.repository_directory)
    for item_type, name in expected:
        if (item_type, name) not in by_key:
            errors.append(f"{item_type} '{name}' was not found in the target workspace.")
    if errors:
        for error in errors:
            log_error(error)
        raise DeploymentError("Verification failed: items missing from the target workspace.")

    definitions = {}
    for item_type, name in expected:
        definition_format = "sqlproj" if item_type == "SQLDatabase" else None
        definitions[(item_type, name)] = client.definition(workspace_id, by_key[(item_type, name)], definition_format)

    for (item_type, name), parts in definitions.items():
        if item_type != "VariableLibrary":
            continue
        variables = {v["name"]: v["value"] for v in json.loads(parts["variables.json"])["variables"]}
        allowed |= {str(variables[v]).lower() for v in CONNECTION_VARIABLES if v in variables}
        library = client.request(
            "GET", f"{FABRIC_API}/workspaces/{workspace_id}/variableLibraries/{by_key[(item_type, name)]}"
        ).json()
        print(f"  VariableLibrary '{name}': active value set '{library['properties'].get('activeValueSetName')}'")
        for variable, value in sorted(variables.items()):
            print(f"    {variable} = {value}")

    for (item_type, name), parts in definitions.items():
        if item_type == "SQLDatabase":
            continue
        for path, text in parts.items():
            for guid in sorted({g.lower() for g in GUID.findall(text)} - allowed):
                errors.append(f"{item_type} '{name}' ({path}) references {guid}, which is outside the target workspace.")

    for (item_type, name), parts in definitions.items():
        if item_type == "Notebook":
            for part in parts.values():
                lakehouse = re.search(r'"default_lakehouse"\s*:\s*"([^"]+)"', part)
                lakehouse_ws = re.search(r'"default_lakehouse_workspace_id"\s*:\s*"([^"]+)"', part)
                if lakehouse:
                    target = next((k[1] for k, v in by_key.items() if k[0] == "Lakehouse" and v == lakehouse[1].lower()), None)
                    print(f"  Notebook '{name}': default lakehouse '{target}' ({lakehouse[1]})")
                    if target is None or (lakehouse_ws and lakehouse_ws[1].lower() != workspace_id):
                        errors.append(f"Notebook '{name}' is not bound to a lakehouse in the target workspace.")
        elif item_type == "DataPipeline":
            content = json.loads(parts["pipeline-content.json"])
            libraries = {key[1]: definitions[key] for key in definitions if key[0] == "VariableLibrary"}
            for alias, binding in (content["properties"].get("libraryVariables") or {}).items():
                library_parts = libraries.get(binding["libraryName"])
                names = {v["name"] for v in json.loads(library_parts["variables.json"])["variables"]} if library_parts else set()
                if binding["variableName"] not in names:
                    errors.append(f"DataPipeline '{name}' binds {alias} to missing {binding['libraryName']}.{binding['variableName']}.")
            print(f"  DataPipeline '{name}': {len(content['properties'].get('libraryVariables') or {})} library variable bindings resolved")
        elif item_type == "SQLDatabase":
            source_folder = expected[(item_type, name)]
            source_objects = {
                p.relative_to(source_folder).as_posix().lower()
                for p in source_folder.rglob("*.sql")
                if ".sharedqueries" not in p.parts
            }
            deployed_objects = {path.lower() for path in parts if path.lower().endswith(".sql")}
            missing = sorted(source_objects - deployed_objects)
            print(f"  SQLDatabase '{name}': {len(source_objects) - len(missing)}/{len(source_objects)} schema objects present")
            for path in missing:
                errors.append(f"SQLDatabase '{name}' is missing schema object {path}.")

    if errors:
        for error in errors:
            log_error(error)
        raise DeploymentError(f"Verification failed with {len(errors)} error(s).")
    print("Verification passed: all items deployed and bound to the target workspace.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--environment", required=True, help="Environment key in parameter.yml, e.g. UAT.")
    parser.add_argument("--workspace-id", help="Target Fabric workspace ID (required unless --check-only).")
    parser.add_argument("--repository-directory", type=Path, default=REPO_ROOT)
    parser.add_argument("--parameter-file", type=Path, default=Path(__file__).resolve().parent / "parameter.yml")
    parser.add_argument("--check-only", action="store_true", help="Run the static check only; no credentials needed.")
    args = parser.parse_args()
    args.repository_directory = args.repository_directory.resolve()
    args.parameter_file = args.parameter_file.resolve()

    try:
        check_repository(args.repository_directory, args.parameter_file, args.environment)
        if args.check_only:
            return 0
        if not args.workspace_id or not GUID.fullmatch(args.workspace_id):
            raise DeploymentError("--workspace-id must be the target workspace GUID.")

        from azure.identity import AzureCliCredential

        credential = AzureCliCredential()
        publish("1/2", INFRASTRUCTURE_TYPES, args, credential)
        publish("2/2", CONTENT_TYPES, args, credential)
        verify(args, credential)
    except DeploymentError as error:
        log_error(str(error))
        return 1
    print(f"\nDeployment to workspace {args.workspace_id} ({args.environment}) completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
