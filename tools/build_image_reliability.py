"""Build the small hash-checked App update; never deploys it."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

VERSION='2026.10.24-image.2'
BASE='2026.10.24-layout.1'
FILES=('App/frontend/app.js','App/frontend/index.html','App/frontend/style.css','App/server/server.py','App/server/job_history.py')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--production-root',type=Path,required=True)
    parser.add_argument('--validation',type=Path,required=True)
    parser.add_argument('--package-dir',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    source=Path(__file__).resolve().parents[1]
    if subprocess.check_output(['git','status','--porcelain'],cwd=source,text=True).strip():raise RuntimeError('Commit source changes before packaging')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
    installed_path=args.production_root/'Support/Diagnostics/InstalledRelease.json'
    installed=json.loads(installed_path.read_text(encoding='utf-8-sig'))
    assert installed['release_version']==BASE
    originals={r['path']:r for r in installed['files']}
    validation=json.loads(args.validation.read_text(encoding='utf-8'))
    assert all(c['status']=='PASS' for c in validation['checks'])
    assert validation['gpu']['fixed_seed_verified'] and validation['gpu']['png_matches_prior_gpu_result']
    assert validation['cancel']['status']=='PASS' and validation['offline_history']['backend_unavailable']
    validation['source_commit']=commit
    validation['production_deployed']=False
    package=args.package_dir.resolve()
    package.mkdir(parents=True,exist_ok=True)
    records=[]
    for relative in FILES:
        original=originals.get(relative)
        actual=args.production_root/relative
        if original:
            assert actual.is_file() and digest(actual)==original['sha256'], 'Modified baseline: '+relative
        else:
            assert not actual.exists(), 'Unexpected new module already installed'
        destination=package/'payload'/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source/relative,destination)
        records.append(dict(path=relative,size=destination.stat().st_size,sha256=digest(destination),old_sha256=original['sha256'] if original else None))
    relative='Support/Diagnostics/ImageReliabilityValidation.json'
    assert not (args.production_root/relative).exists()
    destination=package/'payload'/relative
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
    records.append(dict(path=relative,size=destination.stat().st_size,sha256=digest(destination),old_sha256=None))
    manifest=dict(schema_version=1,release_version=VERSION,source_commit=commit,base_release_version=BASE,base_installed_commit=installed['git_commit'],base_installed_manifest_sha256=digest(installed_path),production_deployed=False,files=records)
    (package/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    shutil.copyfile(source/'tools/Update-ImageReliability.ps1',package/'Update-ImageReliability.ps1')
    shutil.copyfile(source/'docs/image-reliability-ru.md',package/'README-ru.md')
    args.out.mkdir(parents=True,exist_ok=True)
    archive=args.out/('VisualForge-'+VERSION+'.zip')
    # An explicit list prevents stale scratch files from leaking into a release.
    include=['manifest.json','Update-ImageReliability.ps1','README-ru.md']+['payload/'+r['path'] for r in records]
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as stream:
        for name in include:stream.write(package/name,name)
    with zipfile.ZipFile(archive) as stream:assert stream.testzip() is None
    artifact=dict(release_version=VERSION,source_commit=commit,archive=archive.name,size_bytes=archive.stat().st_size,sha256=digest(archive),files=records,production_deployed=False)
    (args.out/'release-manifest.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2),encoding='utf-8')
    shutil.copyfile(source/'docs/image-reliability-ru.md',args.out/'Установка-и-откат.md')
    print(json.dumps({k:v for k,v in artifact.items() if k!='files'},ensure_ascii=False))
if __name__=='__main__':main()
