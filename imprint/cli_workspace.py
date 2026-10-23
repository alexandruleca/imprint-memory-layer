"""Workspace roots, exclusions and preview-first source cleanup."""
import argparse
import json
from . import workspace_policy as policy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['roots', 'add', 'remove', 'exclusions', 'restore', 'purge', 'map'])
    parser.add_argument('target', nargs='?')
    parser.add_argument('directory', nargs='?')
    parser.add_argument('--workspace')
    parser.add_argument('--allow-overlap', action='store_true', help='Explicitly confirm shared roots')
    parser.add_argument('--source', action='store_true', help='Match logical source instead of physical path')
    parser.add_argument('--recursive', action='store_true')
    parser.add_argument('--apply', action='store_true', help='Delete matching indexed chunks and exclude re-ingestion')
    args = parser.parse_args()
    if args.action != 'map' and args.directory is not None:
        parser.error('Only map accepts a second positional argument')
    ws = policy.workspace_name(args.workspace)
    if args.action not in ('roots', 'exclusions') and args.target is None:
        parser.error('target is required')
    if args.action == 'map':
        if not args.directory:
            parser.error('map requires a project name and local directory')
        result = policy.map_project(args.target, args.directory, ws)
    elif args.action == 'roots':
        result = {'workspace': ws, 'roots': policy.roots(ws)}
    elif args.action == 'add':
        result = policy.add_root(args.target, ws, allow_overlap=args.allow_overlap)
    elif args.action == 'remove':
        result = policy.remove_root(args.target, ws)
    elif args.action == 'exclusions':
        result = {'workspace': ws, 'exclusions': policy.exclusions(ws)}
    elif args.action == 'restore':
        policy.restore(args.target, 'source' if args.source else 'path', ws)
        result = {'workspace': ws, 'exclusions': policy.exclusions(ws)}
    else:
        from .purge import purge
        result = purge(args.target, workspace=ws, kind='source' if args.source else 'path',
                       recursive=args.recursive, apply=args.apply)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
