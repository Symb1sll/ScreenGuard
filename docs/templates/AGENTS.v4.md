# Repository guidance

- Application code is under `src/task2_video_panel`; regression tests are under `tests`.
- Keep documentation in `docs` consistent with implemented behavior and runnable commands.
- Use isolated project-local data for verification; preserve operational databases and evidence.
- Do not commit credentials, machine-specific configuration, model weights or generated output.
- Run `python -X utf8 -B tests/run_tests.py` for changes affecting application behavior.
- Windows entry points are in `scripts/windows`; deployment templates are in `deploy`.
