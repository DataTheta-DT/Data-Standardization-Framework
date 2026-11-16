# Official reference data

Every file here comes from an official source.

| File | Source |
|---|---|
| `ucum/ucum-cs.units`, `ucum/ucum-ci.units` | UCUM case-sensitive and case-insensitive unit tables, UCUM 2.2, https://ucum.org |
| `ucum/ucum-essence.xml` | UCUM essence file. It pairs each case-sensitive code with its case-insensitive code. |
| `ucum/ucum-source.xml` | UCUM specification source, kept with the other UCUM files. Not read by the code. |
| `loinc_lab_active.csv` | Derived from the LOINC 2.83 release (Loinc_2_83.zip) from https://loinc.org. |
| `LoincLicense_5.8.txt` | The LOINC license that came with the release. |

## How loinc_lab_active.csv is derived

Run `python scripts/build_reference.py --loinc Loinc_2_83.zip`.

- From `LoincTable/Loinc.csv`: rows with CLASSTYPE = 1 (laboratory), STATUS = ACTIVE and SCALE_TYP = Qn (quantitative). Columns kept: LOINC_NUM, LONG_COMMON_NAME, SHORTNAME, COMPONENT, PROPERTY, TIME_ASPCT, SYSTEM, SCALE_TYP, METHOD_TYP, CLASS, COMMON_TEST_RANK, RELATEDNAMES2, STATUS, VersionLastChanged.
- `canonical_unit` is the first value of EXAMPLE_UCUM_UNITS.
- `consumer_names` is every ConsumerName for the code from `AccessoryFiles/ConsumerName/ConsumerName.csv` in the same release.
- No value is changed.

To use a newer LOINC release, run the script on the new zip, then run notebook 00_setup again.

## How the data is used

- LOINC names: the analyte in LONG_COMMON_NAME, ConsumerName (with its abbreviations), COMPONENT, the first word of SHORTNAME, and RELATEDNAMES2. Closer sources count for more.
- LOINC unit and ranking: EXAMPLE_UCUM_UNITS decides which codes fit the unit on the report. COMMON_TEST_RANK breaks ties between codes that fit equally well.
- UCUM: all unit parsing, validation and conversion uses these definitions. The print symbol of the micro prefix in the XML (`μ`) is used to read a typed `µ` as `u`. No other unit text is rewritten.

## Notices

This material contains content from LOINC® (http://loinc.org). LOINC is copyright © 1995-2026, Regenstrief Institute, Inc. and the Logical Observation Identifiers Names and Codes (LOINC) Committee and is available at no cost under the license at http://loinc.org/license. LOINC® is a registered United States trademark of Regenstrief Institute, Inc.

UCUM is copyright © 1998-2024 Regenstrief Institute and the UCUM Organization.
