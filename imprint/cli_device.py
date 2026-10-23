"""Print the current local identity and working-directory context."""
import json
import argparse
import os
from .device import current


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps({'device': current(), 'working_directory': os.getcwd()}, indent=2))


if __name__ == '__main__':
    main()
