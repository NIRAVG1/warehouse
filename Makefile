.PHONY: setup data db pipeline eval test app clean

setup:
	python3 -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt
	@echo "Setup complete. Activate with: source .venv/bin/activate"

data:
	. .venv/bin/activate && python generate_data.py --weeks 52 --hcps 5000 --seed 42

db:
	. .venv/bin/activate && python -m db.schema

pipeline:
	. .venv/bin/activate && python scripts/run_pipeline.py

eval:
	. .venv/bin/activate && python scripts/generate_eval_report.py

test:
	. .venv/bin/activate && pytest tests/ -v --cov=.

app:
	. .venv/bin/activate && streamlit run ui/app.py

run: data db pipeline eval app

clean:
	rm -rf __pycache__ .pytest_cache htmlcov .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} +
