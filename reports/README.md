# Benchmark Reports

This folder is populated by `benchmarks/load_test.py`.

Expected generated files:
- `performance_metrics.csv`: raw metrics per scenario and client count
- `performance_summary.md`: markdown table and bottleneck summary

Example run:

```bash
python benchmarks/load_test.py --host localhost --port 5555 --clients 5,20,50,100
```
