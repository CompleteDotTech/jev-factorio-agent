"""Native successor capability; all writes delegate to existing fair adapters."""
from importlib.resources import files


class SuccessorFactory:
    def __init__(self, native):
        self.native = native
        if getattr(native.backend, '_native_attachment', None) is not None:
            from .native_attachment import require_asset
            require_asset(native.backend._native_attachment, 'successors')
        else:
            native.command('do\n' + files('jev_factorio').joinpath('lua/successors.lua').read_text() + '\nend')

    def __getattr__(self, name):
        return getattr(self.native, name)
