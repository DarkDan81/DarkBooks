from pathlib import Path

from .core import fingerprint


def finish_grayscale(source, destination):
    """Deterministic technical finishing; preserve the unmodified model output."""
    from PIL import Image, ImageOps
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as raw:
        image = ImageOps.exif_transpose(raw)
        if image.mode in {'RGBA', 'LA'}:
            rgba = image.convert('RGBA')
            background = Image.new('RGBA', rgba.size, 'white')
            background.alpha_composite(rgba)
            image = background.convert('RGB')
        image = image.convert('L')
        image.save(destination, format='PNG', optimize=True)
        size = list(image.size)
    return {'path': str(destination.resolve()), 'sha256': fingerprint(destination.read_bytes()),
            'source_sha256': fingerprint(source.read_bytes()), 'mode': 'L', 'size': size}


def xsd_report(book_path, schema_path):
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    schema = etree.XMLSchema(etree.parse(str(schema_path), parser))
    valid = schema.validate(etree.parse(str(book_path), parser))
    return {'valid': valid, 'errors': [str(error) for error in schema.error_log]}
