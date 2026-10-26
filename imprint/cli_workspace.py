"""Workspace roots, exclusions, preview-first source cleanup and, for the
current directory, which workspace it belongs to (ADR 0072)."""
import argparse
import json
import os
from . import workspace_policy as policy


def directory_workspace(args):
    if args.action == 'resolve':
        # The workspace a command acting on this directory uses, and why.
        resolution = policy.resolve(args.target or os.getcwd())
        return {**resolution, 'why': policy.describe(resolution)}
    if not args.target:
        raise ValueError('use requires a workspace name')
    # In a project, its folder; elsewhere the folder the user ran this in.
    here = policy.canonical(os.getcwd())
    return policy.use_workspace(args.target, policy.place(here).project or here,
                                allow_overlap=args.allow_overlap)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['roots', 'add', 'remove', 'exclusions', 'restore', 'purge', 'map',
                                           'resolve', 'use'])
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
    if args.action in ('resolve', 'use'):
        try:
            print(json.dumps(directory_workspace(args), indent=2))
        except ValueError as error:
            parser.exit(1, f'{error}\n')
        return
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
