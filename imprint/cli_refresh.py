"""Refresh uses the same fingerprinted, failure-safe pipeline as ingestion.

Usage: python -m imprint.cli_refresh <target_dir> <dir:project> ...
"""
from .cli_index import main as index_sources


def main():
    index_sources(command="refresh")


if __name__ == "__main__":
    main()
