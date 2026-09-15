# Production bundle schema demo

These CSV files are synthetic schema fixtures for `audit_production_data`. They verify parsing,
time semantics, relationships, and capability gating only. They are not market history, licensed
vendor data, a backtest input, or investment evidence.

Run from the repository root:

```bash
python -m apps.quant_research.audit_production_data \
  --instrument-master examples/data/production_bundle_demo/instrument_master.csv.example \
  --membership examples/data/production_bundle_demo/universe_membership.csv.example \
  --bars examples/data/production_bundle_demo/bars.csv.example \
  --corporate-actions examples/data/production_bundle_demo/corporate_actions.csv.example \
  --option-contracts examples/data/production_bundle_demo/option_contracts.csv.example \
  --option-quotes examples/data/production_bundle_demo/option_quotes.csv.example \
  --option-lifecycle-events examples/data/production_bundle_demo/option_lifecycle_events.csv.example \
  --fundamentals examples/data/production_bundle_demo/fundamental_vintages.csv.example \
  --classification-vintages examples/data/production_bundle_demo/classification_vintages.csv.example \
  --macro-vintages examples/data/production_bundle_demo/macro_vintages.csv.example \
  --start 2024-01-01T00:00:00Z \
  --end 2024-02-01T00:00:00Z
```

The command intentionally exits with status 2. The report keeps equity, fundamentals, and macro at
`prototype`, options and lifecycle at `scenario_only`, and emits `NON_HISTORICAL_SOURCE`; a schema-correct fixture
must never unlock historical capability.
