"""Deterministic evidence excerpts; character budgets are not token estimates."""
import re


def excerpt(content: str, query: str, width: int) -> tuple[str, int, bool]:
    if len(content) <= width:
        return content, 0, False
    terms = sorted(set(re.findall(r'[\w.-]{2,}', query.lower())), key=lambda term: (-len(term), term))[:32]
    matches = list(re.finditer('|'.join(re.escape(term) for term in terms), content, re.IGNORECASE))[:256] if terms else []
    candidates = {0} | {min(max(0, match.start() - width // 3), len(content) - width) for match in matches}

    def score(start):
        window = [m.group().lower() for m in matches if start <= m.start() < start + width]
        return len(set(window)), len(window), -start

    start = max(candidates, key=score)
    return content[start:start + width], start, True


def compact_search(results, query, workspace, offset, limit, max_chars, threshold):
    """Emit whole result blocks and a continuation that never skips hidden hits."""
    header = f'Workspace: {workspace}. Search evidence; verify live sources before edits.\n'
    blocks = []
    used, advanced = len(header), 0
    more = len(results) >= limit
    for index, result in enumerate(results):
        if result['similarity'] < threshold and not result.get('lexical_score', 0):
            advanced = index + 1
            continue
        rid = result.get('id', '')
        origin = result.get('recorded_device') or {}
        path = result.get('path_context') or {}
        meta = [f"[{offset + index + 1}] id:{rid} sim:{result['similarity']:.3f}"]
        for key in ('project', 'type', 'source'):
            if result.get(key):
                meta.append(f'{key}:{str(result[key])[:256]}')
        if result.get('source_hash'):
            meta.append('sha256:' + result['source_hash'][:12])
        meta.append('recorded on: ' + str(origin.get('name') or 'unknown origin')[:128]
                    + (f" [{origin['id']}]" if origin.get('id') else ''))
        if path.get('original_source_path'):
            meta.append('original path (reference): ' + str(path['original_source_path'])[:256])
            meta.append('path status: ' + path.get('path_status', 'unknown'))
        if path.get('local_source_path'):
            meta.append('local path (verify): ' + str(path['local_source_path'])[:256])
        content = result.get('content', '')
        width = min(900 if not blocks else 300, max_chars - used - len(' | '.join(meta)) - 300)
        if width < 100:
            if blocks:
                more = True
                break
            meta = [f'[{offset + index + 1}] id:{rid}',
                    'source (reference):' + str(result.get('source', ''))[:120],
                    'recorded on: ' + str(origin.get('name') or 'unknown origin')[:64],
                    'path status:' + str(path.get('path_status', 'unknown'))[:64]]
            if path.get('local_source_path'):
                meta.append('local path (verify):' + str(path['local_source_path'])[:120])
            width = 100
        text, start, truncated = excerpt(content, query, width)
        block = ' | '.join(meta) + '\n' + text
        if truncated:
            block += f'\n[excerpt chars {start}:{start + len(text)} of {len(content)}; expand with get_memory("{rid}")]'
        block += '\n\n'
        if used + len(block) > max_chars - 160:
            more = True
            break
        blocks.append(block)
        used += len(block)
        advanced = index + 1
    if not blocks and not more:
        return header + 'No relevant matches. Read the relevant files directly if needed.'
    footer = f'More results may exist: offset={offset + advanced}; use filters or get_memory for selected evidence.' if more else ''
    return header + ''.join(blocks) + footer
