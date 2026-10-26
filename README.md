# Fabric development workspace

Fabric item definitions for `anildwa-dev-workspace`.

## Customer demographics copy

[CopyCustomerDemographics](./CopyCustomerDemographics.DataPipeline/pipeline-content.json)
copies the four TPC-DS files
`customer_demographics_1_4.dat` through `customer_demographics_4_4.dat` from
`https://anildwafabricadlsgen2.dfs.core.windows.net/tpcdsdataset/1g/1g/`
into `wwi-importers.dbo.customer_demographics`.

The files have no header, use a pipe delimiter, and contain nine data fields
followed by a trailing delimiter. Explicit ordinal mappings select only the nine
data fields. Conversion errors fail the load instead of truncating or skipping
data.

### Shared configuration

[CustomerDemographicsConfig](./CustomerDemographicsConfig.VariableLibrary/variables.json)
holds the source connection, storage account, file system, folder and wildcard,
the destination connection, workspace, SQL database item, schema and table, and
the exploration lakehouse IDs. The pipeline binds its source and destination
variables using `@pipeline().libraryVariables.<name>`; the notebook reads its
configuration using `notebookutils.variableLibrary.getLibrary`.

The values in Git target this development workspace. When the items are promoted,
[deploy/parameter.yml](./deploy/parameter.yml) rewrites the workspace, SQL database,
and lakehouse IDs for the target environment (see CI/CD below). Credentials remain
in Fabric connections, not in the variable library or Git.

### Destination and execution

Deploy the [table schema](./wwi-importers.SQLDatabase/dbo/Tables/customer_demographics.sql)
before running the pipeline. Publish the variable library before the pipeline,
and ensure the execution identity can use both referenced connections.

The pipeline inserts into an empty destination table. A pre-copy check explicitly
fails if the table already contains rows; it does not truncate, overwrite, or
silently duplicate an earlier load. Automatic retries are disabled because a
failed copy can leave committed batches. After a partial or completed load,
review the destination before intentionally clearing or otherwise reconciling it
for another run.

Validate a run by checking that it read four files with no skipped rows and that
the destination row count matches the source. The primary key on `cd_demo_sk`
also enforces unique demographic keys.

### Connection troubleshooting

If a run fails with `DMTS_OAuthTokenRefreshFailedError` or `AADSTS700082`, open
Fabric **Settings > Manage connections and gateways** and edit the exact
connections referenced by the library:

- `tpcdsdataset anildwa`: `2cae5b6b-3284-40b0-b415-b8fd3408a7b0`
- `FabricSql anildwa`: `21e5a909-570a-4967-bae4-c2e914d5a1a8`

Sign in again and save each connection. Signing into Fabric alone does not
refresh a connection's stored OAuth credentials. If replacement connections are
created instead, update their IDs in the variable library before rerunning.

## Explore the source with Spark

[ExploreCustomerDemographics](./ExploreCustomerDemographics.Notebook/notebook-content.py)
is a PySpark notebook attached to
[CustomerDemographicsLakehouse](./CustomerDemographicsLakehouse.Lakehouse/).
Open the notebook in Fabric and select **Run all**. It:

1. Reads and displays the active source and lakehouse settings from
   `CustomerDemographicsConfig`.
2. Builds the ADLS `abfss://` source path from those values.
3. Reads the matching `.dat` files with explicit TPC-DS column names and types,
   failing on malformed records rather than silently inferring or discarding them.
4. Displays per-file row counts, the total row count, and the first 20 rows ordered
   by `cd_demo_sk`.
5. Exposes the DataFrame as the session-local Spark SQL view
   `customer_demographics_preview` and returns a JSON execution summary.

The notebook reads ADLS directly using its execution identity; it does not use
the pipeline's stored OAuth connection, and it does not modify the SQL database
or write a lakehouse table. The identity must have permission to read the source
and the Spark session must have network access to the account. The attached
lakehouse does not by itself grant access to external storage. No credentials,
storage keys, or SAS tokens are stored in the notebook or variable library.

The initial Spark validation completed successfully with four files and
1,920,800 rows. Saved run snapshots retain the displayed configuration, per-file
counts, and 20-row preview; open the notebook's run history to inspect them.

## CI/CD: promote to UAT with fabric-cicd

