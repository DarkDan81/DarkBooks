"""Bundle a clean Windows app, local Python runtime and installed Pillow/lxml only."""
from pathlib import Path
import shutil,sys,subprocess,zipfile,time,json
ROOT=Path(__file__).resolve().parents[1]

def build():
    if sys.platform!='win32':raise SystemExit('Build the Windows runtime on Windows')
    staging=ROOT/'.build'/('DarkBooks-'+time.strftime('%Y%m%d-%H%M%S'));staging.mkdir(parents=True)
    shutil.copytree(ROOT/'book_illustrator',staging/'book_illustrator',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ['Start.cmd','launch.py','pyproject.toml','LICENSE','CHANGELOG.md']:
        shutil.copy2(ROOT/name,staging/name)
    shutil.copy2(ROOT/'README.md',staging/'README.md')
    shutil.copytree(ROOT/'docs',staging/'docs')
    runtime=staging/'runtime/python';runtime.mkdir(parents=True)
    python_root=Path(sys.base_prefix)
    for pattern in ('python*.exe','python*.dll','vcruntime*.dll','LICENSE.txt'):
        for path in python_root.glob(pattern):shutil.copy2(path,runtime/path.name)
    shutil.copytree(python_root/'DLLs',runtime/'DLLs',ignore=shutil.ignore_patterns('*.pdb'))
    shutil.copytree(python_root/'Lib',runtime/'Lib',ignore=shutil.ignore_patterns(
        'site-packages','__pycache__','*.pyc','test','tests','idlelib','tkinter','turtledemo','ensurepip'))
    import PIL,lxml
    site=runtime/'Lib/site-packages';site.mkdir()
    for package in (PIL,lxml):
        package_path=Path(package.__path__[0]);shutil.copytree(package_path,site/package_path.name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    installed=Path(PIL.__path__[0]).parent
    for pattern in ('pillow-*.dist-info','lxml-*.dist-info','pillow.libs','lxml.libs'):
        for path in installed.glob(pattern):shutil.copytree(path,site/path.name)
    (runtime/f'python{sys.version_info.major}{sys.version_info.minor}._pth').write_text('Lib\nDLLs\nLib/site-packages\nimport site\n',encoding='utf-8')
    subprocess.run([str(runtime/'python.exe'),'-I',str(staging/'launch.py'),'--self-test'],cwd=staging,check=True)
    forbidden=[p for p in staging.rglob('*') if p.is_file() and (p.suffix.lower() in ('.gguf','.safetensors','.sqlite') or (p.suffix.lower()=='.fb2' and p.relative_to(staging).as_posix()!='book_illustrator/resources/examples/lantern-keeper.fb2') or p.name=='settings.json')]
    if forbidden:raise ValueError('Private files in portable build: '+str(forbidden))
    dist=ROOT/'dist';dist.mkdir(exist_ok=True)
    archive=dist/'DarkBooks-Windows-x64.zip'
    with zipfile.ZipFile(archive.with_suffix('.zip.tmp'),'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for path in staging.rglob('*'):
            if path.is_file():bundle.write(path,Path('DarkBooks')/path.relative_to(staging))
    archive.with_suffix('.zip.tmp').replace(archive)
    manifest={'archive':str(archive),'staging':str(staging),'python':sys.version,'bytes':archive.stat().st_size,
              'private_books_included':False,'original_sample_included':True,'keys_included':False,'model_weights_included':False}
    (dist/'portable-build.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest))

if __name__=='__main__':build()
