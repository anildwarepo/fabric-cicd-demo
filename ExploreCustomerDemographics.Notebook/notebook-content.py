# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "8340a38b-0be0-4b4d-a360-6492cff7ae66",
# META       "default_lakehouse_name": "CustomerDemographicsLakehouse",
# META       "default_lakehouse_workspace_id": "e2ebf2eb-f7df-42a3-b5e3-e033cf7d32c6"
# META     }
# META   }
# META }

# MARKDOWN ********************

#  Explore customer demographics
# Read the active `CustomerDemographicsConfig` values and preview the four
# pipe-delimited ADLS files. This notebook does not copy data or modify tables.
# Direct ADLS reads use the notebook execution identity, not the pipeline's
# stored connection credentials. That identity needs storage read permission
# and network access to the ADLS account.

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
}
if any(not isinstance(value, str) or not value.strip() for value in source_config.values()):
    raise ValueError("All source and lakehouse configuration values must be nonempty strings.")

display(spark.createDataFrame(sorted(source_config.items()), ["setting", "value"]))

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
        }
    )
)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
