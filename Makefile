# =============================================================================
#  EpisodeSleuth - unified build front-door (Linux/macOS)
#
#  A thin, self-documenting wrapper over the project's build/package scripts so
#  every common task has one obvious command. On Windows use make.ps1, which
#  exposes the same verbs (e.g. "pwsh make.ps1 build").
#
#  Run "make" or "make help" to list all targets.
# =============================================================================
.DEFAULT_GOAL := help
.PHONY: help install dev test lint clean clean-deep build package deb tarball dist

PYTHON ?= python3

## help: show this help
help:
	@echo "EpisodeSleuth build commands:"
	@echo ""
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  make /'
	@echo ""
	@echo "Tips:"
	@echo "  BUNDLE_MODEL=1 make build   # pack the Vosk model for a fully offline app"
	@echo "  ONEFILE=1 make build        # build a single-file binary"

## install: install the package and runtime dependencies (editable)
install:
	$(PYTHON) -m pip install -e .

## dev: install with development/test extras
dev:
	$(PYTHON) -m pip install -e . && $(PYTHON) -m pip install pytest

## test: run the test suite
test:
	$(PYTHON) -m pytest -q

## lint: byte-compile every module to catch syntax errors
lint:
	$(PYTHON) -m compileall -q engine gui cli *.py

## clean: remove build, packaging and Python cache artifacts
clean:
	@bash ./clean.sh

## clean-deep: clean and also remove the throwaway build venv
clean-deep:
	@bash ./clean.sh --deep

## build: build a standalone one-folder binary (dist/EpisodeSleuth/)
build:
	@bash ./build_binary.sh

## package: build the Linux release archives (tar.gz + .deb)
package:
	@bash ./package_linux.sh

## dist: clean, then build and package from scratch
dist: clean build package
	@echo "[OK] Fresh build + packages are in dist/"
