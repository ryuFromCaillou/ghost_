"""One-shot legacy context extraction; leaves the original registry untouched."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

if __package__:
    from .strategic_context import StrategicContext, StrategicContextError, _object, load_strategic_context
    from .paths import STATE_ROOT, STRATEGIC_CONTEXT_PATH
else:
    from strategic_context import StrategicContext, StrategicContextError, _object, load_strategic_context
    from paths import STATE_ROOT, STRATEGIC_CONTEXT_PATH


def migrate_context(source=STATE_ROOT / 'goals.json', destination=STRATEGIC_CONTEXT_PATH):
    """Extract the sole linked WHY/DIRECTION; never overwrite either file.

    An identical destination makes reruns harmless; ambiguity or conflict fails.
    The original source path is the deterministic legacy archive.
    """
    temporary = None
    try:
        source, destination = Path(source), Path(destination)
        payload = json.loads(source.read_text(encoding='utf-8'), object_pairs_hook=_object)
        whys, directions = payload['whys'], payload['directions']
        if len(whys) != 1 or len(directions) != 1 or directions[0]['why_id'] != whys[0]['id']:
            raise ValueError('Ambiguous strategic context')
        context = StrategicContext(whys[0]['title'], directions[0]['title'])
        if destination.exists():
            if load_strategic_context(destination) != context:
                raise ValueError('Destination conflicts with legacy context')
            return context
        serialized = json.dumps(asdict(context), ensure_ascii=False, indent=2) + '\n'
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix='.strategic-context-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        # Publish complete bytes atomically, refusing a concurrently created destination.
        os.link(temporary, destination)
        return context
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        raise StrategicContextError('Context migration failed; source registry preserved') from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=STATE_ROOT / 'goals.json')
    parser.add_argument('--destination', type=Path, default=STRATEGIC_CONTEXT_PATH)
    args = parser.parse_args()
    try:
        migrate_context(args.source, args.destination)
    except StrategicContextError as exc:
        parser.exit(1, f'GHOST ERROR\n{exc}\n')
    print('Strategic context ready; legacy registry preserved at its original path.')


if __name__ == '__main__':
    main()
