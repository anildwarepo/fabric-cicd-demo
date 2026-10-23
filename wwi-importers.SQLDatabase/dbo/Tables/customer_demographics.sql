CREATE TABLE [dbo].[customer_demographics] (
    [cd_demo_sk]            INT       NOT NULL,
    [cd_gender]             CHAR (1)  NULL,
    [cd_marital_status]     CHAR (1)  NULL,
    [cd_education_status]   CHAR (20) NULL,
    [cd_purchase_estimate]  INT       NULL,
    [cd_credit_rating]      CHAR (10) NULL,
    [cd_dep_count]          INT       NULL,
    [cd_dep_employed_count] INT       NULL,
    [cd_dep_college_count]  INT       NULL,
    CONSTRAINT [PK_customer_demographics] PRIMARY KEY CLUSTERED ([cd_demo_sk] ASC)
);


GO

