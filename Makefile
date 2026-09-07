# btctrace -- offline Bitcoin transaction traffic monitoring
PY ?= python

.PHONY: help setup demo dashboard test geoip vendor clean

help:
	@echo "make setup      install dependencies"
	@echo "make demo       generate -> ingest -> detect -> evaluate"
	@echo "make dashboard  launch the Streamlit UI (run demo first)"
	@echo "make test       run the check suite"
	@echo "make geoip      download the DB-IP GeoIP database (optional, needs network)"
	@echo "make vendor     download wheels for an air-gapped install"

setup:
	$(PY) -m pip install -r requirements.txt

demo:
	$(PY) -m btctrace.cli pipeline --scale 3

dashboard:
	$(PY) -m streamlit run app.py

test:
	$(PY) tests/test_pipeline.py

geoip:
	sh scripts/fetch_geoip.sh

vendor:
	$(PY) -m pip download -r requirements.txt -d vendor/

clean:
	rm -rf data/raw data/*.parquet data/ground_truth.csv data/rejected.csv
