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

The library has one value set per environment. The **Default value set** holds the
Dev values; the `uat` and `prod` value sets override every workspace, lakehouse,
and SQL database ID with that environment's ID. Each workspace activates its own
value set, so items read the right IDs without any change to their definitions
(see CI/CD below). The connection IDs are tenant-level and shared by all
environments. Credentials remain in Fabric connections, not in the variable
library or Git.

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
is a PySpark notebook. Its first cell uses `%%configure` to attach
[CustomerDemographicsLakehouse](./CustomerDemographicsLakehouse.Lakehouse/) as the
default lakehouse, reading the lakehouse name, ID, and workspace ID from
`CustomerDemographicsConfig`, so each environment attaches its own lakehouse.
Open the notebook in Fabric and select **Run all**. It:

1. Reads and displays the active source and lakehouse settings from
   `CustomerDemographicsConfig`.
2. Builds the ADLS `abfss://` source path from those values.
3. Reads the matching `.dat` files with explicit TPC-DS column names and types,
   failing on malformed records rather than silently inferring or discarding them.
4. Displays per-file row counts, the total row count, and the first 20 rows ordered
   by `cd_demo_sk`.
5. Exposes the DataFrame as the session-local Spark SQL view
   `customer_demographics_preview` and returns a JSON execution summary that
   includes the attached default lakehouse.

The notebook reads ADLS directly using its execution identity; it does not use
the pipeline's stored OAuth connection, and it does not modify the SQL database
or write a lakehouse table. The identity must have permission to read the source
and the Spark session must have network access to the account. The attached
lakehouse does not by itself grant access to external storage. No credentials,
storage keys, or SAS tokens are stored in the notebook or variable library.

The initial Spark validation completed successfully with four files and
1,920,800 rows. Saved run snapshots retain the displayed configuration, per-file
counts, and 20-row preview; open the notebook's run history to inspect them.

## CI/CD: promote to UAT and Prod with fabric-cicd

For the design, diagrams, and lessons learned (value sets, activation, when to use
`parameter.yml`), see the [Fabric CI/CD design guide](./docs/fabric-cicd-design.md).

