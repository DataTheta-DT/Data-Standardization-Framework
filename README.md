#  Data Standardization Framework

A reusable Databricks framework that converts laboratory reports into clean, standardized results.
It supports digital PDFs, scanned PDFs, images. Test names are mapped to official LOINC codes, units are checked and converted using UCUM, and each result is validated.

The framework is configuration-driven. Thresholds, the catalog/schema/volume names and the optional LLM settings live in `config/pipeline_config.yaml`, so a new client needs new settings, not new code.

## Features

* Accepts digital PDFs, scanned PDFs, photos (JPG/PNG/TIFF).
* Classifies reports and selects the appropriate extraction method.
* Structures results using an optional LLM, with rule-based parsing as a fallback.
* Maps test names using official LOINC reference data.
* Validates and converts units using official UCUM reference data.
* Produces one simple structured table and one simple standardized table per report, in Delta, CSV, Excel and JSON.

## Validation Outcomes

| Status | What it means |
|-|-|
| `PASS` | 	Result is standardized with no validation issues. |
| `WARN` | Result is standardized, but needs attention. |
| `FAIL` | Result could not be safely standardized. The reason is recorded. |

Only `PASS` and `WARN` rows reach the gold table; every row, including `FAIL`, is kept in the standardized files with its reason.

## Configuration

Update `config/pipeline_config.yaml` to customize:

* **Pipeline:** catalog, schema, volume and table names.
* **Thresholds:** the LOINC match score and rank cut-offs, extraction confidence, and the warning threshold for a weak match.
* **LLM (optional):** provider, base URL, model and fallback models for the Structure step. Leave unset to use rules only.
* **OCR:** backend (`auto`, `onnx` or `paddleocr_vl`) and the minimum text density that marks a PDF as digital rather than scanned.

## Setup and Execution

1. Upload the project folder to your Databricks workspace.
2. Keep the `config`, `labstd`, `reference`, `schema` and `notebooks` folders together.
3. Run`notebooks/00_setup.py`, using Serverless compute to create the required tables and load LOINC and UCUM reference data.
4. Run `01` to `07` in order to execute the pipeline.
5. Update `config/pipeline_config.yaml` to change project settings.
6. Configure an LLM API key if LLM-based structuring is required. Otherwise, rule-based processing is used automatically.

## Pipeline

* **Setup**  Load configuration and reference data.
* **Input**  Upload or provide laboratory reports.
* **Read & Classify**  Identify each file type.
* **Extract**  Read text using PDF extraction, OCR or  parsing.
* **Structure** Convert extracted content into structured test results.
* **Standardize**  Map test names to LOINC and validate/convert units with UCUM.
* **Validate & Export**  Check results and save standardized outputs.


## Prerequisites
* Databricks workspace with PySpark (Free Edition with Serverless compute is supported).
* Permissions to create schemas, volumes and tables.
* Official LOINC and UCUM reference data.
* Hugging Face, Gemini, Groq API key (optional, for LLM-based structuring).

## Technologies

* Python
* PySpark
* Apache Spark
* Databricks
* Delta Lake
* Official LOINC terminology
* Official UCUM units of measure

## Sources

* [Apache Spark / PySpark](https://github.com/apache/spark)
* [Delta Lake](https://github.com/delta-io/delta)
* [LOINC](https://loinc.org)
* [UCUM](https://ucum.org)
* [PyMuPDF](https://github.com/pymupdf/PyMuPDF)
* [ONNX Runtime](https://github.com/microsoft/onnxruntime)
* [RapidOCR](https://github.com/RapidAI/RapidOCR)
* [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz)
