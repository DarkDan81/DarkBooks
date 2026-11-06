from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys
import zlib

from .backends import ComfyBackend, TextBackend
from .core import Book, read_json, write_json, verify_export
from .pipeline import Pipeline

ROOT = Path(__file__).resolve().parent.parent


def test_card(path):
    """Deterministic export fixture, visibly a test card, not an AI illustration."""
    width, height = 640, 360
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            border = x in {8, 9, 630, 631} or y in {8, 9, 350, 351}
            diagonal = abs(x * height - y * width) < 1200 or abs((width-x) * height-y*width) < 1200
            raw.append(30 if border or diagonal else 248)
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name+data) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
    png += chunk(b'tEXt', b'Description\x00EXPORT TEST CARD - NOT GENERATED ART')
    png += chunk(b'IDAT', zlib.compress(bytes(raw))) + chunk(b'IEND', b'')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Local illustrated FB2 pilot')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--config', type=Path, default=Path('configs/pilot.json'))
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ['inspect', 'prepare', 'analyze', 'plan', 'references', 'attach-references', 'status', 'doctor', 'smoke-test']:
        sub.add_parser(name)
    for name in ['render', 'render-references', 'review', 'export']:
        command = sub.add_parser(name)
        command.add_argument('--profile', default='krea-draft')
        if name == 'export':
            command.add_argument('--allow-unreviewed', action='store_true')
    approval = sub.add_parser('approve')
    production = sub.add_parser('run', help='Resumable full chapter pipeline with separate Qwen portraits')
    production.add_argument('--phase', choices=['all','text','images'], default='all')
    complete = sub.add_parser('end-to-end', help='Cold full run with scene-crop references and face variant A')
    complete.add_argument('--delivery',type=Path,required=True)
    approval.add_argument('scene_ids', nargs='+')
    approval.add_argument('--reject', action='store_true')
    approval.add_argument('--profile', default='krea-draft')
    curation = sub.add_parser('curate')
    curation.add_argument('--decisions', type=Path, default=Path('configs/pilot-curation.json'))
    args = parser.parse_args(argv)
    config_path = args.config if args.config.is_absolute() else args.root / args.config
    pipeline = None
    try:
        pipeline = Pipeline(args.root, config_path)
        command = args.command
        if command == 'end-to-end':
            from .end_to_end import EndToEndRunner
            EndToEndRunner(pipeline,args.delivery).run()
            data={'run':str(pipeline.run),'delivery':str(args.delivery)}
        elif command == 'run':
            from .production import ProductionRunner
            ProductionRunner(pipeline).run(args.phase)
            data = {'run':str(pipeline.run),'phase':args.phase}
        elif command == 'inspect':
            data = pipeline.book.inventory()
            data['chapters'] = [{k:v for k,v in c.items() if k != 'paragraphs'}
                                for c in data['chapters']]
        elif command == 'prepare':
            write_json(pipeline.run / 'book.json', pipeline.book.inventory())
            selected = pipeline.selected()
            write_json(pipeline.run / 'pilot-input.json', {'source_sha256': pipeline.book.sha,
                                                          'chapters': selected})
            data = {'run': str(pipeline.run), 'chapters': [c['id'] for c in selected],
                    'paragraphs': sum(len(c['paragraphs']) for c in selected)}
        elif command == 'analyze':
            result = pipeline.analyze()
            data = {'characters': len(result['characters']), 'path': str(pipeline.run / 'analysis.json')}
        elif command == 'plan':
            result = pipeline.plan()
            data = {'scenes': len(result['scenes']), 'path': str(pipeline.run / 'scenes.json')}
        elif command == 'curate':
            path = args.decisions if args.decisions.is_absolute() else pipeline.root / args.decisions
            result = pipeline.curate(path)
            data = {'characters': len(result['characters']), 'path': str(pipeline.run / 'analysis-curated.json')}
        elif command == 'references':
            result = pipeline.references()
            data = {'designs': len(result['designs']), 'path': str(pipeline.run / 'reference-briefs.json')}
        elif command == 'render-references':
            data = pipeline.render_references(args.profile)
        elif command == 'attach-references':
            result = pipeline.attach_references()
            data = {'scenes': len(result['scenes'])}
        elif command == 'render':
            result = pipeline.render(args.profile)
            data = {'images': len(result)}
        elif command == 'export':
            data = pipeline.export(args.profile, args.allow_unreviewed)
        elif command == 'review':
            data = {'path': pipeline.review(args.profile)}
        elif command == 'approve':
            file = pipeline.run / 'scenes.json'
            plan = read_json(file)
            ids = {s['id'] for s in plan['scenes']}
            requested = set(args.scene_ids)
            if 'all' in requested:
                requested = ids
            if requested - ids:
                raise ValueError('Unknown scene IDs')
            render_file = pipeline.run / f'renders-{args.profile}.json'
            rendered = read_json(render_file) if render_file.exists() else {'images': []}
            hashes = {item['scene_id']: item['image_sha256'] for item in rendered['images']}
            for scene in plan['scenes']:
                if scene['id'] in requested:
                    if not args.reject and scene['id'] not in hashes:
                        raise ValueError('Cannot approve an image that has not been rendered')
                    scene['approved'] = not args.reject
                    scene['approved_image_sha256'] = hashes.get(scene['id']) if not args.reject else None
            write_json(file, plan)
            data = {'updated': sorted(requested), 'approved': not args.reject}
        elif command == 'status':
            data = {'run': str(pipeline.run), 'jobs': pipeline.store.status()}
        elif command == 'doctor':
            data = {'text': {}, 'images': {}, 'downloads': {}}
            try:
                models = TextBackend(pipeline.config['text']).models()
                ids = [m['id'] for m in models.get('data', [])]
                expected = pipeline.config['text']['model']
                data['text'] = {'available_models': ids, 'configured_model': expected,
                                'configured_model_available': expected in ids}
            except Exception as error:
                data['text'] = {'error': str(error)}
            for name, config in pipeline.config['images'].items():
                if 'variants' in config:
                    data['images'][name] = {'variants': config['variants']}
                    continue
                try:
                    backend = ComfyBackend(config, pipeline.root)
                    backend.health()
                    backend.validate_workflow()
                    data['images'][name] = {'ready': True}
                except Exception as error:
                    data['images'][name] = {'ready': False, 'error': str(error)}
            manifest = read_json(pipeline.root / 'configs/download-manifest.json')
            for item in manifest['files']:
                path = Path(item['destination'])
                data['downloads'][path.name] = {'file_exists': path.exists(),
                                                'expected_bytes': item['bytes'],
                                                'actual_bytes': path.stat().st_size if path.exists() else 0}
            write_json(pipeline.run / 'doctor.json', data)
        elif command == 'smoke-test':
            card = pipeline.run / 'fixtures/export-test-card.png'
            test_card(card)
            entries = [{'path': str(card), 'after_paragraph_id': chapter['paragraphs'][-1]['id']}
                       for chapter in pipeline.selected()]
            output = pipeline.root / 'outputs/export-smoke-test-NOT-ILLUSTRATED.fb2'
            data = pipeline.book.export(entries, output)
            data['path'] = str(output)
            write_json(output.with_suffix('.verification.json'), data)
        else:
            raise ValueError('Unknown command')
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TimeoutError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    finally:
        if pipeline:
            pipeline.store.close()
