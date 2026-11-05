"""Package installed Linux wheels with uv; never ship native host binaries."""
from pathlib import Path
import shutil
import subprocess

import aws_cdk as cdk
from aws_cdk import aws_lambda as lambda_
import jsii


@jsii.implements(cdk.ILocalBundling)
class LocalPythonBundling:
    def __init__(self, entry):
        self.entry = Path(entry).resolve()

    def try_bundle(self, output_dir, options):
        output = Path(output_dir)
        subprocess.run([
            'uv', 'pip', 'install', '--target', str(output), '--python-version', '3.11',
            '--python-platform', 'x86_64-manylinux2014', '--only-binary', ':all:',
            '--link-mode', 'copy', '--no-compile-bytecode',
            '--requirements', str(self.entry / 'requirements.txt'),
        ], check=True)
        shutil.copytree(self.entry, output, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', 'test_*.py', '*.pyc'))
        return True


def python_asset(entry):
    return lambda_.Code.from_asset(str(entry), asset_hash_type=cdk.AssetHashType.OUTPUT,
        bundling=cdk.BundlingOptions(
            # CDK requires an image declaration; the local bundler must succeed.
            image=cdk.DockerImage.from_registry('public.ecr.aws/sam/build-python3.11'),
            local=LocalPythonBundling(entry)))
