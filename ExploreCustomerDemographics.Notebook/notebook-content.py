# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# MARKDOWN ********************

#  Explore customer demographics
# Read the active `CustomerDemographicsConfig` values and preview the four
# pipe-delimited ADLS files. The first code cell attaches the default lakehouse
# from the library with `%%configure`, so each environment uses its own lakehouse. This notebook does not copy data or modify tables.
# Direct ADLS reads use the notebook execution identity, not the pipeline's
# stored connection credentials. That identity needs storage read permission
# and network access to the ADLS account.

# CELL ********************

# MAGIC %%configure
# MAGIC {
# MAGIC     "defaultLakehouse": {
# MAGIC         "name": {"variableName": "$(/**/CustomerDemographicsConfig/LakehouseName)"},
# MAGIC         "id": {"variableName": "$(/**/CustomerDemographicsConfig/LakehouseId)"},
# MAGIC         "workspaceId": {"variableName": "$(/**/CustomerDemographicsConfig/LakehouseWorkspaceId)"}
# MAGIC     }
# MAGIC }

# CELL ********************

import json

import notebookutils
from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType, StringType, StructField, StructType

config = notebookutils.variableLibrary.getLibrary("CustomerDemographicsConfig")

source_config = {
    "storage_account": config.SourceStorageAccount,
    "file_system": config.SourceFileSystem,
    "folder_path": config.SourceFolderPath,
    "file_pattern": config.SourceFilePattern,
    "lakehouse_workspace_id": config.LakehouseWorkspaceId,
    "lakehouse_id": config.LakehouseId,
    "lakehouse_name": config.LakehouseName,
}
if any(not isinstance(value, str) or not value.strip() for value in source_config.values()):
    raise ValueError("All source and lakehouse configuration values must be nonempty strings.")

display(spark.createDataFrame(sorted(source_config.items()), ["setting", "value"]))

# Fail if the attached default lakehouse isn't the one in the active value set.
context = notebookutils.runtime.context
attached = {
    "id": str(context.get("defaultLakehouseId") or ""),
    "workspace_id": str(context.get("defaultLakehouseWorkspaceId") or ""),
    "name": str(context.get("defaultLakehouseName") or ""),
}
if (attached["id"].lower(), attached["workspace_id"].lower()) != (
    source_config["lakehouse_id"].lower(),
    source_config["lakehouse_workspace_id"].lower(),
):
    raise ValueError(f"Attached default lakehouse {attached} does not match the variable library {source_config}.")
print(f"Default lakehouse: {attached['name']} ({attached['id']}) in workspace {attached['workspace_id']}")


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

source_path = (
    f"abfss://{source_config['file_system']}@{source_config['storage_account']}.dfs.core.windows.net/"
    f"{source_config['folder_path']}/{source_config['file_pattern']}"
)
print(f"Reading: {source_path}")

schema = StructType([
    StructField("cd_demo_sk", IntegerType(), False),
    StructField("cd_gender", StringType(), True),
    StructField("cd_marital_status", StringType(), True),
    StructField("cd_education_status", StringType(), True),
    StructField("cd_purchase_estimate", IntegerType(), True),
    StructField("cd_credit_rating", StringType(), True),
    StructField("cd_dep_count", IntegerType(), True),
    StructField("cd_dep_employed_count", IntegerType(), True),
    StructField("cd_dep_college_count", IntegerType(), True),
    StructField("_trailing_delimiter", StringType(), True),
])

# TPC-DS rows end with a pipe, creating an empty tenth field.
raw_demographics = (
    spark.read.schema(schema)
    .option("sep", "|")
    .option("header", "false")
    .option("quote", "")
    .option("encoding", "UTF-8")
    .option("mode", "FAILFAST")
    .csv(source_path)
    .withColumn("source_file", F.input_file_name())
)

if raw_demographics.filter(
    F.col("cd_demo_sk").isNull()
    | (F.coalesce(F.col("_trailing_delimiter"), F.lit("")) != "")
).limit(1).count():
    raise ValueError("A source row has a missing demographic key or a nonempty trailing field.")

customer_demographics = raw_demographics.drop("_trailing_delimiter")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

file_counts = customer_demographics.groupBy("source_file").count().orderBy("source_file")
counts = file_counts.collect()
row_count = sum(row["count"] for row in counts)
if not counts:
    raise ValueError(f"No customer demographics rows found at {source_path}.")

display(file_counts)
print(f"Rows: {row_count:,} across {len(counts)} files")
display(customer_demographics.orderBy("cd_demo_sk").limit(20))

customer_demographics.createOrReplaceTempView("customer_demographics_preview")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# The DataFrame is also available as the Spark temporary view
# `customer_demographics_preview`. The attached lakehouse remains unchanged.

# CELL ********************

notebookutils.notebook.exit(
    json.dumps(
        {
            "library": "CustomerDemographicsConfig",
            "sourcePath": source_path,
            "fileCount": len(counts),
            "rowCount": row_count,
            "previewRows": 20,
            "defaultLakehouseId": attached["id"],
            "defaultLakehouseWorkspaceId": attached["workspace_id"],
        }
    )
)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