Items are promoted from Git by one Azure Pipeline that uses the
[fabric-cicd](https://microsoft.github.io/fabric-cicd/) library. Each environment has
its own workspace, lakehouse, and SQL database. Only the Dev workspace is connected to
Git.

```text
anildwa-dev-workspace --(Fabric Source control: Commit)--> branch dev
    --(pull request)--> branch uat  --(pipeline + UAT approval)-->  anildwa-uat-workspace
    --(pull request)--> branch prod --(pipeline + PROD approval)--> anildwa-prod-workspace
```

| File | Purpose |
|---|---|
| [deploy/azure-pipelines.yml](./deploy/azure-pipelines.yml) | Azure Pipeline `fabric-cicd-deploy`; runs on commits to `uat` and `prod` |
| [deploy/deploy.py](./deploy/deploy.py) | Static check, publish, SQL schema deployment, and verification |
| [deploy/requirements.txt](./deploy/requirements.txt) | Pinned deployment dependencies |

### How environment values work

Environment-specific values live **only** in the variable library's value sets. No
IDs are replaced during deployment, and there is no `parameter.yml`.

| Value set | Used in | Holds |
|---|---|---|
| Default value set | `anildwa-dev-workspace` | Dev IDs |
| `uat` | `anildwa-uat-workspace` | UAT IDs |
| `prod` | `anildwa-prod-workspace` | Prod IDs |

- The pipeline passes the branch name (`uat` or `prod`) to fabric-cicd, which
  activates the value set with exactly that name. Names are case-sensitive; if no
  value set matches, fabric-cicd would silently activate the Default value set, so
  the static check fails instead.
- The copy pipeline reads its IDs through `@pipeline().libraryVariables`, and the
  notebook reads them with `notebookutils.variableLibrary` and `%%configure`. Neither
  definition contains an ID.

### Release a change

1. Develop and test in `anildwa-dev-workspace`. If you add a workspace, lakehouse,
   or SQL database ID to the library, add its UAT and Prod values to the `uat` and
   `prod` value sets.
2. In Fabric, open **Source control** and commit to `dev`.
3. In Azure DevOps, create a pull request from `dev` into `uat` and complete it.
   The pipeline validates, waits for approval on the **UAT** environment, then
   deploys and verifies `anildwa-uat-workspace`.
4. After testing in UAT, create a pull request from `uat` into `prod` and complete
   it. The pipeline does the same for **PROD** and `anildwa-prod-workspace`.

### What the pipeline checks

- **Validate** (no credentials) fails if:
  - the library has no value set named exactly like the environment;
  - the value set doesn't override every workspace, lakehouse, or database ID, so
    it would fall back to the Dev value;
  - two value sets share the same ID (for example a copy-pasted UAT ID in `prod`);
  - any notebook, pipeline, lakehouse, or SQL project contains a hard-coded ID; or
  - a notebook or pipeline references a library variable that doesn't exist.
- **Deploy** publishes the items, deploys the SQL project schema (fabric-cicd 1.4.0
  creates SQL databases without their schema; existing data is kept), then
  **verifies** the workspace and fails if:
  - the active value set isn't the environment;
  - any workspace, lakehouse, or database ID in it doesn't belong to the target
    workspace;
  - an item references an ID outside the target workspace; or
  - a pipeline library binding or SQL schema object is missing.

The pipeline only deploys definitions; it doesn't run the copy pipeline or load
data. Items deleted from Git are not removed from UAT or Prod; delete them manually.

The ADLS account `anildwafabricadlsgen2` has public network access disabled. Only
the Dev workspace currently has a network path to it, so the UAT and Prod notebooks
attach their own lakehouse but fail to read the `.dat` files with
`403 AuthorizationFailure` until each workspace is given access (for example a
managed private endpoint approved on the storage account).

### Deploy only some item types (`items_in_scope`)

Runs triggered by a merge deploy all types:
`Lakehouse,SQLDatabase,VariableLibrary,Notebook,DataPipeline`. To deploy a subset,
run the pipeline manually from the `uat` or `prod` branch and edit **Fabric item
types to deploy**. Items of other types are left unchanged, and any they reference
must already exist in the target workspace.

### Set up a new environment

A new environment's lakehouse and SQL database must exist before their IDs can be
added to its value set:

1. Follow [Connect Azure DevOps to Fabric](#connect-azure-devops-to-fabric) to create
   the workspace access, service principal, federated service connection, and Azure
   DevOps environment with an approval check. For an environment other than UAT or
   Prod, also add its branch-to-workspace mapping to
   [deploy/azure-pipelines.yml](./deploy/azure-pipelines.yml).
2. Create the lakehouse and SQL database by running the pipeline manually with
   `items_in_scope` = `Lakehouse,SQLDatabase`.
3. In the Dev workspace, add a value set named like the environment with the new
   IDs, commit, and promote as usual.

### Run the checks locally

```powershell
python -m pip install -r deploy/requirements.txt
python deploy/deploy.py --environment uat --check-only
python deploy/deploy.py --environment prod --check-only
```

### Identity and permissions

| Environment | Workspace | Service connection / principal | Approval |
|---|---|---|---|
| UAT | `anildwa-uat-workspace` | `fabric-cicd-uat` / `sp-fabric-cicd-uat` | Azure DevOps environment **UAT** |
| Prod | `anildwa-prod-workspace` | `fabric-cicd-prod` / `sp-fabric-cicd-prod` | Azure DevOps environment **PROD** |

Each service connection uses workload identity federation (no secret). Fabric API
authorization uses workspace roles, not Azure subscription RBAC. Each service
principal is **Contributor** on its own workspace only.

The copy pipeline uses the shared tenant-level connections in
`CustomerDemographicsConfig`; whoever runs it must be able to use them. To give an
environment its own connections, override `SourceConnectionId` and
`DestinationConnectionId` in its value set.

### Connect Azure DevOps to Fabric

There are two separate connections:

- **Fabric Git integration** lets developers synchronize Dev item definitions with
  Azure Repos. It does not give Azure Pipelines permission to publish to Fabric.
- **An Azure DevOps service connection** lets the deployment pipeline authenticate
  as a Microsoft Entra service principal and publish definitions through Fabric
  REST APIs. UAT and Prod do **not** need Git integration enabled.

The publishing authentication flow is:

```text
Azure Pipeline -> service connection -> federated Microsoft Entra token
    -> AzureCLI@2 login -> AzureCliCredential -> Fabric APIs -> target workspace
```

No client secret, personal access token (PAT), or interactive sign-in is needed
for pipeline publishing.

#### 1. Prerequisites and Dev Git connection

- Have the target workspaces assigned to an active Fabric capacity that supports
  the deployed item types.
- Arrange permission to create Entra app registrations and a security group, a
  Fabric administrator to configure tenant settings, and a workspace administrator
  to grant access. In Azure DevOps, you need permission to create service
  connections, pipelines, and environments.
- In Azure Repos, create the `dev`, `uat`, and `prod` branches if they do not
  already exist.
- For the Dev source-control workflow only, have a Fabric administrator enable
  **Users can synchronize workspace items with their Git repositories** for the
  developers. As a Dev workspace administrator, open **Workspace settings > Git
  integration > Azure DevOps**, sign in with an account that can read and
  contribute to the repository, and select the organization, project, repository,
  `dev` branch, and repository root folder. Review the initial synchronization
  direction before connecting to avoid replacing the wrong copy of the items.
  Leave UAT and Prod disconnected from Git.

#### 2. Create one deployment identity per environment

In the Microsoft Entra admin center, open **App registrations > New registration**:

1. Register `sp-fabric-cicd-uat` as a single-tenant application in the **Fabric
   tenant**. No redirect URI is required.
2. Record its **Application (client) ID** and **Directory (tenant) ID**. The client
   ID is not the application's or enterprise application's object ID.
3. Repeat for `sp-fabric-cicd-prod`.
4. Create an Entra security group for Fabric deployment API access and add both
   **service principals** (the corresponding enterprise applications) as members.

Do not create a client secret. For the Fabric item REST APIs used here, access is
controlled by the tenant setting and workspace role below; adding delegated API
permissions to the app registration is not a substitute.

#### 3. Allow the identities to call Fabric APIs

A Fabric administrator must open **Tenant settings > Developer settings** in the
Fabric admin portal (also available under **OneLake catalog > Govern >
Configurations**). Enable **Service principals can use Fabric APIs**, also labeled
**Service principals can call Fabric public APIs** in some portal versions.
Scope it to the security group from step 2 rather than the whole organization.

This setting only permits API use; it does not grant access to any workspace.
Read-only or update **admin API** tenant switches are not needed for this pipeline.

#### 4. Grant access to each target workspace

In Fabric, open the target workspace and select **Manage access > Add people or
groups**. Find the deployment application by name and grant **Contributor**:

| Principal | Grant Contributor on | Do not grant access to |
|---|---|---|
| `sp-fabric-cicd-uat` | `anildwa-uat-workspace` | Dev or Prod |
| `sp-fabric-cicd-prod` | `anildwa-prod-workspace` | Dev or UAT |

Use the shared security group only to scope the tenant setting, not to give both
identities access to both workspaces. Copy each workspace ID from its Fabric URL
and check the `targetWorkspaceId` mapping in
[deploy/azure-pipelines.yml](./deploy/azure-pipelines.yml).

Azure subscription **Contributor** or **Owner** does not grant Fabric workspace
access. Conversely, Fabric Contributor does not grant access to ADLS, stored
connection credentials, or other external data sources.

#### 5. Create the federated Azure DevOps service connections

Repeat these steps for UAT and Prod, using the matching application and connection
names from the [identity table](#identity-and-permissions):

1. In Azure DevOps, open **Project settings > Service connections > New service
   connection > Azure Resource Manager**.
2. Choose **App registration or Managed identity (manual)** and **Workload
   identity federation**. Older forms call this **Workload identity federation
   (manual)**. Use the existing app registration rather than an automatic option
   that creates a new identity and assigns Azure resource permissions.
3. Enter `fabric-cicd-uat` or `fabric-cicd-prod` as the service connection name,
   select **Azure Cloud**, and enter the corresponding tenant and client IDs.
   Complete the scope metadata required by the form. If using **Subscription**
   scope, use a real subscription that the identity can select: ARM verification
   and `AzureCLI@2` subscription selection can require Azure RBAC read access
   (for example **Reader**). That access is separate from Fabric authorization;
   do not grant Azure Contributor or Owner just to publish Fabric items.
4. Leave **Grant access permission to all pipelines** unchecked. Copy the
   generated **Issuer** and **Subject identifier**, then **Keep as draft**.
5. In Entra, open the matching app registration and select **Certificates &
   secrets > Federated credentials > Add credential > Other issuer**. Paste the
   issuer and subject **exactly as Azure DevOps generated them**. Use
   `api://AzureADTokenExchange` as the audience for Azure Cloud and save the
   credential with a descriptive name. Do not construct the issuer or subject
   from examples; formats can vary between connections.
6. Return to the draft service connection, select **Finish setup**, and
   **Verify and save**. ARM verification is not a test of Fabric workspace access.
7. Under the connection's **Pipeline permissions**, authorize only
   `fabric-cicd-deploy` once the pipeline exists.

The existing `AzureCLI@2` task refers to the connection through its
`azureSubscription` input. Despite the input's name, the deployment script uses
that login to request a token for `https://api.fabric.microsoft.com/.default`.
Its `AzureCliCredential` is passed to fabric-cicd and used for schema deployment
and verification. No extra secret variables or separate login command are needed.

#### 6. Configure the pipeline and approval gates

1. In **Pipelines > Environments**, create **UAT** and **PROD** with those exact
   names. On each, open **Approvals and checks**, add an approval check, and select
   the appropriate release approvers. Authorize the deployment pipeline to use
   each environment.
2. Create an Azure Pipeline from this Azure Repos repository using **Existing
   Azure Pipelines YAML file** and select
   [deploy/azure-pipelines.yml](./deploy/azure-pipelines.yml). Name it
   `fabric-cicd-deploy`.
3. Confirm the YAML's branch mappings select the correct workspace ID, service
   connection name, and approval environment. The variable library must have the
   corresponding `uat` and `prod` value sets with the target item IDs.
4. Authorize the service connections for this pipeline as described above.

The supplied pipeline uses a Microsoft-hosted Ubuntu agent with Python and Azure
CLI. A self-hosted agent also needs those tools, the deployment dependencies, and
network access to Entra, Fabric APIs, and its approved package feed.

#### 7. Verify publishing

Run `fabric-cicd-deploy` manually from **uat**, not `dev` or another branch:

1. Confirm **Validate** succeeds; it requires no Fabric credentials.
2. Confirm deployment waits for the **UAT** approval, then approve it.
3. Confirm **Publish and verify with fabric-cicd** succeeds, including SQL schema
   deployment and post-deployment verification.
4. Open `anildwa-uat-workspace` and check that the items exist and the variable
   library's active value set is `uat`. After UAT testing, promote to `prod` and
   repeat with the **PROD** approval.

For a new workspace without lakehouse/database IDs yet, first use the
[new-environment bootstrap](#set-up-a-new-environment) with
`items_in_scope = Lakehouse,SQLDatabase`, then complete the value sets before a
full deployment. A successful publish does not test data-source credentials or
run the copy pipeline.

#### Setup troubleshooting

| Symptom | Check |
|---|---|
| Service connection not found or pipeline not authorized | Exact connection name in YAML, connection **Pipeline permissions**, and environment pipeline permissions. |
| `AADSTS70021` / no matching federated identity record | Correct app/client and tenant IDs; exact issuer, subject, and audience on that app's federated credential. Recheck these after renaming or recreating a connection. |
| ARM verification fails, or Azure CLI cannot select the subscription | Subscription scope metadata and the identity's subscription visibility. Azure RBAC read access may be needed for the connection's ARM login, but does not replace Fabric permissions. |
| Fabric API returns `401` or `403` | Correct Fabric tenant and token resource, enabled API tenant setting, service principal membership in the allowed group, and Contributor on the exact target workspace. Allow time for permission changes to propagate. |
| Deployment waits or cannot use an environment | UAT/PROD approval check, approver availability, and pipeline authorization on the Azure DevOps environment. |
| Publish succeeds but a notebook or copy run fails | Separate data-source permissions, Fabric connection access/credentials, and network access. See [Connection troubleshooting](#connection-troubleshooting) and the UAT/Prod storage limitation above. |

References: [Fabric API identity support](https://learn.microsoft.com/en-us/rest/api/fabric/articles/identity-support),
[manual Azure DevOps workload identity federation setup](https://learn.microsoft.com/en-us/azure/devops/pipelines/release/configure-workload-identity?view=azure-devops),
and [Fabric Git integration setup](https://learn.microsoft.com/en-us/fabric/cicd/git-integration/git-get-started?tabs=azure-devops).
