"""C11-accelerated multiphysics demo for agent-driven simulations."""

__version__ = "1.0.0"


def build_system(spec):
    """Build a reusable physical system from parameters, without running it."""
    from .systems import build_system as build
    return build(spec)


def run_system(spec, output_dir, *, make_video=True):
    """Build and run a system; return the existing verified run summary."""
    from .runner import simulate
    return simulate(build_system(spec), output_dir, make_video=make_video)


def load_run(path):
    """Read verified full-step observations and sampled body states."""
    from .io.records import load_run as load
    return load(path)


def liquid_preset(name):
    """Return one canonical liquid preset as detached JSON-ready data."""
    from .core.liquids import liquid_preset as resolve
    return resolve(name)
