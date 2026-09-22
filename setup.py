"""Copy the single source of example assets into built distributions."""
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithExamples(build_py):
    def run(self):
        super().run()
        source = Path(__file__).resolve().parent / "examples"
        target = Path(self.build_lib) / "physics_demo" / "data" / "examples"
        target.mkdir(parents=True, exist_ok=True)
        for pattern in ("*.json", "*.png"):
            for asset in sorted(source.glob(pattern)):
                self.copy_file(str(asset), str(target / asset.name))


setup(cmdclass={"build_py": BuildWithExamples})
