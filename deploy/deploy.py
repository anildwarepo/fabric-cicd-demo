"""Deploy Fabric items from this repository to a target workspace with fabric-cicd.

Environment-specific values live in the variable library's value sets, one per
environment and named exactly like the environment (for example "uat" and "prod").
Items are published unchanged; no IDs are replaced during deployment. fabric-cicd
activates the value set whose name equals --environment, and items read their
workspace, lakehouse, and database IDs from the library at run time.

Steps:
    1. Static check (no credentials): each library has a value set named like the
       environment that overrides every environment-specific ID with a distinct value,
       and no other item contains a hard-coded ID.
    2. Publish the in-scope items with fabric-cicd.
    3. Deploy SQL database schemas from their SQL projects, because fabric-cicd
       publishes SQLDatabase items without their definition.
    4. Verify the target workspace: the active value set is the environment, every
       environment-specific ID belongs to the target workspace, and all item
       references resolve. Any mismatch fails the deployment.

Usage:
    python deploy/deploy.py --environment uat --check-only
    python deploy/deploy.py --environment uat --workspace-id <target-workspace-id>
    python deploy/deploy.py --environment uat --workspace-id <id> --items-in-scope Lakehouse,SQLDatabase
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
LIBRARY_REFERENCE = re.compile(r"\$\(/\*\*/([^/)]+)/([^/)]+)\)")
DEFAULT_VALUE_SET = "Default value set"

# Order matters only for display; fabric-cicd applies its own dependency order.
ITEM_TYPES = ["Lakehouse", "SQLDatabase", "VariableLibrary", "Notebook", "DataPipeline"]

# Library variables holding tenant-level connection IDs. They are not tied to a
# workspace, so value sets may keep the default value.
CONNECTION_VARIABLES = {"SourceConnectionId", "DestinationConnectionId"}

REPO_ROOT = Path(__file__).resolve().parent.parent


class DeploymentError(Exception):
    pass


def log_error(message):
    if os.environ.get("TF_BUILD"):
        print(f"##vso[task.logissue type=error]{message}")
    else:
        print(f"ERROR: {message}", file=sys.stderr)


def parse_items_in_scope(value):
    """Parse a comma-separated item type list into ITEM_TYPES order."""
    requested = [part.strip() for part in value.split(",") if part.strip()]
    unknown = sorted(set(requested) - set(ITEM_TYPES))
    if unknown:
        raise DeploymentError(
            f"Unsupported item type(s) in items_in_scope: {', '.join(unknown)}. Supported: {', '.join(ITEM_TYPES)}."
        )
    if not requested:
        raise DeploymentError("items_in_scope must contain at least one item type.")
    return [item_type for item_type in ITEM_TYPES if item_type in requested]


def repository_items(repository_directory, item_types=ITEM_TYPES):
    """Map (item type, display name) to the item folder for every item of the given types."""
    items = {}
    for platform_file in sorted(repository_directory.glob("*/.platform")):
        metadata = json.loads(platform_file.read_text(encoding="utf-8-sig"))["metadata"]
        if metadata["type"] in item_types:
            items[(metadata["type"], metadata["displayName"])] = platform_file.parent
    return items


def content_files(folder):
    return [path for path in sorted(folder.rglob("*")) if path.is_file() and path.name != ".platform"]


def is_guid(value):
    return isinstance(value, str) and bool(GUID.fullmatch(value))


class Library:
    """A variable library definition: default values plus named value sets."""

    def __init__(self, name, parts):
        self.name = name
        self.defaults = {v["name"]: v["value"] for v in json.loads(parts["variables.json"])["variables"]}
        settings = json.loads(parts.get("settings.json") or "{}")
        self.value_set_order = settings.get("valueSetsOrder") or []
        self.value_sets = {}
        for path, text in parts.items():
            if path.startswith("valueSets/") and path.endswith(".json"):
                value_set = json.loads(text)
                self.value_sets[value_set["name"]] = {o["name"]: o["value"] for o in value_set.get("variableOverrides") or []}

    @classmethod
    def from_folder(cls, name, folder):
        return cls(name, {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8-sig") for p in content_files(folder)})

    def environment_variables(self):
        """Variables whose default is a workspace-specific ID and must differ per environment."""
        return sorted(n for n, v in self.defaults.items() if is_guid(v) and v != ZERO_GUID and n not in CONNECTION_VARIABLES)

    def resolve(self, value_set):
        if value_set == DEFAULT_VALUE_SET:
            return dict(self.defaults)
        return {**self.defaults, **self.value_sets.get(value_set, {})}


def check_repository(repository_directory, environment, item_types):
    """Validate the repository without credentials before anything is published."""
    errors = []
    items = repository_items(repository_directory, item_types)
    print(f"Environment: {environment}")
    print(f"Item types in scope: {', '.join(item_types)}")
    print(f"Repository items in scope ({len(items)}):")
    for (item_type, name), folder in items.items():
        print(f"  {item_type:16} {name}  [{folder.name}]")
    for item_type in item_types:
        if not any(key[0] == item_type for key in items):
            errors.append(f"No {item_type} item found in {repository_directory}.")

    libraries = {name: Library.from_folder(name, folder) for (t, name), folder in repository_items(repository_directory, ["VariableLibrary"]).items()}

    for name, library in libraries.items():
        if ("VariableLibrary", name) not in items:
            continue
        if environment not in library.value_set_order or environment not in library.value_sets:
            near = [v for v in library.value_sets if v.lower() == environment.lower()]
            hint = f" Found '{near[0]}', but names are case-sensitive." if near else ""
            errors.append(
                f"VariableLibrary '{name}' has no value set named '{environment}' (value sets: {sorted(library.value_sets)}). "
                f"fabric-cicd would activate '{DEFAULT_VALUE_SET}' instead.{hint}"
            )
            continue
        unknown = sorted(set(library.value_sets[environment]) - set(library.defaults))
        if unknown:
            errors.append(f"VariableLibrary '{name}' value set '{environment}' overrides unknown variable(s): {unknown}.")
        sets = [DEFAULT_VALUE_SET, *library.value_sets]
        print(f"VariableLibrary '{name}': environment-specific variables per value set")
        for variable in library.environment_variables():
            values = {value_set: library.resolve(value_set)[variable] for value_set in sets}
            print(f"  {variable:24} " + "  ".join(f"{s}={v}" for s, v in values.items()))
            if variable not in library.value_sets[environment]:
                errors.append(
                    f"VariableLibrary '{name}' value set '{environment}' does not override {variable}; "
                    f"it would use the Default (Dev) value {library.defaults[variable]}."
                )
            elif not is_guid(library.value_sets[environment][variable]):
                errors.append(f"VariableLibrary '{name}' value set '{environment}' sets {variable} to a non-ID value.")
            lowered = [str(v).lower() for v in values.values()]
            if len(set(lowered)) != len(lowered):
                duplicates = [s for s, v in values.items() if lowered.count(str(v).lower()) > 1]
                errors.append(f"VariableLibrary '{name}' {variable} has the same value in value sets {duplicates}; each environment needs its own ID.")

    for (item_type, name), folder in items.items():
        if item_type == "VariableLibrary":
            continue
        for path in content_files(folder):
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            for guid in sorted({g.lower() for g in GUID.findall(text)} - {ZERO_GUID}):
                errors.append(
                    f"{item_type} '{name}' file {path.relative_to(repository_directory)} contains hard-coded ID {guid}; "
                    "read it from the variable library instead."
                )
            for library_name, variable in LIBRARY_REFERENCE.findall(text):
                if library_name in libraries and variable not in libraries[library_name].defaults:
                    errors.append(f"{item_type} '{name}' references missing variable {library_name}/{variable}.")
            if item_type == "DataPipeline" and path.name == "pipeline-content.json":
                for alias, binding in (json.loads(text)["properties"].get("libraryVariables") or {}).items():
                    library = libraries.get(binding["libraryName"])
                    if library and binding["variableName"] not in library.defaults:
                        errors.append(f"DataPipeline '{name}' binds {alias} to missing {binding['libraryName']}.{binding['variableName']}.")

    if errors:
        for error in errors:
            log_error(error)
        raise DeploymentError(f"Static check failed with {len(errors)} error(s).")
    print(f"Static check passed for environment '{environment}'.")


def publish(args, credential):
    from fabric_cicd import FabricWorkspace, publish_all_items

    print(f"\n=== Publishing {', '.join(args.item_types)} ===", flush=True)
    workspace = FabricWorkspace(
        workspace_id=args.workspace_id,
        environment=args.environment,
        repository_directory=str(args.repository_directory),
        item_type_in_scope=args.item_types,
        token_credential=credential,
        skip_parameterization=True,
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
            self.session.headers["Authorization"] = "Bearer " + token.token
            self.expires_on = token.expires_on
        response = self.session.request(method, url, timeout=120, **kwargs)
        if not response.ok:
            raise DeploymentError(f"{method} {url} returned {response.status_code}: {response.text[:500]}")
        return response

    def wait(self, response, description):
        """Wait for a Fabric long-running operation and return its final response."""
        if response.status_code != 202:
            return response
        location = response.headers["Location"]
        deadline = time.monotonic() + 1200
        while True:
            time.sleep(int(response.headers.get("Retry-After", "5")))
            response = self.request("GET", location)
            body = response.json()
            status = body.get("status")
            if status == "Succeeded":
                return response
            if status in {"Failed", "Cancelled"}:
                raise DeploymentError(f"{description} ended with status {status}: {json.dumps(body.get('error', body))[:1500]}")
            if time.monotonic() > deadline:
                raise DeploymentError(f"{description} did not finish within 20 minutes.")

    def definition(self, workspace_id, item_id, definition_format=None):
        url = f"{FABRIC_API}/workspaces/{workspace_id}/items/{item_id}/getDefinition"
        if definition_format:
            url += f"?format={definition_format}"
        response = self.request("POST", url)
        if response.status_code == 202:
            location = self.wait(response, f"getDefinition for {item_id}").url
            response = self.request("GET", f"{location.rstrip('/')}/result")
        return {
            part["path"]: base64.b64decode(part["payload"]).decode("utf-8-sig", errors="replace")
            for part in response.json()["definition"]["parts"]
            if part["path"] != ".platform"
        }


def deploy_sql_schema(args, credential):
    """Deploy each SQL database's schema from its SQL project with the Fabric API.

    Fabric builds the project and applies a differential update; existing data is kept.
    """
    print("\n=== Deploying SQL database schemas (sqlproj) ===", flush=True)
    client = FabricClient(credential)
    workspace_id = args.workspace_id.lower()
    deployed = client.request("GET", f"{FABRIC_API}/workspaces/{workspace_id}/items").json()["value"]
    by_key = {(item["type"], item["displayName"]): item["id"] for item in deployed}
    for (item_type, name), folder in repository_items(args.repository_directory, ["SQLDatabase"]).items():
        if (item_type, name) not in by_key:
            raise DeploymentError(f"SQLDatabase '{name}' does not exist in the target workspace after publishing.")
        parts = [
            {
                "path": path.relative_to(folder).as_posix(),
                "payload": base64.b64encode(path.read_bytes()).decode(),
                "payloadType": "InlineBase64",
            }
            for path in content_files(folder)
            if path.relative_to(folder).as_posix() != ".gitignore"
        ]
        projects = [part["path"] for part in parts if part["path"].endswith(".sqlproj")]
        if len(projects) != 1:
            raise DeploymentError(f"SQLDatabase '{name}' must contain exactly one .sqlproj file; found {projects}.")
        print(f"  Deploying '{name}' schema from {len(parts)} file(s): {', '.join(p['path'] for p in parts)}", flush=True)
        response = client.request(
            "POST",
            f"{FABRIC_API}/workspaces/{workspace_id}/items/{by_key[(item_type, name)]}/updateDefinition",
            json={"definition": {"format": "sqlproj", "parts": parts}},
        )
        client.wait(response, f"Schema deployment for SQLDatabase '{name}'")
        print(f"  Deployed schema for SQLDatabase '{name}'", flush=True)


def expected_target(variable, workspace_id, by_key):
    """Return (description, allowed IDs) for an environment-specific library variable."""
    ids_of = lambda item_type: {v for (t, _), v in by_key.items() if t == item_type}
    if variable.endswith("WorkspaceId"):
        return "the target workspace", {workspace_id}
    if variable.endswith("LakehouseId"):
        return "a Lakehouse in the target workspace", ids_of("Lakehouse")
    if variable.endswith("DatabaseId"):
        return "a SQLDatabase in the target workspace", ids_of("SQLDatabase")
    return "an item in the target workspace", set(by_key.values()) | {workspace_id}


def verify(args, credential):
    """Read the target workspace back and fail if any value or reference points elsewhere."""
    print("\n=== Verifying target workspace ===", flush=True)
    client = FabricClient(credential)
    workspace_id = args.workspace_id.lower()
    deployed = client.request("GET", f"{FABRIC_API}/workspaces/{workspace_id}/items").json()["value"]
    by_key = {(item["type"], item["displayName"]): item["id"].lower() for item in deployed}
    names_by_id = {v: k[1] for k, v in by_key.items()}
    errors = []

    expected = repository_items(args.repository_directory, args.item_types)
    missing = [f"{t} '{n}'" for t, n in expected if (t, n) not in by_key]
    if missing:
        for item in missing:
            log_error(f"{item} was not found in the target workspace.")
        raise DeploymentError("Verification failed: items missing from the target workspace.")

    definitions = {}
    for item_type, name in expected:
        definition_format = "sqlproj" if item_type == "SQLDatabase" else None
        definitions[(item_type, name)] = client.definition(workspace_id, by_key[(item_type, name)], definition_format)

    # Libraries referenced by in-scope items are checked even when the library itself is out of scope.
    referenced_libraries = set()
    for (item_type, name), parts in definitions.items():
        for text in parts.values():
            referenced_libraries.update(library for library, _ in LIBRARY_REFERENCE.findall(text))
        if item_type == "DataPipeline":
            bindings = json.loads(parts["pipeline-content.json"])["properties"].get("libraryVariables") or {}
            referenced_libraries.update(b["libraryName"] for b in bindings.values())
    library_names = {n for t, n in expected if t == "VariableLibrary"} | referenced_libraries

    libraries, active_sets = {}, {}
    for name in sorted(library_names):
        if ("VariableLibrary", name) not in by_key:
            errors.append(f"VariableLibrary '{name}' is referenced but does not exist in the target workspace.")
            continue
        parts = definitions.get(("VariableLibrary", name)) or client.definition(workspace_id, by_key[("VariableLibrary", name)])
        library = libraries[name] = Library(name, parts)
        active = active_sets[name] = client.request(
            "GET", f"{FABRIC_API}/workspaces/{workspace_id}/variableLibraries/{by_key[('VariableLibrary', name)]}"
        ).json()["properties"].get("activeValueSetName")
        print(f"  VariableLibrary '{name}': active value set '{active}'")
        if active != args.environment:
            errors.append(f"VariableLibrary '{name}' has '{active}' active, expected '{args.environment}'.")
        values = library.resolve(active)
        for variable in library.environment_variables():
            description, allowed = expected_target(variable, workspace_id, by_key)
            value = str(values[variable]).lower()
            target = "workspace" if value == workspace_id else names_by_id.get(value, "NOT IN TARGET WORKSPACE")
            print(f"    {variable:24} = {value}  ({target})")
            if value not in allowed:
                errors.append(f"VariableLibrary '{name}' {variable} = {value} in '{active}', expected {description}.")

    allowed_ids = {workspace_id, ZERO_GUID} | set(by_key.values())
    for (item_type, name), parts in definitions.items():
        if item_type == "VariableLibrary":
            continue
        for path, text in parts.items():
            for guid in sorted({g.lower() for g in GUID.findall(text)} - allowed_ids):
                errors.append(f"{item_type} '{name}' ({path}) references {guid}, which is outside the target workspace.")
            for library_name, variable in LIBRARY_REFERENCE.findall(text):
                if library_name in libraries and variable not in libraries[library_name].defaults:
                    errors.append(f"{item_type} '{name}' references missing variable {library_name}/{variable}.")
        if item_type == "Notebook":
            text = "\n".join(parts.values())
            references = LIBRARY_REFERENCE.findall(text)
            if "%%configure" in text and references:
                summary = ", ".join(
                    f"{v}={libraries[l].resolve(active_sets[l]).get(v)}" if l in libraries else f"{v}=?" for l, v in references
                )
                print(f"  Notebook '{name}': %%configure resolves {summary}")
            lakehouse = re.search(r'"default_lakehouse"\s*:\s*"([^"]+)"', text)
            if lakehouse:
                print(f"  Notebook '{name}': attached lakehouse '{names_by_id.get(lakehouse[1].lower(), lakehouse[1])}'")
        elif item_type == "DataPipeline":
            bindings = json.loads(parts["pipeline-content.json"])["properties"].get("libraryVariables") or {}
            unresolved = [
                alias for alias, b in bindings.items()
                if b["libraryName"] not in libraries or b["variableName"] not in libraries[b["libraryName"]].defaults
            ]
            for alias in unresolved:
                errors.append(f"DataPipeline '{name}' library variable {alias} does not resolve.")
            print(f"  DataPipeline '{name}': {len(bindings) - len(unresolved)}/{len(bindings)} library variable bindings resolved")
        elif item_type == "SQLDatabase":
            source_folder = expected[(item_type, name)]
            source_objects = {
                p.relative_to(source_folder).as_posix().lower()
                for p in source_folder.rglob("*.sql")
                if ".sharedqueries" not in p.parts
            }
            deployed_objects = {path.lower() for path in parts if path.lower().endswith(".sql")}
            for path in sorted(source_objects - deployed_objects):
                errors.append(f"SQLDatabase '{name}' is missing schema object {path}.")
            print(f"  SQLDatabase '{name}': {len(source_objects & deployed_objects)}/{len(source_objects)} schema objects present")

    if errors:
        for error in errors:
            log_error(error)
        raise DeploymentError(f"Verification failed with {len(errors)} error(s).")
    print("Verification passed: the target workspace uses only its own IDs.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--environment", required=True, help="Value set name for the target environment, e.g. uat.")
    parser.add_argument("--workspace-id", help="Target Fabric workspace ID (required unless --check-only).")
    parser.add_argument("--repository-directory", type=Path, default=REPO_ROOT)
    parser.add_argument("--check-only", action="store_true", help="Run the static check only; no credentials needed.")
    parser.add_argument(
        "--items-in-scope",
        default=",".join(ITEM_TYPES),
        help=f"Comma-separated item types to deploy (default: all). Supported: {', '.join(ITEM_TYPES)}.",
    )
    args = parser.parse_args()
    args.repository_directory = args.repository_directory.resolve()

    try:
        args.item_types = parse_items_in_scope(args.items_in_scope)
        check_repository(args.repository_directory, args.environment, args.item_types)
        if args.check_only:
            return 0
        if not args.workspace_id or not GUID.fullmatch(args.workspace_id):
            raise DeploymentError("--workspace-id must be the target workspace GUID.")

        from azure.identity import AzureCliCredential

        credential = AzureCliCredential()
        publish(args, credential)
        if "SQLDatabase" in args.item_types:
            deploy_sql_schema(args, credential)
        verify(args, credential)
    except DeploymentError as error:
        log_error(str(error))
        return 1
    print(f"\nDeployment to workspace {args.workspace_id} ({args.environment}) completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
