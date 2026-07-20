# V12.7.1 Dual Pre-open Schedule Fix

## Scope

This is a scheduling and report-identity patch for **Stone AI Investment Manager Pro V12.7.1 Final Freeze**.
It does not change investment strategy, DQS, Risk Score, Opportunity Score, trading gates, portfolio data, or the manual-confirmation boundary.

## Result

- China pre-open workflow: `.github/workflows/daily.yml`
  - Weekdays at **08:35 Asia/Shanghai**.
  - GitHub UTC cron: `35 0 * * 1-5`.
  - Report label: `北京时间 08:35`.
  - Instance ID: `CN_PREOPEN`.
  - Artifact prefix: `stone-ai-cn-preopen-reports-`.

- US pre-open workflow: `.github/workflows/daily-us.yml`
  - Weekdays at **08:40 America/New_York**.
  - EDT trigger: `40 12 * * 1-5`.
  - EST trigger: `40 13 * * 1-5`.
  - Runtime DST gate accepts exactly the active seasonal trigger; the inactive trigger exits without generating or emailing a report.
  - Report label: `美东时间 08:40`.
  - Instance ID: `US_PREOPEN`.
  - Artifact prefix: `stone-ai-us-preopen-reports-`.

## Isolation guarantees

- Independent workflow files.
- Independent concurrency groups.
- Independent report labels and instance IDs.
- Independent artifact names.
- Each production workflow invokes the single production entrypoint: `python main.py`.
- A CN run cannot cancel or overwrite a US run, and a US run cannot cancel or overwrite a CN run.

## Audit metadata

`run_status.json` now records:

- `report_identity`
- `report_instance_id`

The final decision bundle report metadata also records:

- `report_run_label`
- `report_instance_id`

Failure-email subjects include the report label when available, so CN and US production failures are distinguishable.

## Verification

- Workflow YAML parsing: passed for `daily.yml`, `daily-us.yml`, and `test.yml`.
- Full pytest regression suite: **414 passed**.
- No production version upgrade was introduced.
- No investment-rule files were modified.

A direct `python main.py` smoke run could not finish inside the isolated editing environment because external market-data calls exceeded the execution timeout. This does not affect the completed offline regression result; GitHub Actions will exercise the production path with the repository's configured network and secrets.