Items are promoted from Git to `anildwa-uat-workspace` by an Azure Pipeline that
uses the [fabric-cicd](https://microsoft.github.io/fabric-cicd/) library. UAT has
its own lakehouse and SQL database; it is not Git-connected.

```text
anildwa-dev-workspace --(Fabric Source control: Commit)--> branch anildwa-fabric-dev
        --(pull request)--> branch uat --(Azure Pipeline + approval)--> anildwa-uat-workspace
```

| File | Purpose |
|---|---|
| [deploy/azure-pipelines-uat.yml](./deploy/azure-pipelines-uat.yml) | Azure Pipeline `fabric-cicd-deploy-uat`; triggered by commits to `uat`; `items_in_scope` parameter |
| [deploy/deploy.py](./deploy/deploy.py) | Static check, phased publish (including SQL schema), and post-deploy verification |
| [deploy/parameter.yml](./deploy/parameter.yml) | Rewrites Dev IDs to the UAT workspace and its deployed items |
| [deploy/requirements.txt](./deploy/requirements.txt) | Pinned deployment dependencies |

### Release a change to UAT

1. Develop and test in `anildwa-dev-workspace`.
2. In Fabric, open **Source control** and commit the changed items to
   `anildwa-fabric-dev`.
3. In Azure DevOps, create a pull request from `anildwa-fabric-dev` into `uat`
   and complete it.
4. The pipeline runs **Validate**, then waits for approval on the **UAT**
   environment. Approve it under **Pipelines > Environments > UAT** or from the run.
5. **Deploy to UAT** publishes and verifies the items. Review the run log.

### What the pipeline does

- **Validate** (no credentials): fails if a `parameter.yml` entry no longer matches
  the repository, or if any item contains an ID that is not rewritten, so a Dev
  reference cannot leak into UAT.
- **Deploy to UAT**, phase 1: publishes `Lakehouse` and `SQLDatabase`. fabric-cicd
  creates these as shells; lakehouse data is never copied or overwritten.
- **Deploy to UAT**, phase 1b: deploys each SQL project (for example
  `dbo.customer_demographics`) with the Fabric `updateDefinition` API in `sqlproj`
  format, because fabric-cicd 1.4.0 does not publish SQL database definitions.
  Fabric builds the project and applies a differential update; existing UAT data
  is kept. If Fabric rejects a change that would lose data, the run fails and the
  change must be applied manually.
- **Deploy to UAT**, phase 2: publishes `VariableLibrary`, `Notebook`, and
  `DataPipeline`. The two phases exist because fabric-cicd publishes variable
  libraries first, and `$items` references resolve only to items that already
  exist in the target workspace.
- **Verify**: reads the deployed definitions back and fails if any item references
  an ID outside the UAT workspace (other than the shared connection IDs), if the
  notebook is not bound to the UAT lakehouse, if a pipeline library binding is
  unresolved, or if a SQL schema object is missing.

In UAT, `CustomerDemographicsConfig` keeps the **Default value set** active, and its
values point to the UAT workspace, SQL database, and lakehouse. The notebook's
default lakehouse is the UAT `CustomerDemographicsLakehouse`.

The pipeline only deploys definitions; it does not run the UAT copy pipeline or
load data. Items deleted from Git are not removed from UAT (orphan cleanup is
intentionally disabled); delete them in UAT manually if needed.

### Deploy only some item types (`items_in_scope`)

The pipeline has an `items_in_scope` parameter: a comma-separated list of item
types to deploy. The default deploys all of them:

```text
Lakehouse,SQLDatabase,VariableLibrary,Notebook,DataPipeline
```

Runs triggered by a merge into `uat` always use the default. To deploy a subset,
start the pipeline manually: **Pipelines > fabric-cicd-deploy-uat > Run pipeline**,
select the `uat` branch, and edit **Fabric item types to deploy**.

- Validation, publishing, and verification apply only to the selected types.
  Phases with no selected type are skipped.
- Items of other types are left unchanged in UAT. If a selected item references
  one (for example a notebook's default lakehouse), it must already exist in UAT;
  validation warns about this and publishing fails if it is missing.
- Pipeline library bindings are checked against the variable library already in
  UAT when `VariableLibrary` is not selected.
- Excluding `VariableLibrary` keeps the UAT library's current values, but changes
  to its variables in Git are not deployed until it is included again.
- Only the five types above are supported. Unknown types fail validation; to add
  one, see "Add another item or environment" below.

### Run the checks locally

```powershell
python -m pip install -r deploy/requirements.txt
python deploy/deploy.py --environment UAT --check-only
python deploy/deploy.py --environment UAT --check-only --items-in-scope Notebook,DataPipeline
```

### Identity and permissions

| Resource | Configuration |
|---|---|
| Service connection | `fabric-cicd-uat` (Azure Resource Manager, workload identity federation, management-group scope) |
| Service principal | `sp-fabric-cicd-uat`; no client secret or certificate; no Azure RBAC role |
| Fabric access | **Contributor** on `anildwa-uat-workspace` only; no access to Dev |
| Approval | Azure DevOps environment **UAT** requires approval before deployment |

The UAT copy pipeline reuses the tenant-level connections in
`CustomerDemographicsConfig`. Whoever runs it in UAT must be able to use those
connections. To give UAT its own connections, add `key_value_replace` entries for
`SourceConnectionId` and `DestinationConnectionId` in `deploy/parameter.yml`.

### Add another item or environment

- **New item in an existing type**: commit it from Fabric. If the static check
  reports an unparameterized ID, add a `parameter.yml` entry for it.
- **New item type**: add it to `INFRASTRUCTURE_TYPES` (items that others reference)
  or `CONTENT_TYPES` in `deploy/deploy.py`, and to the `items_in_scope` default in
  `deploy/azure-pipelines-uat.yml`.
- **New environment** (for example PROD): add the environment key to every
  `replace_value` in `deploy/parameter.yml`, then create a matching pipeline,
  branch, service connection, and approval environment.
